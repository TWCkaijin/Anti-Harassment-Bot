"""Exclude private-organization referrals from active service guidance.

Judgment originals are evidence, not a directory of recommended services. This
policy only filters curated resource rows, actions, and generated referral text.
"""

from __future__ import annotations

import re

_PRIVATE_ORGANIZATION = re.compile(
    r"(?:勵馨|現代婦女|基金會|協會|公益團體|民間團體|社團法人|財團法人)"
)
_GENERIC_PRIVATE_LEGAL_SERVICE = re.compile(r"公益法律服務(?:機構|團體|組織)")
_PUBLIC_LEGAL_SERVICE = re.compile(r"(?:政府|公設|公立|公部門|地方主管機關).{0,12}法律服務")
_REFERRAL_VERB = re.compile(r"(?:聯絡|諮詢|求助|轉介|撥打|洽詢|尋求|提供.{0,8}(?:協助|支援))")
_NEGATED_REFERRAL = re.compile(
    r"(?:不要|不得|禁止|避免|不主動).{0,16}(?:轉介|推薦|提供|聯絡).{0,16}(?:基金會|協會|團體)"
)
_KNOWN_PRIVATE_NUMBERS = {"0223917133", "0289118595", "087512243"}


def is_private_referral_resource(content: str, metadata: dict) -> bool:
    """Quarantine legacy resource rows without discarding raw law or judgments."""
    collection = str(metadata.get("collection") or "")
    if not any(
        metadata.get(key) in ("remedy", "resource")
        for key in ("data_type", "document_type", "type")
    ) and not collection.endswith("remedies"):
        return False
    return bool(_PRIVATE_ORGANIZATION.search(content))


def is_private_referral_action(action) -> bool:
    label = str(getattr(action, "label", "") or "")
    phone = re.sub(r"\D", "", str(getattr(action, "phone_number", "") or ""))
    return bool(_PRIVATE_ORGANIZATION.search(label) or phone in _KNOWN_PRIVATE_NUMBERS)


def is_private_referral_sentence(text: str) -> bool:
    """Catch proactive referral prose while allowing factual law discussion."""
    return bool(
        (
            _PRIVATE_ORGANIZATION.search(text)
            or (
                _GENERIC_PRIVATE_LEGAL_SERVICE.search(text)
                and not _PUBLIC_LEGAL_SERVICE.search(text)
            )
        )
        and _REFERRAL_VERB.search(text)
        and not _NEGATED_REFERRAL.search(text)
    )
