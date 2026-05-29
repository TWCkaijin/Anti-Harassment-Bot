"""
性騷擾防治智能 AI — Health Check Router
提供服務健康狀態端點，供 Firebase Functions warm-up 與 CI/CD 監控使用。
"""
import os
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str
    environment: str


@router.get("/", response_model=HealthResponse, summary="健康狀態檢查")
async def health_check() -> HealthResponse:
    """回傳服務運行狀態，用於 load balancer 與 CI/CD 驗證。"""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(tz=UTC).isoformat(),
        version=os.getenv("APP_VERSION", "0.1.0"),
        environment=os.getenv("ENVIRONMENT", "development"),
    )
