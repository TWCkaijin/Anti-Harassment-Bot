"""Text-only, tool-free review calls on a request-scoped OpenRouter transport."""

import json
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, model_validator

from rag_core.core.config import get_settings
from rag_core.core.runtime_config import RuntimeConfig


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["allow", "deny"]
    reason_code: Literal["allowed", "technical_implementation", "out_of_scope"]

    @model_validator(mode="after")
    def consistent_decision(self):
        if (self.decision == "allow") != (self.reason_code == "allowed"):
            raise ValueError("Inconsistent Gate decision")
        return self


class GateAgent:
    async def run(
        self,
        *,
        stage: Literal["pre", "post"],
        user_message: str,
        history: list[dict[str, str]],
        case_context: dict,
        runtime_config: RuntimeConfig,
        candidate: dict | None = None,
    ) -> GateDecision:
        if stage == "post" and candidate is None:
            raise ValueError("Post review requires a candidate")
        if stage == "pre" and candidate is not None:
            raise ValueError("Pre review cannot receive a candidate")
        settings = get_settings()
        content = (
            {
                "stage": "pre",
                "current_message": user_message,
                "history": history,
                "case_context": case_context,
            }
            if stage == "pre"
            else {"stage": "post", "candidate": candidate}
        )
        async with AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            timeout=runtime_config.gate_timeout_seconds,
            max_retries=0,
        ) as client:
            response = await client.chat.completions.create(
                model=runtime_config.gate_model,
                temperature=0,
                max_tokens=128,
                messages=[
                    {"role": "system", "content": runtime_config.gate_prompt_for_stage(stage)},
                    {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "service_gate_decision",
                        "strict": True,
                        "schema": GateDecision.model_json_schema(),
                    },
                },
            )
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise ValueError("Gate did not return a complete decision")
        return GateDecision.model_validate_json(choice.message.content or "")
