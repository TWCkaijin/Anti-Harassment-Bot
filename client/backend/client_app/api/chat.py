"""Public chat transport backed by the shared inference service."""

from flask import Blueprint

from rag_core.chat_service import chat

chat_bp = Blueprint("chat", __name__)
chat_bp.add_url_rule("/", view_func=chat, methods=["POST"])
