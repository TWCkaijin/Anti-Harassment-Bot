"""
性騷擾防治智能 AI — FastAPI 主入口
"""

import os

import firebase_admin
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import credentials

from backend.app.api.chat import router as chat_router
from backend.app.api.health import router as health_router
from backend.app.core.config import get_settings
from backend.app.core.logger import setup_logging

settings = get_settings()
setup_logging()

# ── Firebase Admin 初始化 ────────────────────────────────────────────────────
_cred_path = str(settings.firebase_admin_credential_path)
if os.path.exists(_cred_path) and not firebase_admin._apps:
    _cred = credentials.Certificate(_cred_path)
    firebase_admin.initialize_app(_cred)

# ── FastAPI 應用程式 ─────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=(
        "性騷擾防治智能 AI API — 為潛在受害者提供安全的諮詢、關懷與通報指引。\n\n"
        "**隱私聲明**：所有對話在後端進行 PII 匿名化處理，且不儲存任何對話記錄。"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS 設定 ────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── 掛載 Routers ─────────────────────────────────────────────────────────────
# 支援 /api/v1 (本地與 Firebase Hosting Rewrite) 以及 /v1 (直接呼叫 Cloud Function)
app.include_router(health_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(health_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": settings.api_title,
        "version": settings.api_version,
        "docs": "/docs",
    }
