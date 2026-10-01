"""
性騷擾防治智能 AI — PII 匿名化模組
在請求傳送給 AI 模型前，移除或遮蓋個人識別資訊（PII）以保護使用者隱私。

支援的 PII 類型（中英文）：
- 台灣手機號碼 / 市話
- 電子郵件地址
- 身份證字號
- 信用卡號
- IP 位址

角色、行為、場所、日期及法條不是識別資訊。本模組不以姓名關鍵詞遮罩自由文字。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

# ── PII 規則定義 ─────────────────────────────────────────────────────────────


@dataclass
class PIIRule:
    """單一 PII 偵測規則。"""

    name: str
    pattern: re.Pattern[str]
    replacement: str
    should_mask: Callable[[re.Match[str], str], bool] | None = None


def _mask_card_candidate(match: re.Match[str], text: str) -> bool:
    """Preserve an unambiguous pair of compact calendar dates, not arbitrary IDs."""
    prefix = text[max(0, match.start() - 16) : match.start()]
    if re.search(r"(?:信用卡|卡號|card(?:\s+number)?)\s*[:：]?\s*$", prefix, re.I):
        return True
    dates = re.fullmatch(r"((?:19|20)\d{6})[-\s]((?:19|20)\d{6})", match.group())
    if dates is None:
        return True
    try:
        for compact in dates.groups():
            date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
    except ValueError:
        return True
    return False


def _mask_ip_candidate(match: re.Match[str], text: str) -> bool:
    """A dotted section/version number is not an IP address in document context."""
    prefix = text[max(0, match.start() - 24) : match.start()]
    suffix = text[match.end() : match.end() + 16]
    if re.search(r"(?:IPv?4?|IP位址|IP地址|網路位址|主機位址|來源位址)\s*[:：]?\s*$", prefix, re.I):
        return True
    document_label = r"(?:小節|章節|條款|段落|編號|條目|版本|版號)"
    if re.search(document_label + r"\s*[:：]?\s*$", prefix):
        return False
    return not re.match(r"\s*(?:小節|章節|條款|段落|版本|版號|節|章|款|項)", suffix)


_RULES: list[PIIRule] = [
    PIIRule(
        name="taiwan_mobile",
        pattern=re.compile(r"(?<![A-Za-z0-9])09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?![A-Za-z0-9])"),
        replacement="[手機號碼]",
    ),
    PIIRule(
        name="taiwan_phone",
        pattern=re.compile(r"\(0\d{1,2}\)\s?\d{3,4}[-\s]\d{4}(?![A-Za-z0-9])"),
        replacement="[電話號碼]",
    ),
    PIIRule(
        name="email",
        pattern=re.compile(
            r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@"
            r"[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.\-])"
        ),
        replacement="[電子郵件]",
    ),
    PIIRule(
        name="taiwan_id",
        pattern=re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])"),
        replacement="[身份證號]",
    ),
    PIIRule(
        name="credit_card",
        pattern=re.compile(r"(?<![A-Za-z0-9])(?:\d{4}[-\s]?){3}\d{4}(?![A-Za-z0-9])"),
        replacement="[信用卡號]",
        should_mask=_mask_card_candidate,
    ),
    PIIRule(
        name="ipv4",
        pattern=re.compile(
            r"(?<![A-Za-z0-9.])(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?![A-Za-z0-9.])"
        ),
        replacement="[IP位址]",
        should_mask=_mask_ip_candidate,
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
        source = result

        def replacement(match, rule=rule, source=source):
            if rule.should_mask is not None and not rule.should_mask(match, source):
                return match.group()
            return rule.replacement

        new_result = rule.pattern.sub(replacement, result)
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
