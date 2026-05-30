"""
性騷擾防治智能 AI — Health Check Blueprint
提供服務健康狀態端點，供 Firebase Functions warm-up 與 CI/CD 監控使用。
"""

import os
from datetime import UTC, datetime
from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__, url_prefix="/health")

@health_bp.route("/", methods=["GET"])
def health_check():
    """回傳服務運行狀態，用於 load balancer 與 CI/CD 驗證。"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "version": os.getenv("APP_VERSION", "0.1.0"),
        "environment": os.getenv("ENVIRONMENT", "development"),
    })
