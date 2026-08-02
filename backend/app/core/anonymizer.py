"""
性騷擾防治智能 AI — PII 匿名化模組
在請求傳送給 AI 模型前，移除或遮蓋個人識別資訊（PII）以保護使用者隱私。

支援的 PII 類型（中英文）：
- 台灣手機號碼 / 市話
- 電子郵件地址
- 身份證字號
- 信用卡號
- 姓名（基本模式）
- IP 位址
"""

import re
from dataclasses import dataclass, field

# ── PII 規則定義 ─────────────────────────────────────────────────────────────


@dataclass
class PIIRule:
    """單一 PII 偵測規則。"""

    name: str
    pattern: re.Pattern[str]
    replacement: str


_RULES: list[PIIRule] = [
    PIIRule(
        name="taiwan_mobile",
        pattern=re.compile(r"\b09\d{2}[-\s]?\d{3}[-\s]?\d{3}\b"),
        replacement="[手機號碼]",
    ),
    PIIRule(
        name="taiwan_phone",
        pattern=re.compile(r"\(0\d{1,2}\)\s?\d{3,4}[-\s]\d{4}\b"),
        replacement="[電話號碼]",
    ),
    PIIRule(
        name="email",
        pattern=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"),
        replacement="[電子郵件]",
    ),
    PIIRule(
        name="taiwan_id",
        pattern=re.compile(r"\b[A-Z][12]\d{8}\b"),
        replacement="[身份證號]",
    ),
    PIIRule(
        name="credit_card",
        pattern=re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        replacement="[信用卡號]",
    ),
    PIIRule(
        name="ipv4",
        pattern=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        replacement="[IP位址]",
    ),
]


# ── 匿名化結果 ───────────────────────────────────────────────────────────────


@dataclass
class AnonymizationResult:
    """匿名化結果，保留原文與處理後文字的對照。"""

    original: str
    anonymized: str
    detected_types: list[str] = field(default_factory=list)

    @property
    def was_modified(self) -> bool:
        return self.original != self.anonymized


# ── 主要匿名化函式 ───────────────────────────────────────────────────────────


def anonymize(text: str, rules: list[PIIRule] | None = None) -> AnonymizationResult:
    """
    對輸入文字執行 PII 匿名化。

    Args:
        text: 原始使用者輸入
        rules: 自訂 PII 規則（預設使用內建規則集）

    Returns:
        AnonymizationResult 包含匿名化後的文字及偵測到的 PII 類型清單
    """
    active_rules = rules if rules is not None else _RULES
    result = text
    detected: list[str] = []

    for rule in active_rules:
        new_result = rule.pattern.sub(rule.replacement, result)
        if new_result != result:
            detected.append(rule.name)
            result = new_result

    return AnonymizationResult(
        original=text,
        anonymized=result,
        detected_types=detected,
    )


def anonymize_messages(
    messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    批次匿名化對話歷史中的所有使用者訊息。
    僅處理 role='user' 的訊息，assistant 訊息保持原樣。

    Args:
        messages: 對話歷史，格式為 [{"role": "user"|"assistant", "content": "..."}]

    Returns:
        匿名化後的對話歷史（深度複製，不修改原始資料）
    """
    anonymized = []
    for msg in messages:
        if msg.get("role") == "user":
            result = anonymize(msg.get("content", ""))
            anonymized.append({**msg, "content": result.anonymized})
        else:
            anonymized.append(msg)
    return anonymized
