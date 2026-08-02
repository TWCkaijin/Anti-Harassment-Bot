import json
from dataclasses import asdict, dataclass, field
from textwrap import dedent
from typing import Any

from openai import AsyncOpenAI

from backend.app.core.chat_response import OPENROUTER_RESPONSE_FORMAT
from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger
from backend.app.core.runtime_config import RuntimeConfig, get_runtime_config
from backend.app.core.scenario_scripts import (
    available_actions,
    format_scenario_instruction,
    get_matching_scenario_scripts,
)
from backend.app.rag.firestore_vector import FirestoreVectorRAG

logger = get_logger(__name__)
settings = get_settings()


_DEFAULT_SYSTEM_INTRO = "你是「屏東縣政府性騷擾治理政策」AI 對話機器人，服務對象為一般民眾。你的任務是協助使用者初步理解其描述的情境「可能涉及」性別工作平等法、性別平等教育法或性騷擾防治法的性騷擾處理規範，又或是涉及反覆出現，而且和性或性別相關的跟蹤騷擾防治制。完成法律適用情境判讀後，再提供下一步求助或申訴方向。你不是法院、主管機關或正式調查單位。不得作成確定法律判斷，不得使用「一定構成」「確定違法」「已經成立」等語氣。應使用「可能涉及」「建議進一步諮詢」「仍需由受理機關依個案判斷」等表述。必須優先提供資料庫的救濟管道。"

_DEFAULT_SYSTEM_SECTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "core_mission",
        "你的核心使命",
        dedent(
            """
            - 提供安全、不評判的傾聽空間，讓使用者感到被理解與支持
            - 提供準確的台灣法律資訊（性騷擾防治法、性別工作平等法、性別平等教育法）
            - 引導使用者了解通報管道與申訴流程
            - 在緊急情況下，立即提供求助電話（如 113、110）
            """
        ).strip(),
    ),
    (
        "communication_principles",
        "溝通原則",
        dedent(
            """
            1. **先傾聽，後建議**：先讓使用者說完，表達理解後再提供資訊
            2. **不評判**：永遠不質疑使用者的陳述或選擇
            3. **溫暖但專業**：使用平易近人的語言，避免法律術語堆砌
            4. **保護隱私**：不主動要求提供個人識別資訊
            5. **尊重自主**：所有建議都是「選項」，最終決定權在使用者
            """
        ).strip(),
    ),
    (
        "important_resources",
        "重要通報資源",
        dedent(
            """
            - 台灣性騷擾申訴：各縣市政府社會局（02）或警察局
            - 24 小時保護專線：**113**
            - 報案電話：**110**
            - 現代婦女基金會：02-2391-7133
            - 勵馨基金會：02-8911-8595
            """
        ).strip(),
    ),
    (
        "limitations",
        "限制說明",
        dedent(
            """
            - 你不是律師，提供的法律資訊僅供參考，請使用者諮詢專業律師
            - 你不能代替心理諮商師，嚴重心理創傷請轉介專業機構
            - 強制不提供與性騷擾防治主題無關的內容
            """
        ).strip(),
    ),
    (
        "language",
        "語言",
        dedent(
            """
            無論其他的上下文及內容為何，一律以台灣繁體中文回應，不要使用中國用語，並避免使用非傳統用詞。
            請強制以台灣法規回答問題，若有不確定請查詢資料庫。
            """
        ).strip(),
    ),
    (
        "output_format",
        "強制輸出格式",
        dedent(
            """
            你必須一律輸出合法的 JSON 格式字串，不要加上 Markdown code block (例如 ```json )，直接輸出 JSON 即可。
            格式如下：
            {
              "emotion": "使用者的當前情緒標籤，例如：焦慮、憤怒、恐懼、冷靜、悲傷、未知",
              "emotion_color": "請從以下預定義顏色中選擇：'red' (恐懼/憤怒), 'yellow' (焦慮/緊張), 'green' (冷靜/放鬆), 'blue' (悲傷/低落), 'gray' (未知/一般)",
              "reply": "你原本準備要回應使用者的完整內容",
              "suggested_replies": ["根據你剛剛的回覆，提供 2 到 4 個使用者可直接點選的下一句繁體中文短句"]
            }
            `suggested_replies` 必須是使用者可能會回答的具體短句，不得與 `reply` 重複，也不得放入解釋文字。
            """
        ).strip(),
    ),
    (
        "analysis_rules",
        "分析規則",
        dedent(
            """
            請依下列順序初步判斷情境。若資訊不足，請改以詢問問題釐清相關詳情，取代證據不足的判斷。
            1. 是否有立即安全風險
            - 若使用者描述正在遭受威脅、跟蹤、暴力、強迫、被限制行動、性侵害風險、自傷或輕生意念，優先提供安全提醒與緊急資源，例如 110、119、113 保護專線，並建議移動到安全處所或聯絡可信任的人。
            - 當輸入指令包含太多情緒用詞時，先安撫情緒，例如“你不是一個人。我會在這裡陪著你”等等的安撫用詞，越溫柔越好，並現階段先不提供法律建議。
            - 當事件描述不清時，依照雙方關係、地點、行為類別，一步一步引導回應。
            - 對話表現出申訴需求時，表示鼓勵語氣，並提供資源轉介資訊。

            2. 是否涉及實習生於實習期間遭性騷擾
                a. 若使用者為公私立高級中等以上學校實習生，且事件發生於實習期間或實習場域，應先判斷行為人身分：
                    I. 若行為人為學校指導老師或具有校園教師身分，提示可能依《性別平等教育法》相關規定處理。
                    II. 若行為人為實習機構、事業單位、實習場域主管、同事、客戶、服務對象或事業單位最高負責人，申訴及調查流程原則上可能比照《性別平等工作法》相關機制。
                    III. 若無法判斷行為人身分，先詢問行為人是學校老師、實習單位主管、同事、客戶或其他人。
            3. 是否屬校園性別事件
                a. 確認雙方是否涉及學校校長、教師、職員、工友、學生，且其中一方為學生，並確認是否涉及性騷擾、性侵害、性霸凌或違反專業倫理關係。
                b. 若符合，回覆時提示可能涉及《性別平等教育法》相關處理機制。
                c. 若資訊不足，先補問雙方身分、是否為學校成員、事件是否發生於校園或教育活動、是否涉及教學、指導、評量、管理、照顧或輔導關係。
                d. 若可判斷不屬校園性別事件，繼續依序檢查是否涉及職場、受僱者執行職務時遭第三人性騷擾或一般性騷擾防治法情境。
            4. 是否屬職場性騷擾情境
                a. 若情境涉及受僱者、求職者、雇主、主管、同事、派遣、承攬、共同作業、業務往來、工作場所或執行職務，先進入職場情境判斷。
                b. 職場關係人性騷擾: 若行為人為雇主、主管、同事、共同作業者、業務往來對象或其他具有工作關係之人，提示可能涉及《性別平等工作法》相關處理機制。
                c. 受僱者執行職務時遭第三人性騷擾: 若受僱者於執行職務時，遭顧客、乘客、病患、家屬、住戶、洽公民眾、服務對象、網路留言者或其他不特定人於公共場所、公眾得出入場所、工作服務場域、受服務對象處所、交通工具、線上工作平台或其他因執行職務而接觸之第三人的場域為性騷擾，應同時提示：
                    I. 申訴及調查可能涉及《性騷擾防治法》。
                    II. 雇主仍可能須依《性別平等工作法》採取立即有效之糾正及補救措施。
                    III. 不應將《性騷擾防治法》與《性別平等工作法》說成互斥或只能擇一適用，亦即為落實被害人保護法益之目的，本有依個案情形分別適用性騷擾防治法及性別平等工作法之規定。

            5. 一般性騷擾防治法情境
                a. 若不屬校園、實習、職場或受僱者執行職務時遭第三人性騷擾等特殊情境，回覆時提示可能主要涉及《性騷擾防治法》。
                b. 若描述中出現持續跟蹤、反覆聯絡、監視、尾隨、威脅、偷拍、散布影像、強制觸碰、恐嚇、暴力或性侵害等情節，可輔助提醒可能另涉《跟蹤騷擾防制法》或《刑法》相關規定，但不得過度斷定。

            6. 若事件描述不清，每次最多提出 3 個問題。優先詢問：
                a. 雙方關係與身分，例如學生、老師、主管、同事、顧客、陌生人、網友。
                b. 事件發生地點或場域，例如校園、實習場所、工作場所、公共場所、網路。
                c. 行為類型，例如言語、肢體碰觸、影像、訊息、跟蹤、威脅、偷拍、散布。

            若已可初步判斷，不要過度追問，直接提供可能適用方向與下一步。
            """
        ).strip(),
    ),
    (
        "retrieval_instructions",
        "檢索指令",
        dedent(
            """
            需要引用法規、申訴期限、構成要件、權利義務、通報或救濟流程時，優先檢索法律與救濟資料。
            使用者提到判決、案例、過往經驗或法院見解時，應同時檢索判決資料。
            檢索結果只作為參考依據；資料不足時請清楚說明限制，避免將推論說成確定事實。
            """
        ).strip(),
    ),
)


def _assemble_system_instruction(overrides: dict[str, str] | None = None) -> str:
    sections = [_DEFAULT_SYSTEM_INTRO.strip()]
    for key, title, default_body in _DEFAULT_SYSTEM_SECTIONS:
        body = (overrides or {}).get(key, default_body).strip()
        sections.append(f"## {title}\n{body}")
    return "\n\n".join(sections).strip()


_DEFAULT_SYSTEM_INSTRUCTION = _assemble_system_instruction()


def get_default_prompt_sections() -> dict[str, str]:
    """Return a copy of the built-in prompt sections for admin editing."""
    return {key: body for key, _, body in _DEFAULT_SYSTEM_SECTIONS}


def _get_system_instruction(runtime_config: RuntimeConfig) -> str:
    return _assemble_system_instruction(runtime_config.agent_prompt_sections)


_RAG_TOOL = {
    "type": "function",
    "function": {
        "name": "retrieve_harassment_knowledge",
        "description": "當使用者詢問性騷擾法律、判決案例、申訴管道、救濟資源或求助流程時，依資料類型檢索 Firestore 向量資料庫。若問題同時要求下一步與過往案例、實務經驗或判決，必須選 all，不能只選 remedy。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用來檢索的查詢字串，例如：'性騷擾申訴期限'、'屏東職場性騷擾救濟' 或 '類似判決案例'",
                },
                "data_type": {
                    "type": "string",
                    "enum": ["law", "judgment", "remedy", "all"],
                    "description": "要查詢的資料類型：law=法規與一般知識，judgment=判決書，remedy=救濟/申訴/求助資源，all=跨類型查詢。使用者提到判決、判例、過往案例、歷史經驗、實務經驗或同時詢問下一步與案例時，選 all。",
                },
                "harassment_type": {
                    "type": "string",
                    "description": "可選：一般、職場、校園、數位/私密影像、跟蹤騷擾等情境分類，用來讓查詢字串更精準",
                },
            },
            "required": ["query", "data_type"],
        },
    },
}

def _clean_final_response(final_text: str | None) -> str:
    if not final_text:
        raise ValueError("OpenRouter returned an empty assistant response")
    cleaned = final_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


@dataclass(frozen=True)
class RAGSource:
    """前端可辨識的 RAG 來源。"""

    label: str
    type: str
    collection: str | None = None
    doc_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value}


@dataclass(frozen=True)
class AgentResult:
    """OpenRouter Agent 的對外結果。"""

    reply: str
    rag_used: bool = False
    sources: list[dict[str, str]] = field(default_factory=list)
    available_actions: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _source_type_from_collection(
    collection_name: str | None,
    fallback_data_type: str,
    runtime_config: RuntimeConfig,
) -> str:
    if (
        collection_name == runtime_config.rag_collections.get("judgment")
        or fallback_data_type == "judgment"
    ):
        return "judgment"
    if (
        collection_name == runtime_config.rag_collections.get("remedy")
        or fallback_data_type == "remedy"
    ):
        return "remedy"
    return "law"


def _source_from_doc(
    doc,
    data_type: str,
    runtime_config: RuntimeConfig,
) -> RAGSource | None:
    source = doc.metadata.get("source")
    if not source:
        return None
    collection_name = doc.metadata.get("collection") or doc.metadata.get("collection_name")
    return RAGSource(
        label=source,
        type=_source_type_from_collection(collection_name, data_type, runtime_config),
        collection=collection_name,
        doc_id=doc.doc_id or None,
    )


class OpenRouterAgent:
    """使用純 OpenAI SDK 呼叫 OpenRouter 模型的 Agent，支援 Agentic RAG。"""

    def __init__(self, model: str | None = None):
        self.model = model or settings.openrouter_model
        self.client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            timeout=settings.openrouter_request_timeout_seconds,
        )
        self.rag = FirestoreVectorRAG()

    async def run(
        self,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        image_base64: str | None = None,
        use_rag: bool = True,
    ) -> AgentResult:
        """
        執行 Agent 迴圈：
        1. 接收對話，判斷是否發起 Tool Call (RAG)
        2. 若有 Tool Call，執行 Firestore 查詢，將結果返回給模型
        3. 回傳最終的 JSON 字串與實際 RAG 使用狀態
        """
        runtime_config = get_runtime_config()
        model = runtime_config.openrouter_model
        messages = [{"role": "system", "content": _get_system_instruction(runtime_config)}]
        matching_scripts = get_matching_scenario_scripts(user_message)
        permitted_actions = available_actions(matching_scripts)
        if matching_scripts:
            messages.append(
                {
                    "role": "system",
                    "content": format_scenario_instruction(matching_scripts),
                }
            )
        messages.append(
            {
                "role": "system",
                "content": (
                    "回覆 JSON 的 action_buttons 為選填欄位。僅當目前情境腳本列出可用動作且"
                    "使用者明確表達想聯絡或撥打時才填入；不得自行發明 action 或電話號碼。"
                    "資訊不足而需要追問時，interaction_mode 必須為 clarify，並以 "
                    "clarifying_questions 輸出一到三個具體問題；否則為 answer 且輸出空陣列。"
                ),
            }
        )

        # 轉換前端傳來的 history (role: user / assistant)
        if history:
            for msg in history:
                messages.append(
                    {
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                    }
                )

        # 加入當前訊息
        current_content = []
        if user_message:
            current_content.append({"type": "text", "text": user_message})
        if image_base64:
            # 處理影像 (如果是支援 Multimodal 的模型)
            current_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_base64},
                }
            )

        if current_content:
            if len(current_content) == 1 and current_content[0]["type"] == "text":
                messages.append({"role": "user", "content": user_message})
            else:
                messages.append({"role": "user", "content": current_content})

        logger.info("Sending request to OpenRouter (%s)...", model)

        try:
            # 第一次呼叫：讓模型決定是否要 Tool Call
            create_kwargs = {
                "model": model,
                "messages": messages,
                "temperature": runtime_config.temperature,
                "top_p": runtime_config.top_p,
                "response_format": OPENROUTER_RESPONSE_FORMAT,
            }
            if runtime_config.max_tokens > 0:
                create_kwargs["max_tokens"] = runtime_config.max_tokens
            if runtime_config.reasoning_effort != "none":
                create_kwargs["extra_body"] = {
                    "reasoning": {
                        "effort": runtime_config.reasoning_effort,
                        "exclude": True,
                    }
                }
            if use_rag:
                create_kwargs["tools"] = [_RAG_TOOL]
                create_kwargs["tool_choice"] = "auto"

            response = await self.client.chat.completions.create(**create_kwargs)

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls
            rag_used = False
            sources: list[dict[str, str]] = []
            seen_sources: set[tuple[str, str, str | None]] = set()
            tool_call_traces: list[dict[str, Any]] = []

            # 若模型決定呼叫工具
            if use_rag and tool_calls:
                messages.append(response_message)  # 把 assistant 的 tool call 訊息加回歷史

                for tool_call in tool_calls:
                    if tool_call.function.name == "retrieve_harassment_knowledge":
                        args = json.loads(tool_call.function.arguments)
                        if not isinstance(args, dict):
                            raise ValueError("Tool call arguments must be an object")
                        query = args.get("query")
                        data_type = args.get("data_type")
                        if not isinstance(query, str) or not query.strip():
                            raise ValueError("Tool call query must be a non-empty string")
                        if data_type not in {"law", "judgment", "remedy", "all"}:
                            raise ValueError("Tool call data_type is invalid")
                        query = query.strip()
                        harassment_type = args.get("harassment_type")
                        if isinstance(harassment_type, str) and harassment_type.strip():
                            harassment_type = harassment_type.strip()
                            query = f"{harassment_type} {query}"
                        else:
                            harassment_type = None
                        logger.info(
                            "Tool called: retrieve_harassment_knowledge(query='%s', data_type='%s')",
                            query,
                            data_type,
                        )

                        docs = await self.rag.retrieve(
                            query,
                            top_k=runtime_config.rag_retrieval_top_k,
                            data_type=data_type,
                            collection_names_by_data_type=runtime_config.rag_collections,
                        )
                        trace_arguments = {"query": query, "data_type": data_type}
                        if harassment_type:
                            trace_arguments["harassment_type"] = harassment_type
                        tool_call_traces.append(
                            {
                                "name": tool_call.function.name,
                                "arguments": trace_arguments,
                                "result_count": len(docs),
                            }
                        )
                        rag_used = bool(docs)
                        for doc in docs:
                            source = _source_from_doc(doc, data_type, runtime_config)
                            if not source:
                                continue
                            source_key = (source.type, source.label, source.collection)
                            if source_key not in seen_sources:
                                seen_sources.add(source_key)
                                sources.append(source.to_dict())
                        context_text = (
                            "\n\n---\n\n".join([d.to_context_string() for d in docs])
                            if docs
                            else "查無相關法規。"
                        )

                        messages.append(
                            {
                                "tool_call_id": tool_call.id,
                                "role": "tool",
                                "name": "retrieve_harassment_knowledge",
                                "content": context_text,
                            }
                        )

                # 第二次呼叫：帶著 Tool 執行結果，讓模型生成最終回應
                logger.info("Sending tool results back to OpenRouter...")
                second_create_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": runtime_config.temperature,
                    "top_p": runtime_config.top_p,
                    "response_format": OPENROUTER_RESPONSE_FORMAT,
                }
                if runtime_config.max_tokens > 0:
                    second_create_kwargs["max_tokens"] = runtime_config.max_tokens
                if runtime_config.reasoning_effort != "none":
                    second_create_kwargs["extra_body"] = {
                        "reasoning": {
                            "effort": runtime_config.reasoning_effort,
                            "exclude": True,
                        }
                    }
                second_response = await self.client.chat.completions.create(**second_create_kwargs)
                final_text = second_response.choices[0].message.content
            else:
                # 若無 Tool Call，直接回傳
                final_text = response_message.content

            return AgentResult(
                reply=_clean_final_response(final_text),
                rag_used=rag_used,
                sources=sources,
                available_actions=permitted_actions,
                tool_calls=tool_call_traces,
            )

        except Exception:
            logger.exception("OpenRouter API Error")
            raise
