"""
性騷擾防治智能 AI — 後端設定模組
統一管理所有環境變數與應用設定
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()
# 專案根目錄（backend/ 的上一層）
ROOT_DIR = Path(__file__).parent.parent.parent.parent


class Settings(BaseSettings):
    """應用程式設定，優先從 .env 讀取，再從環境變數讀取。"""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── OpenRouter ─────────────────────────────────────────────────────────
    openrouter_api_key: str = Field(..., description="OpenRouter API Key")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter OpenAI-compatible API base URL",
    )
    openrouter_model: str = Field(default="google/gemma-2-9b-it", description="預設使用的模型")
    openrouter_request_timeout_seconds: float = Field(
        default=60.0,
        description="OpenRouter API request timeout 秒數",
    )
    agent_system_prompt: str | None = Field(
        default=None,
        description="OpenRouter Agent system prompt；未設定時使用程式內建預設 prompt",
    )

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

    # ── Firebase Admin ───────────────────────────────────────────────────
    firebase_project_id: str = Field(
        default="anti-harassment-bot", description="Firebase Project ID"
    )
    firebase_admin_credential_path: Path = Field(
        default=ROOT_DIR / "firebase_admin.json",
        description="Firebase Admin SDK JSON 路徑",
    )

    # ── Flask API ────────────────────────────────────────────────────────
    api_title: str = "性騷擾防治智能 AI API"
    api_version: str = "0.1.0"
    cors_origins: list[str] = Field(
        default=["*"],
        description="允許的 CORS 來源",
    )

    # ── Privacy ──────────────────────────────────────────────────────────
    enable_anonymization: bool = Field(default=True, description="是否啟用請求前的 PII 匿名化")


@lru_cache
def get_settings() -> Settings:
    """取得快取的設定單例，避免重複讀取 .env。"""
    return Settings()  # type: ignore[call-arg]
