"""OpenRouter entry point for chat contract v4 and admin applicable-law prediction."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from openai import AsyncOpenAI

from rag_core.core.agent_prompts import (
    get_default_prompt_sections as get_default_prompt_sections,
)
from rag_core.core.config import get_settings
from rag_core.core.logger import get_logger
from rag_core.core.runtime_config import RuntimeConfig
from rag_core.rag.firestore_vector import FirestoreVectorRAG

logger = get_logger(__name__)
settings = get_settings()

if TYPE_CHECKING:
    from rag_core.agents.guided_v4 import ApplicableLawPrediction


class AgentContractError(ValueError):
    """The model returned a response or tool call that violates the agent contract."""

    def __init__(self, message, *, reason_code=None, stage=None):
        super().__init__(message)
        self.reason_code = reason_code
        self.stage = stage

    @property
    def safe_diagnostics(self):
        # Never expose exception text, model fields, or arbitrary provider codes.
        reasons = {
            "plan_wrapper",
            "json_structure",
            "schema_validation",
            "answer_validation",
            "provider_protocol",
            "route_validation",
            "value_validation",
        }
        if (
            not isinstance(self.reason_code, str)
            or not isinstance(self.stage, str)
            or self.reason_code not in reasons
            or self.stage not in {"understanding", "answer"}
        ):
            return {}
        return {"contract_reason": self.reason_code, "contract_stage": self.stage}


@dataclass(frozen=True)
class AgentResult:
    """OpenRouter Agent 的對外結果。"""

    reply: str
    rag_used: bool = False
    sources: list[dict[str, Any]] = field(default_factory=list)
    available_actions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    guidance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplicableLawPredictionResult:
    """Admin-only prediction, deliberately independent from the chat response."""

    prediction: "ApplicableLawPrediction"


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
        on_reply_delta: Callable[[str], Awaitable[None]] | None = None,
        on_guidance: Callable[[dict], Awaitable[None]] | None = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
        contract_version: int = 4,
        case_context: dict | None = None,
        clarification_answer: dict | None = None,
        clarification_constraints: dict | None = None,
        runtime_config: RuntimeConfig | None = None,
        diagnostics: dict | None = None,
        on_analysis: Callable[[dict], Awaitable[None]] | None = None,
        on_reasoning: Callable[[dict], Awaitable[None]] | None = None,
        regenerate_from_summary: bool = False,
        output_contract: Literal["chat", "applicable_laws_v1"] = "chat",
        expected_applicable_laws_contract_sha256: str | None = None,
        expected_chat_test_contract_sha256: str | None = None,
    ) -> AgentResult | ApplicableLawPredictionResult:
        if contract_version != 4:
            raise AgentContractError("Only chat contract version 4 is supported")
        if output_contract not in {"chat", "applicable_laws_v1"}:
            raise AgentContractError("Unsupported output contract")
        if (
            expected_applicable_laws_contract_sha256 is not None
            and output_contract != "applicable_laws_v1"
        ):
            raise AgentContractError("Applicable-law fingerprint requires prediction mode")
        if expected_chat_test_contract_sha256 is not None and output_contract != "chat":
            raise AgentContractError("Chat-test fingerprint requires v4 chat")
        if output_contract == "applicable_laws_v1" and any(
            callback is not None for callback in (on_reply_delta, on_guidance, on_reasoning)
        ):
            raise AgentContractError("Applicable-law prediction requires non-streaming v4")
        from rag_core.agents.guided_v4 import run_guided_v4

        arguments = dict(
            user_message=user_message,
            history=history,
            image_base64=image_base64,
            case_context=case_context,
            clarification_answer=clarification_answer,
            clarification_constraints=clarification_constraints,
            on_reply_delta=on_reply_delta,
            on_guidance=on_guidance,
            on_progress=on_progress,
            use_rag=use_rag,
            contract_version=contract_version,
            runtime_config=runtime_config,
            diagnostics=diagnostics,
            on_analysis=on_analysis,
            on_reasoning=on_reasoning,
            regenerate_from_summary=regenerate_from_summary,
            output_contract=output_contract,
            expected_chat_test_contract_sha256=expected_chat_test_contract_sha256,
            expected_applicable_laws_contract_sha256=expected_applicable_laws_contract_sha256,
        )
        if isinstance(self.client, AsyncOpenAI):
            async with AsyncOpenAI(
                base_url=settings.openrouter_base_url,
                api_key=settings.openrouter_api_key,
                timeout=settings.openrouter_request_timeout_seconds,
                **(
                    {"max_retries": 0}
                    if output_contract == "applicable_laws_v1"
                    or expected_chat_test_contract_sha256 is not None
                    else {}
                ),
            ) as client:
                return await run_guided_v4(self.rag, client, **arguments)
        return await run_guided_v4(self.rag, self.client, **arguments)
