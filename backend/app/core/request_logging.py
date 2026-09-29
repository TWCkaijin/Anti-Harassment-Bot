"""Record handled HTTP failures as application logs, not just access logs."""

import logging

from flask import Flask, Response, request

logger = logging.getLogger(__name__)


def register_error_response_logging(app: Flask) -> None:
    @app.after_request
    def log_error_response(response: Response) -> Response:
        if response.status_code < 400:
            return response

        payload = response.get_json(silent=True) if response.is_json else None
        payload = payload if isinstance(payload, dict) else {}
        detail = payload.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            detail = response.status
        # Select public diagnostic fields explicitly. Never log request bodies,
        # auth headers, query strings, or the development-only debug_message.
        fields = {
            "event": "http_error_response",
            "http_status": response.status_code,
            "request_method": request.method,
            "request_path": request.path,
            "error_detail": detail,
        }
        for source, target in (("code", "error_code"), ("error_id", "error_id")):
            value = payload.get(source)
            if isinstance(value, str):
                fields[target] = value
        if isinstance(payload.get("retryable"), bool):
            fields["retryable"] = payload["retryable"]

        logger.log(
            logging.ERROR if response.status_code >= 500 else logging.WARNING,
            "HTTP %s %s %s: %s%s",
            response.status_code,
            request.method,
            request.path,
            f"[{fields['error_code']}] " if "error_code" in fields else "",
            detail,
            extra=fields,
        )
        return response
