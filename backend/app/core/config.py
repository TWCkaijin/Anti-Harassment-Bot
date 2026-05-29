"""
性騷擾防治智能 AI — 後端設定模組
統一管理所有環境變數與應用設定
"""

from functools import lru_cache
from pathlib import Path

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

    # ── Gemini / Google ADK ──────────────────────────────────────────────
    gemini_api_key: str = Field(..., description="Gemini API Key")
    gemini_model: str = Field(default="gemini-3.5-flash", description="預設使用的 Gemini 模型")

    # ── Firebase Admin ───────────────────────────────────────────────────
    firebase_project_id: str = Field(
        default="anti-harassment-bot", description="Firebase Project ID"
    )
    firebase_admin_credential_path: Path = Field(
        default=ROOT_DIR / "firebase_admin.json",
        description="Firebase Admin SDK JSON 路徑",
    )

    # ── FastAPI ──────────────────────────────────────────────────────────
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
