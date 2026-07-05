import json
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger
from backend.app.rag.firestore_vector import FirestoreVectorRAG

logger = get_logger(__name__)
settings = get_settings()

_SYSTEM_INSTRUCTION = """
你是「守護者」，一位專門協助性騷擾潛在受害者的 AI 諮詢助理。

## 你的核心使命
- 提供安全、不評判的傾聽空間，讓使用者感到被理解與支持
- 提供準確的台灣法律資訊（性騷擾防治法、性別工作平等法、性別平等教育法）
- 引導使用者了解通報管道與申訴流程
- 在緊急情況下，立即提供求助電話（如 113、110）

## 溝通原則
1. **先傾聽，後建議**：先讓使用者說完，表達理解後再提供資訊
2. **不評判**：永遠不質疑使用者的陳述或選擇
3. **溫暖但專業**：使用平易近人的語言，避免法律術語堆砌
4. **保護隱私**：不主動要求提供個人識別資訊
5. **尊重自主**：所有建議都是「選項」，最終決定權在使用者

## 重要通報資源
- 台灣性騷擾申訴：各縣市政府社會局（02）或警察局
- 24 小時保護專線：**113**
- 報案電話：**110**
- 現代婦女基金會：02-2391-7133
- 勵馨基金會：02-8911-8595

## 限制說明
- 你不是律師，提供的法律資訊僅供參考，請使用者諮詢專業律師
- 你不能代替心理諮商師，嚴重心理創傷請轉介專業機構
- 不提供與性騷擾防治主題無關的內容

## 語言
請使用繁體中文回應。若使用者使用其他語言，可用其語言回應，但法律資訊仍以台灣法規為主。

## 強制輸出格式
你必須一律輸出合法的 JSON 格式字串，不要加上 Markdown code block (例如 ```json )，直接輸出 JSON 即可。
格式如下：
{
  "emotion": "使用者的當前情緒標籤，例如：焦慮、憤怒、恐懼、冷靜、悲傷、未知",
  "emotion_color": "請從以下預定義顏色中選擇：'red' (恐懼/憤怒), 'yellow' (焦慮/緊張), 'green' (冷靜/放鬆), 'blue' (悲傷/低落), 'gray' (未知/一般)",
  "reply": "你原本準備要回應使用者的完整內容"
}
"""

_RAG_TOOL = {
    "type": "function",
    "function": {
        "name": "retrieve_harassment_knowledge",
        "description": "當使用者詢問性騷擾法律、判決案例、申訴管道、救濟資源或求助流程時，依資料類型檢索 Firestore 向量資料庫。",
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
                    "description": "要查詢的資料類型：law=法規與一般知識，judgment=判決書，remedy=救濟/申訴/求助資源，all=不確定時跨類型查詢",
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


@dataclass(frozen=True)
class AgentResult:
    """OpenRouter Agent 的對外結果。"""

    reply: str
    rag_used: bool = False
    sources: list[str] = field(default_factory=list)


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
        messages = [{"role": "system", "content": _SYSTEM_INSTRUCTION}]

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

        logger.info(f"Sending request to OpenRouter ({self.model})...")

        try:
            # 第一次呼叫：讓模型決定是否要 Tool Call
            create_kwargs = {
                "model": self.model,
                "messages": messages,
            }
            if use_rag:
                create_kwargs["tools"] = [_RAG_TOOL]
                create_kwargs["tool_choice"] = "auto"

            response = await self.client.chat.completions.create(**create_kwargs)

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls
            rag_used = False
            sources: list[str] = []

            # 若模型決定呼叫工具
            if use_rag and tool_calls:
                messages.append(response_message)  # 把 assistant 的 tool call 訊息加回歷史

                for tool_call in tool_calls:
                    if tool_call.function.name == "retrieve_harassment_knowledge":
                        args = json.loads(tool_call.function.arguments)
                        query = args.get("query", user_message)
                        data_type = args.get("data_type", "law")
                        harassment_type = args.get("harassment_type")
                        if harassment_type:
                            query = f"{harassment_type} {query}"
                        logger.info(
                            "Tool called: retrieve_harassment_knowledge(query='%s', data_type='%s')",
                            query,
                            data_type,
                        )

                        docs = await self.rag.retrieve(
                            query,
                            top_k=settings.rag_retrieval_top_k,
                            data_type=data_type,
                        )
                        rag_used = bool(docs)
                        for doc in docs:
                            source = doc.metadata.get("source")
                            if source and source not in sources:
                                sources.append(source)
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
                second_response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                )
                final_text = second_response.choices[0].message.content
            else:
                # 若無 Tool Call，直接回傳
                final_text = response_message.content

            # 清理可能的 markdown code block
            if final_text.startswith("```json"):
                final_text = final_text[7:]
            if final_text.endswith("```"):
                final_text = final_text[:-3]

            return AgentResult(reply=final_text.strip(), rag_used=rag_used, sources=sources)

        except Exception as e:
            logger.error(f"OpenRouter API Error: {e}")
            # 發生錯誤時的 fallback JSON
            fallback = {
                "emotion": "未知",
                "emotion_color": "gray",
                "reply": "系統目前無法連線至 AI 引擎，請稍後再試。若有緊急狀況請撥打 113 保護專線。",
            }
            return AgentResult(reply=json.dumps(fallback, ensure_ascii=False))
