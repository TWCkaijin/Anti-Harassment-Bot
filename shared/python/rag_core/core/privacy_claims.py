"""Reject unsupported claims about this service's handling of identifiers."""

import re
from collections.abc import Iterator
from typing import Any

VISIBLE_FIELDS = (
    "reply",
    "suggested_replies",
    "clarifying_questions",
    "action_buttons",
    "answer_sections",
    "analysis",
    "reasoning",
    "summary_update",
    "fact_updates",
    "clarification",
)
_SERVICE = ("系統", "服務", "平台", "網站", "我們", "我")
_IDENTIFIERS = (
    "個人識別資訊",
    "個人資料",
    "個資",
    "姓名",
    "電話",
    "聯絡方式",
    "任何資料",
    "任何資訊",
)
_THIRD_PARTY = ("被害人", "受害者", "當事人", "他人", "第三人", "對方", "某人", "案主", "個案")
_PRIVACY_TOPIC = ("隱私", "個資", "個人資料", "識別資訊", "收集", "蒐集", "儲存", "保存", "刪除")
_NO_DATA = re.compile(
    r"(?:不會|絕不|從不|一律不|完全不).{0,35}(?:收集|蒐集|儲存|保存|保留|記錄|使用|處理)"
)
_CANNOT_PROCESS = re.compile(
    r"(?:無法|不能).{0,5}(?:接受|接收|收到|處理).{0,12}(?:或|及|並|、)?.{0,8}(?:處理|使用|儲存)?"
)
_QUALIFIED = re.compile(r"(?:不能|無法|不可|不應)(?:作出|提供|宣稱|聲稱|保證).{0,3}$")

THIRD_PARTY_REPLY = (
    "抱歉，我無法提供或核對他人的姓名、電話等識別資料。"
    "這個對話服務會接收並處理送出的內容以產生回覆；請避免輸入不必要的個資，並循正式管道向有權機關查詢。"
)
GENERAL_REPLY = (
    "這個對話服務會接收並處理送出的內容以產生回覆。"
    "我無法確認所有資料保存與存取安排；請避免輸入不必要的個人識別資訊。"
)


def needs_private_completion(message: str, history: list[dict] | None = None) -> bool:
    """Delay visible streaming for turns likely to discuss identifiable data."""
    recent = [message, *(str(item.get("content", "")) for item in (history or [])[-4:])]
    return any(
        asks_for_third_party_identifiers(text) or any(term in text for term in _PRIVACY_TOPIC)
        for text in recent
    )


def asks_for_third_party_identifiers(message: str) -> bool:
    return any(term in message for term in _THIRD_PARTY) and any(
        term in message for term in _IDENTIFIERS
    )


def _visible_texts(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _visible_texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _visible_texts(item)


def has_unsupported_privacy_claim(payload: dict, *, third_party_request: bool) -> bool:
    """Flag absolute handling promises, or a false inability to process PII requests."""
    for field in VISIBLE_FIELDS:
        for text in _visible_texts(payload.get(field)):
            for sentence in re.split(r"[。！？!?\n]", text):
                sentence = re.sub(r"\s+", "", sentence)
                if not sentence or not any(subject in sentence for subject in _SERVICE):
                    continue
                for match in _NO_DATA.finditer(sentence):
                    if not any(subject in sentence[: match.start()] for subject in _SERVICE):
                        continue
                    if not any(identifier in sentence for identifier in _IDENTIFIERS):
                        continue
                    if not _QUALIFIED.search(sentence[max(0, match.start() - 14) : match.start()]):
                        return True
                if third_party_request and _CANNOT_PROCESS.search(sentence):
                    return True
    return False


def local_privacy_reply(message: str) -> str:
    return THIRD_PARTY_REPLY if asks_for_third_party_identifiers(message) else GENERAL_REPLY


def is_local_privacy_reply(payload: dict) -> bool:
    return payload.get("reply") in {THIRD_PARTY_REPLY, GENERAL_REPLY}
