import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# ── 取得環境變數（避免與 config.py 循環引用） ─────────────────────────────
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")


class GCPJsonFormatter(logging.Formatter):
    """
    用於部署環境的 GCP 結構化日誌 Formatter。
    將日誌轉換成 Google Cloud Logging 支援的 JSON 格式。
    """

    def format(self, record: logging.LogRecord) -> str:
        # 將級別對照為 GCP 認可的 severity
        severity = record.levelname
        if severity == "WARNING":
            severity = "WARNING"
        elif severity == "CRITICAL":
            severity = "CRITICAL"

        # 構造基本 JSON Payload
        log_data = {
            "severity": severity,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            },
        }

        # 例外追蹤資訊
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # 保留自訂的 extra 屬性（過濾掉系統預設屬性）
        standard_keys = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName", "processName",
            "process", "message"
        }
        for key, value in record.__dict__.items():
            if key not in standard_keys:
                log_data[key] = value

        return json.dumps(log_data, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """
    用於本地開發環境的彩色文字日誌 Formatter。
    """
    # ANSI 顏色控制碼
    ANSI_RESET = "\033[0m"
    ANSI_BOLD = "\033[1m"
    COLORS = {
        "DEBUG": "\033[36m",      # 青色
        "INFO": "\033[32m",       # 綠色
        "WARNING": "\033[33m",    # 黃色
        "ERROR": "\033[31m",      # 紅色
        "CRITICAL": "\033[1;41m", # 粗體紅底白字
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
    # 決定使用哪種 formatter
    if ENVIRONMENT == "development":
        formatter = ColoredFormatter()
    else:
        formatter = GCPJsonFormatter()

    # 清除或接管預設的 root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 建立一個統一輸出到 stdout 的 handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    # 設定全域與相關框架的日誌等級
    log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
    root_logger.setLevel(log_level)

    # 接管 Uvicorn/FastAPI 的日誌，使其格式統一
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        target_logger = logging.getLogger(logger_name)
        target_logger.handlers = []
        target_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """取得具備全域設定格式的 Logger。"""
    return logging.getLogger(name)
