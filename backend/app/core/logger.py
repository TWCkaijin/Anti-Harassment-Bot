import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

from flask import has_request_context, request

_TRACE_CONTEXT_PATTERN = re.compile(
    r"(?P<trace>[0-9a-fA-F]{32})(?:/(?P<span>[0-9]{1,20}))?(?:;o=(?P<sampled>[01]))?"
)
_STANDARD_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def _cloud_project_id() -> str | None:
    for variable in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        if project_id := os.environ.get(variable, "").strip():
            return project_id
    # Firebase also accepts a filename here; never open it or credential files.
    try:
        firebase_config = json.loads(os.environ.get("FIREBASE_CONFIG", "{}"))
    except (TypeError, ValueError):
        return None
    if isinstance(firebase_config, dict):
        project_id = firebase_config.get("projectId")
        if isinstance(project_id, str) and project_id.strip():
            return project_id.strip()
    return None


def get_request_log_context(req: Any | None = None) -> dict[str, Any]:
    """Return request routing and validated trace metadata, never request contents."""
    if req is None:
        if not has_request_context():
            return {}
        req = request

    context: dict[str, Any] = {}
    for attribute, field in (("method", "request_method"), ("path", "request_path")):
        value = getattr(req, attribute, None)
        if isinstance(value, str):
            context[field] = value

    headers = getattr(req, "headers", None)
    header = headers.get("X-Cloud-Trace-Context") if headers is not None else None
    if not isinstance(header, str) or not (match := _TRACE_CONTEXT_PATTERN.fullmatch(header)):
        return context

    trace_id = match["trace"].lower()
    span_id = int(match["span"]) if match["span"] is not None else None
    if int(trace_id, 16) == 0 or (span_id is not None and not 0 < span_id < 2**64):
        return context
    if not (project_id := _cloud_project_id()):
        return context

    context["logging.googleapis.com/trace"] = f"projects/{project_id}/traces/{trace_id}"
    if span_id is not None:
        context["logging.googleapis.com/spanId"] = f"{span_id:016x}"
    if match["sampled"] is not None:
        context["logging.googleapis.com/trace_sampled"] = match["sampled"] == "1"
    return context


def _safe_log_text(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}: unprintable>"


def _json_safe_extra(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=_safe_log_text, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError):
        # Circular containers, invalid dictionary keys and non-finite floats must
        # not prevent the original ERROR from reaching Cloud Logging.
        return _safe_log_text(value)


class GCPJsonFormatter(logging.Formatter):
    """
    用於部署環境的 GCP 結構化日誌 Formatter。
    將日誌轉換成 Google Cloud Logging 支援的 JSON 格式。
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            key: _json_safe_extra(value)
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_KEYS
        }
        log_data.update(get_request_log_context())
        # Framework/application extras cannot override the actual log severity.
        log_data.update(
            {
                "severity": record.levelname,
                "message": record.getMessage(),
                "logger": record.name,
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "logging.googleapis.com/sourceLocation": {
                    "file": record.pathname,
                    "line": str(record.lineno),
                    "function": record.funcName,
                },
            }
        )

        # 例外追蹤資訊
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            log_data["exception"] = record.exc_text
        if record.stack_info:
            log_data["stack"] = self.formatStack(record.stack_info)

        return json.dumps(log_data, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """
    用於本地開發環境的彩色文字日誌 Formatter。
    """

    # ANSI 顏色控制碼
    ANSI_RESET = "\033[0m"
    ANSI_BOLD = "\033[1m"
    COLORS = {
        "DEBUG": "\033[36m",  # 青色
        "INFO": "\033[32m",  # 綠色
        "WARNING": "\033[33m",  # 黃色
        "ERROR": "\033[31m",  # 紅色
        "CRITICAL": "\033[1;41m",  # 粗體紅底白字
    }

    def format(self, record: logging.LogRecord) -> str:
        asctime = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        levelname = record.levelname
        color = self.COLORS.get(levelname, "")
        reset = self.ANSI_RESET
        bold = self.ANSI_BOLD

        # 格式化訊息
        message = record.getMessage()
        exc_text = ""
        if record.exc_info:
            exc_text = "\n" + self.formatException(record.exc_info)

        # 取得相對路徑以便於閱讀
        rel_path = record.filename
        if "backend/app/" in record.pathname:
            rel_path = record.pathname.split("backend/app/")[-1]

        # 輸出格式：[時間] [等級] [檔名:行數 - 函數] 訊息
        return (
            f"[{color}{levelname}{reset}] {asctime} "
            f"[{bold}{rel_path}:{record.lineno}{reset} {record.funcName}] - {message}{exc_text}"
        )


def setup_logging() -> None:
    """
    初始化與設定全域日誌配置。
    根據環境變數 ENVIRONMENT 切換彩色終端機格式或 GCP JSON 結構化格式。
    """
    # Deployment wrappers can set ENVIRONMENT after importing this module.
    environment = os.environ.get("ENVIRONMENT", "development")
    cloud_runtime = any(
        name in os.environ for name in ("K_SERVICE", "FUNCTION_TARGET", "FUNCTION_NAME")
    )
    local_development = environment == "development" and not cloud_runtime
    formatter = ColoredFormatter() if local_development else GCPJsonFormatter()

    # 清除或接管預設的 root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 建立一個統一輸出到 stdout 的 handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    # 設定全域與相關框架的日誌等級
    log_level = logging.DEBUG if local_development else logging.INFO
    root_logger.setLevel(log_level)

    # 接管 Uvicorn/FastAPI 的日誌，使其格式統一
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        target_logger = logging.getLogger(logger_name)
        target_logger.handlers = []
        target_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """取得具備全域設定格式的 Logger。"""
    return logging.getLogger(name)
