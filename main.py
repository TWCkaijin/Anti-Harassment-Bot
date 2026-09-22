import json
import logging

from firebase_functions import https_fn, options
from flask import Response as FlaskResponse
from werkzeug.wrappers import Response as WerkzeugResponse

from backend.app.core.config import get_settings
from backend.app.core.logger import get_request_log_context
from backend.app.core.security import cors_headers_for_origin, new_error_id
from backend.app.main import app

logger = logging.getLogger(__name__)
settings = get_settings()


def handle_request(req: https_fn.Request) -> https_fn.Response:
    try:
        # Flask-CORS owns both preflight and normal response headers.
        w_res = WerkzeugResponse.from_app(app, req.environ)
        return FlaskResponse(
            response=w_res.get_data(),
            status=w_res.status_code,
            headers=list(w_res.headers),
        )
    except Exception:
        error_id = new_error_id()
        logger.exception(
            "Unhandled Firebase HTTP wrapper exception error_id=%s",
            error_id,
            extra={
                **get_request_log_context(req),
                "event": "firebase_wrapper_error",
                "error_id": error_id,
                "http_status": 500,
            },
        )
        return FlaskResponse(
            response=json.dumps(
                {"detail": "Internal Server Error", "error_id": error_id},
                ensure_ascii=False,
            ),
            status=500,
            mimetype="application/json",
            headers=cors_headers_for_origin(
                req.headers.get("Origin"),
                settings.cors_origins,
                settings.cors_preview_origin_regexes,
            ),
        )


# 註冊為 Firebase HTTP 函數，支援公開呼叫 (invoker="public")
# 增加記憶體至 512MB 以獲得更多 CPU 資源，並將超時時間延長至 180 秒
@https_fn.on_request(
    region="asia-east1",
    invoker="public",
    timeout_sec=180,
    memory=options.MemoryOption.MB_512,
)
def api(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)


@https_fn.on_request(
    region="asia-east1",
    invoker="public",
    timeout_sec=180,
    memory=options.MemoryOption.MB_512,
)
def api_preview(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)
