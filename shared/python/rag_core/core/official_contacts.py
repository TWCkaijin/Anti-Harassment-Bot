"""Read-only public telephone entries packaged with the call-support Skill."""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from rag_core.core.chat_response import validate_action_url

_CATALOG = Path(__file__).resolve().parents[1] / "skills" / "call-support" / "contacts.json"
_CONTACT = re.compile(r"電話|總機|專線|號碼|分機")
_CORRECTION = re.compile(r"更正|說錯|錯了|不對|應該是|才對")
_DECLARATION = re.compile(r"(?:電話|總機|專線|號碼)(?:號碼)?\s*(?:是|為|[:：])")
_REFERENCE = re.compile(r"剛剛|剛才|上一輪|你說的|你提供的|那[支個組]|更正")
_REQUEST = re.compile(r"給我|提供|請問|想知道|我想查|查詢|多少|幾號|是什麼|怎麼打")
_NUMBER = re.compile(r"\[電話號碼\]|(?:0\d[\d ()-]{6,}\d)|(?<!\d)\d{3,}(?!\d)")
_AGENCY_DETAIL = re.compile(r"(?:的)?[\u3400-\u9fff]{0,6}(?:局|科|處|中心|承辦人|個人|手機)")


@dataclass(frozen=True)
class OfficialContact:
    label: str
    aliases: tuple[str, ...]
    phone: str
    extension: str | None
    source_url: str
    checked_at: str


@lru_cache(maxsize=1)
def contact_catalog() -> tuple[str, tuple[OfficialContact, ...]]:
    data = json.loads(_CATALOG.read_text(encoding="utf-8"))
    date.fromisoformat(data["version"])
    entries = []
    for entry in data["contacts"]:
        url = validate_action_url(entry["source_url"])
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not (parsed.hostname or "").endswith(".gov.tw")
            or not re.fullmatch(r"(?:\d{3,4}|0\d{1,3}-\d{6,8})", entry["phone"])
            or (entry["extension"] is not None and not re.fullmatch(r"\d{1,6}", entry["extension"]))
        ):
            raise ValueError("Invalid packaged official contact")
        date.fromisoformat(entry["checked_at"])
        entries.append(OfficialContact(**{**entry, "aliases": tuple(entry["aliases"])}))
    return data["version"], tuple(entries)


def _named_contacts(text: str, *, match_short_codes=True) -> list[OfficialContact]:
    matches = []
    for entry in contact_catalog()[1]:
        for alias in entry.aliases:
            if alias.isdecimal():
                if not match_short_codes:
                    continue
                pattern = rf"(?<![\d-]){re.escape(alias)}(?![\d-])"
                match = re.search(pattern, text)
            else:
                match = re.search(re.escape(alias), text)
            if match:
                matches.append((match.start(), match.end(), entry))
    # Prefer the specific unit when its name contains its parent (e.g. 市政府勞工局).
    return list(
        dict.fromkeys(
            entry
            for start, end, entry in matches
            if not any(
                other_start <= start and other_end >= end and other_end - other_start > end - start
                for other_start, other_end, _ in matches
            )
            and not (
                text[start:end] in {"高雄市政府", "高雄市府"} and _AGENCY_DETAIL.match(text[end:])
            )
        )
    )


def _contact_only_request(text: str) -> bool:
    if not _CONTACT.search(text):
        return False
    return bool(
        _REQUEST.search(text)
        or re.fullmatch(r"[\u3400-\u9fff\d\s]{2,40}(?:電話|總機|專線|號碼)[?。]?", text.strip())
    )


def official_contact_reply(
    message: str, history: list[dict] | None
) -> tuple[str, list[dict]] | None:
    """Resolve direct requests and phone corrections without using user-supplied digits."""
    message = unicodedata.normalize("NFKC", message)
    has_topic = bool(_CONTACT.search(message))
    has_number = bool(_NUMBER.search(message))
    refers_back = bool(_REFERENCE.search(message))
    is_correction = bool(
        (_CORRECTION.search(message) and (has_topic or has_number or refers_back))
        or (_DECLARATION.search(message) and has_number)
    )
    entries = (
        _named_contacts(message, match_short_codes=not is_correction)
        if has_topic or _REQUEST.search(message)
        else []
    )
    # A phone correction may refer only to the last public contact request.
    if (
        is_correction
        and not entries
        and refers_back
        and not re.search(
            r"(?:縣|市)(?:政府|府)|(?:勞工|社會|教育)局|我的|我本人|私人|手機", message
        )
    ):
        previous = next(
            (item.get("content") for item in reversed(history or []) if item.get("role") == "user"),
            None,
        )
        if isinstance(previous, str):
            previous = unicodedata.normalize("NFKC", previous)
            if _contact_only_request(previous) or (
                _DECLARATION.search(previous) and _NUMBER.search(previous)
            ):
                entries = _named_contacts(previous)
    if not entries or not (
        is_correction
        or _contact_only_request(message)
        or (_REQUEST.search(message) and not has_topic and len(message) <= 20)
    ):
        return None
    # Keep mixed requests on the main answer path.
    if not is_correction and re.search(
        r"(?:還想|順便|另外|以及|並且|怎麼申訴|如何申訴|算性騷擾)", message
    ):
        return None

    contacts = entries[:2]
    lines = [f"依官方公布的聯絡資料（核對日期：{contacts[0].checked_at}）："]
    actions = []
    if is_correction:
        lines.insert(0, "您提供的更正未經核對，請以下列官方資料為準。")
    for entry in contacts:
        extension = f" 轉 {entry.extension}" if entry.extension else ""
        lines.append(f"- {entry.label}：{entry.phone}{extension}")
        actions.append(
            {"action": "tel", "phone_number": entry.phone, "label": f"撥打{entry.label}"}
        )
    if len({entry.source_url for entry in contacts}) == 1:
        actions.append(
            {"action": "url", "url": contacts[0].source_url, "label": "查看官方聯絡資訊"}
        )
    return "\n".join(lines), actions
