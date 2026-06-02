"""
性騷擾防治智能 AI — Flask 主入口
"""

import os

import firebase_admin
from firebase_admin import credentials
from flask import Flask, jsonify
from flask_cors import CORS

from backend.app.api.chat import chat_bp
from backend.app.api.health import health_bp
from backend.app.core.config import get_settings
from backend.app.core.logger import setup_logging

settings = get_settings()
setup_logging()

# ── Firebase Admin 初始化 ────────────────────────────────────────────────────
if not firebase_admin._apps:
    _cred_path = str(settings.firebase_admin_credential_path)
    _initialized = False
    if os.path.exists(_cred_path) and os.path.getsize(_cred_path) > 10:
        try:
            _cred = credentials.Certificate(_cred_path)
            firebase_admin.initialize_app(_cred)
            _initialized = True
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                f"Invalid firebase creds at {_cred_path}, falling back to ADC. Error: {e}"
            )
    if not _initialized:
        firebase_admin.initialize_app()

# ── Flask 應用程式 ─────────────────────────────────────────────────────────
app = Flask(__name__)

# ── CORS 設定 ────────────────────────────────────────────────────────────────
# 設定與原本 FastAPI 相同的 CORS
# Flask-CORS 允許針對全域或特定路由設定
CORS(app, resources={r"/*": {"origins": settings.cors_origins}}, supports_credentials=False)

# ── 掛載 Blueprints ─────────────────────────────────────────────────────────────
# 支援 /api/v1 (本地與 Firebase Hosting Rewrite)
app.register_blueprint(health_bp, url_prefix="/api/v1/health")
app.register_blueprint(chat_bp, url_prefix="/api/v1/chat")
# 支援 /v1 (直接呼叫 Cloud Function 且無 rewrite 時備用)
app.register_blueprint(health_bp, url_prefix="/v1/health", name="health_v1")
app.register_blueprint(chat_bp, url_prefix="/v1/chat", name="chat_v1")


@app.route("/")
def root():
    return jsonify(
        {
            "service": settings.api_title,
            "version": settings.api_version,
        }
    )
