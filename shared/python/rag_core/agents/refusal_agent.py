"""Rerun the configured main model for a bounded, short refusal."""

from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from rag_core.core.config import get_settings
from rag_core.core.gate_policy import SHORT_REFUSAL_PROMPTS
from rag_core.core.runtime_config import RuntimeConfig


class ShortRefusal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reply: str = Field(min_length=1, max_length=300)


class RefusalAgent:
    async def run(
        self,
        *,
        reason_code: Literal["technical_implementation", "out_of_scope"],
        runtime_config: RuntimeConfig,
    ) -> str:
        settings = get_settings()
        async with AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            timeout=runtime_config.gate_timeout_seconds,
            max_retries=0,
        ) as client:
            response = await client.chat.completions.create(
                model=runtime_config.openrouter_model,
                temperature=0,
                max_tokens=180,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你只產生簡短婉拒文案，不能回答原問題、不能給程式碼、"
                            "指令、操作步驟、網址或引述使用者內容。只輸出指定 JSON。"
                        ),
                    },
                    {"role": "user", "content": SHORT_REFUSAL_PROMPTS[reason_code]},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "short_refusal",
                        "strict": True,
                        "schema": ShortRefusal.model_json_schema(),
                    },
                },
            )
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise ValueError("Main model did not return a complete refusal")
        reply = ShortRefusal.model_validate_json(choice.message.content or "").reply.strip()
        if not reply or "```" in reply or "\n" in reply:
            raise ValueError("Main model refusal is not a short sentence")
        return reply
