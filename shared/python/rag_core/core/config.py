"""
性騷擾防治智能 AI — 後端設定模組
統一管理所有環境變數與應用設定
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve local configuration explicitly; deployed packages never search parents
# for a developer's .env or credentials.
ROOT_DIR = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / ".firebaserc").is_file()),
    Path(__file__).resolve().parents[1],
)
if not os.environ.get("K_SERVICE"):
    load_dotenv(os.environ.get("RAG_ENV_FILE", str(ROOT_DIR / ".env")))

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://anti-harassment-bot.web.app",
    "https://anti-harassment-bot.firebaseapp.com",
)
DEFAULT_CORS_PREVIEW_ORIGIN_REGEXES = (
    r"\Ahttps://anti-harassment-bot--dev-preview-[a-z0-9-]+\.web\.app\Z",
)

# Only allow an anchored Firebase Hosting preview-host shape. This intentionally
# rejects arbitrary administrator-supplied regex features such as ``.*`` or
# alternation, which could silently turn the allowlist into a wildcard.
_SAFE_FIREBASE_PREVIEW_ORIGIN_REGEX = re.compile(
    r"\\Ahttps://[a-z0-9]+(?:-[a-z0-9]+)*--[a-z0-9]+(?:-[a-z0-9]+)*-"
    r"\[a-z0-9-\]\+\\\.web\\\.app\\Z"
)


class Settings(BaseSettings):
    """應用程式設定，優先從 .env 讀取，再從環境變數讀取。"""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── OpenRouter ─────────────────────────────────────────────────────────
    openrouter_api_key: str = Field(default="", description="OpenRouter API Key")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter OpenAI-compatible API base URL",
    )
    openrouter_model: str = Field(
        default="google/gemini-2.5-pro", description="Firestore 未設定時使用的本地預設模型"
    )
    openrouter_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="模型生成溫度的預設值",
    )
    openrouter_top_p: float = Field(
        default=1.0,
        gt=0.0,
        le=1.0,
        description="模型 nucleus sampling 的預設值",
    )
    openrouter_max_tokens: int = Field(
        default=1200,
        ge=0,
        le=8192,
        description="模型單次輸出的最大 token 數；0 表示不傳送上限",
    )
    openrouter_request_timeout_seconds: float = Field(
        default=60.0,
        description="OpenRouter API request timeout 秒數",
    )

    openrouter_provider_sort: Literal["auto", "latency", "throughput", "price"] = "auto"
    rag_rerank_enabled: bool = False
    rag_rerank_model: str = Field(default="cohere/rerank-4-fast", min_length=1)
    rag_rerank_candidate_k: int = Field(default=20, ge=1, le=50)
    rag_rerank_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10)
    rag_rerank_max_chars_per_doc: int = Field(default=2000, ge=100, le=20000)

    # ── RAG / Firestore Vector Search ─────────────────────────────────────
    rag_collection_name: str = Field(
        default="rag_documents",
        description="Firestore RAG 文件 Collection 名稱",
    )
    rag_judgment_collection_name: str = Field(
        default="rag_judgments",
        description="Firestore 判決書向量 Collection 名稱",
    )
    rag_remedy_collection_name: str = Field(
        default="rag_remedies",
        description="Firestore 救濟資源向量 Collection 名稱",
    )
    rag_retrieval_top_k: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Agentic RAG 每次工具檢索的文件數量",
    )
    embedding_provider: Literal["openrouter", "local"] = Field(
        default="openrouter",
        description="Embedding 來源：openrouter 或 local sentence-transformers",
    )
    embedding_model: str = Field(
        default="intfloat/multilingual-e5-large",
        description="主要 Embedding 模型，預設為 multilingual-e5-large",
    )
    embedding_dimensions: int = Field(default=1024, ge=1, le=2048)
    firestore_database_id: str | None = None
    firebase_project_id: str | None = None
    clarification_signing_key: str | None = None
    clarification_legacy_signing_key: str | None = None
    clarification_legacy_valid_until: str | None = None

    # ── Firebase Admin ───────────────────────────────────────────────────
    firebase_admin_credential_path: Path = Field(
        default=ROOT_DIR / "firebase_admin.json",
        description="Firebase Admin SDK JSON 路徑",
    )

    # ── Shared business configuration ────────────────────────────────────
    runtime_config_collection_name: str = Field(
        default="runtime_config",
        description="Firestore runtime config collection 名稱",
    )
    runtime_config_document_id: str = Field(
        default="app_dev",
        description="Firestore runtime config document ID",
    )
    runtime_config_cache_ttl_seconds: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="Runtime config process-local cache TTL 秒數",
    )
    # ── Flask API ────────────────────────────────────────────────────────
    api_title: str = "性騷擾防治智能 AI API"
    api_version: str = "0.1.0"
    environment: Literal["development", "test", "ci", "preview", "production"] = Field(
        default="development",
        description="執行環境；只有 production 應觸發生產環境強制條件",
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CORS_ORIGINS),
        description="允許的完整 CORS origin allowlist（不允許 wildcard）",
    )
    cors_preview_origin_regexes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CORS_PREVIEW_ORIGIN_REGEXES),
        description="允許的 Firebase Hosting preview origin 嚴格錨定 regex allowlist",
    )
    api_max_content_length_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        le=20 * 1024 * 1024,
        description="Flask API 單一請求 body 上限（bytes）",
    )
    chat_app_check_enabled: bool = Field(
        default=False,
        description="是否強制聊天端點驗證 X-Firebase-AppCheck；前端 rollout 前保持關閉",
    )
    chat_rate_limit_enabled: bool = Field(
        default=True,
        description="是否啟用行程內、依用戶端 IP 區分的聊天滑動視窗限流",
    )
    chat_rate_limit_requests: int = Field(
        default=30,
        ge=1,
        le=1000,
        description="每個行程在限流視窗內允許的聊天請求數",
    )
    chat_rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="聊天限流滑動視窗秒數",
    )

    @model_validator(mode="after")
    def enforce_environment_database(self):
        expected = "(default)" if self.environment == "production" else "app-dev"
        secret_suffix = "PROD" if self.environment == "production" else "DEV"
        self.openrouter_api_key = self.openrouter_api_key or os.getenv(
            f"RAG_OPENROUTER_{secret_suffix}", ""
        )
        self.clarification_signing_key = self.clarification_signing_key or os.getenv(
            f"RAG_CLARIFICATION_{secret_suffix}"
        )
        self.clarification_legacy_signing_key = self.clarification_legacy_signing_key or os.getenv(
            f"RAG_CLARIFICATION_LEGACY_{secret_suffix}"
        )
        if self.environment in {"production", "preview"}:
            if self.firestore_database_id != expected:
                raise ValueError(f"{self.environment} requires FIRESTORE_DATABASE_ID={expected}")
            expected_document = "app_main" if self.environment == "production" else "app_dev"
            if self.runtime_config_document_id != expected_document:
                raise ValueError(
                    f"{self.environment} requires runtime document {expected_document}"
                )
            discovering = os.getenv("RAG_FUNCTION_DISCOVERY") == "1" and not os.getenv("K_SERVICE")
            if not discovering and (
                not self.clarification_signing_key or not self.openrouter_api_key
            ):
                raise ValueError(
                    "Model and clarification secrets are required for deployed services"
                )
        elif self.firestore_database_id is None:
            self.firestore_database_id = "app-dev"
        elif self.firestore_database_id != expected and not os.environ.get(
            "FIRESTORE_EMULATOR_HOST"
        ):
            raise ValueError("Local applications must use the app-dev business database")
        for name in (
            self.rag_collection_name,
            self.rag_judgment_collection_name,
            self.rag_remedy_collection_name,
            self.runtime_config_collection_name,
        ):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,100}", name):
                raise ValueError("Collections must be configured as single collection names")
        return self

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: list[str]) -> list[str]:
        """Normalize exact HTTP(S) origins and fail safe for a legacy wildcard."""
        normalized: list[str] = []
        legacy_wildcard_configured = False
        for raw_origin in origins:
            origin = raw_origin.strip().rstrip("/")
            if origin == "*":
                legacy_wildcard_configured = True
                continue
            if not origin:
                continue
            parsed = urlsplit(origin)
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError(
                    f"CORS origin must be an exact HTTP(S) origin: {raw_origin!r}"
                ) from exc
            has_regex_metacharacter = any(char in origin for char in r"*\[](){}|^$")
            invalid_origin = (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or has_regex_metacharacter
                or any(char.isspace() for char in origin)
            )
            if invalid_origin:
                raise ValueError(f"CORS origin must be an exact HTTP(S) origin: {raw_origin!r}")
            if origin not in normalized:
                normalized.append(origin)
        if normalized:
            return normalized
        return list(DEFAULT_CORS_ORIGINS) if legacy_wildcard_configured else []

    @field_validator("cors_preview_origin_regexes")
    @classmethod
    def validate_cors_preview_origin_regexes(cls, patterns: list[str]) -> list[str]:
        """Accept only fully anchored Firebase Hosting preview-host regexes."""
        normalized: list[str] = []
        for raw_pattern in patterns:
            pattern = raw_pattern.strip()
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Invalid CORS preview origin regex: {raw_pattern!r}") from exc
            if _SAFE_FIREBASE_PREVIEW_ORIGIN_REGEX.fullmatch(pattern) is None:
                raise ValueError(
                    "CORS preview origin regex must be a strictly anchored Firebase Hosting "
                    "preview origin"
                )
            if pattern not in normalized:
                normalized.append(pattern)
        return normalized

    # ── Privacy ──────────────────────────────────────────────────────────
    enable_anonymization: bool = Field(default=True, description="是否啟用請求前的 PII 匿名化")


@lru_cache
def get_settings() -> Settings:
    """取得快取的設定單例，避免重複讀取 .env。"""
    return Settings()  # type: ignore[call-arg]
