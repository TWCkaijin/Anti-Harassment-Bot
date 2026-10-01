"""Build a reproducible article corpus from the MOJ official bulk JSON.

This module has no Firebase, embedding or network initialization. Dates supplied
by MOJ describe the law's latest amendment/schedule, not every article's history.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.app.rag.legal_lookup import LAW_ALIASES, article_number, lookup_key

OFFICIAL_URL = "https://law.moj.gov.tw/api/ch/law/json"
DATASET_URL = "https://data.gov.tw/dataset/18289"
REQUIRED_METADATA = (
    "law_name",
    "article_number",
    "source_url",
    "promulgated_date",
    "effective_date",
    "version",
    "checked_at",
)


def digest(value) -> str:
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    return hashlib.sha256(data).hexdigest()


def read_bulk(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    sha = digest(raw)
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo("ChLaw.json")
            if info.file_size > 100_000_000:
                raise ValueError("Official bulk archive exceeds the size budget")
            raw = archive.read(info)
    result = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(result.get("Laws"), list) or not result.get("UpdateDate"):
        raise ValueError("Unsupported MOJ bulk schema")
    return result, sha


def iso_date(value: str) -> str | None:
    if not value or value == "99991231":
        return None
    return date.fromisoformat(value).isoformat()


def build_corpus(bulk: dict, *, checked_at: str, source_sha256: str) -> tuple[list, dict]:
    date.fromisoformat(checked_at)
    selected = [law for law in bulk["Laws"] if law.get("LawName") in LAW_ALIASES]
    if Counter(law["LawName"] for law in selected) != Counter(list(LAW_ALIASES)):
        raise ValueError("The official bulk must contain exactly one entry for each of six laws")
    corpus_version = f"moj-{source_sha256[:16]}"
    documents, laws, pending = [], [], []
    for law in sorted(selected, key=lambda item: item["LawName"]):
        name = law["LawName"]
        parsed_url = urlsplit(law["LawURL"])
        if parsed_url.scheme != "https" or parsed_url.hostname != "law.moj.gov.tw":
            raise ValueError("Unexpected official law URL")
        pcode = parse_qs(parsed_url.query)["pcode"][0]
        if not re.fullmatch(r"[A-Z]\d{7}", pcode):
            raise ValueError("Unexpected official law identifier")
        modified = iso_date(law["LawModifiedDate"])
        effective = iso_date(law.get("LawEffectiveDate", ""))
        history = law.get("LawHistories", "").replace("\r\n", "\n")
        note = law.get("LawEffectiveNote", "").replace("\r\n", "\n")
        article_count = 0
        chapter = ""
        for item in law["LawArticles"]:
            if item["ArticleType"] != "A":
                chapter = item["ArticleContent"].strip()
                continue
            number = article_number(item["ArticleNo"])
            text = item["ArticleContent"].replace("\r\n", "\n").strip()
            if not text:
                raise ValueError("Official article has no content")
            status = "published"
            if re.fullmatch(r"[（(]?\s*刪除\s*[）)]?[。.]?", text):
                status = "deleted"
            # The bulk contains newly promulgated text, including provisions
            # not yet effective. These known MOJ schedule exceptions must not
            # enter default current-law retrieval.
            if name == "民法" and number == "166-1":
                status = "not_effective_date_unset"
            if name == "民法" and number == "1223" and modified == "2026-08-17":
                status = "pending_effectivity_review"
            if law.get("LawAbandonNote"):
                status = "repealed_law"
            active = status == "published"
            source = f"{name}第{number}條"
            metadata = {
                "data_type": "document",
                "document_type": "law",
                "type": "law",
                "source": source,
                "law_name": name,
                "law_code": pcode,
                "article_number": number,
                "source_url": f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={pcode}&flno={number}",
                "law_source_url": law["LawURL"],
                "source_provider": "法務部全國法規資料庫",
                "promulgated_date": modified,
                "law_modified_date": modified,
                "promulgated_date_scope": "law_latest_amendment_not_article_specific",
                "effective_date": effective,
                "effective_note": note,
                "effective_date_scope": "law_source_schedule_not_article_specific",
                "effective_date_status": "source_schedule"
                if effective
                else "see_law_history_and_schedule",
                "checked_at": checked_at,
                "source_updated_at": bulk["UpdateDate"],
                "version": f"{pcode}@{modified}",
                "corpus_version": corpus_version,
                "content_sha256": digest(text.encode()),
                "article_status": status,
                "corpus_active": active,
                "jurisdiction": "台灣",
                "chapter": chapter,
                "chunk_index": 0,
                "chunk_count": 1,
                "lookup_keys": [lookup_key(name, number)],
            }
            documents.append(
                {
                    "doc_id": f"law-{pcode}-{number}",
                    "content": f"{source}\n{text}",
                    "metadata": metadata,
                }
            )
            article_count += 1
            if status not in {"published", "deleted"}:
                pending.append({"law_name": name, "article_number": number, "reason": status})
        laws.append(
            {
                "law_name": name,
                "law_code": pcode,
                "article_count": article_count,
                "source_url": law["LawURL"],
                "promulgated_date": modified,
                "effective_date": effective,
                "effective_note": note,
                "history": history,
            }
        )
    if len({row["doc_id"] for row in documents}) != len(documents):
        raise ValueError("Duplicate official article identifiers")
    manifest = {
        "schema_version": 1,
        "source_url": OFFICIAL_URL,
        "dataset_url": DATASET_URL,
        "source_sha256": source_sha256,
        "source_updated_at": bulk["UpdateDate"],
        "checked_at": checked_at,
        "corpus_version": corpus_version,
        "document_count": len(documents),
        "active_count": sum(row["metadata"]["corpus_active"] for row in documents),
        "documents_sha256": digest(documents),
        "laws": laws,
        "withheld_articles": pending,
        "review_status": "official_source_snapshot_not_expert_case_gold",
        "date_boundary": "Law-level dates are not article-specific applicability findings; event-date and transition rules still require review.",
    }
    return documents, manifest


def apply_current_supplements(documents: list[dict], manifest: dict, supplements: list[dict]):
    """Retain separately checked effective text while a new version is pending.

    Supplements require a fresh explicit source check; they are not permanent
    overrides that could silently survive the next legal-data refresh.
    """
    by_id = {row["doc_id"]: row for row in documents}
    applied = []
    for supplement in supplements:
        if supplement["checked_at"] != manifest["checked_at"]:
            raise ValueError("Supplement source check must match this corpus check date")
        if urlsplit(supplement["source_url"]).hostname != "law.moj.gov.tw":
            raise ValueError("Supplement must reference an official MOJ source")
        row = by_id[supplement["doc_id"]]
        if row["metadata"]["article_status"] != "pending_effectivity_review":
            raise ValueError("A current supplement may only replace explicitly pending text")
        text = supplement["article_content"].strip()
        if digest(text.encode()) != supplement["content_sha256"]:
            raise ValueError("Supplement content digest mismatch")
        current = {
            "doc_id": row["doc_id"],
            "content": f"{row['metadata']['source']}\n{text}",
            "metadata": {
                **row["metadata"],
                "source_url": supplement["source_url"],
                "promulgated_date": supplement["law_modified_date"],
                "law_modified_date": supplement["law_modified_date"],
                "version": f"{row['metadata']['law_code']}@{supplement['law_modified_date']}",
                "article_status": "effective_previous_version",
                "corpus_active": True,
                "content_sha256": supplement["content_sha256"],
                "effective_date": None,
                "effective_note": supplement["effectivity_note"],
                "source_page_sha256": supplement["source_page_sha256"],
            },
        }
        row["doc_id"] += "-pending-" + row["metadata"]["law_modified_date"]
        documents.append(current)
        applied.append(
            {
                key: supplement[key]
                for key in ("doc_id", "source_url", "checked_at", "source_page_sha256")
            }
        )
    manifest.update(
        documents_sha256=digest(documents),
        document_count=len(documents),
        active_count=sum(row["metadata"]["corpus_active"] for row in documents),
        current_supplements=applied,
    )
    return documents, manifest


def audit_legacy(documents: list[dict], official: list[dict]) -> dict:
    """Report conflicts; never overwrite or delete legacy rows implicitly."""
    issues = []
    seen = {}
    official_keys = {key for row in official for key in row["metadata"].get("lookup_keys", [])}
    for doc in documents:
        metadata = doc.get("metadata") or {}
        text = doc.get("content", "")
        missing = [key for key in REQUIRED_METADATA if not metadata.get(key)]
        conflicts = []
        if "性騷擾防治法第13條" in re.sub(r"\s+", "", text) and re.search(r"一年|1年", text):
            conflicts.append("outdated_article_13_one_year_complaint_rule")
        if "性別工作平等法" in text:
            conflicts.append("legacy_law_name_and_employer_obligation_review")
        sha = digest(text.encode())
        duplicate = seen.get(sha)
        seen[sha] = doc["doc_id"]
        issues.append(
            {
                "doc_id": doc["doc_id"],
                "missing_metadata": missing,
                "conflicts": conflicts,
                "duplicate_of": duplicate,
                "action": "quarantine_review"
                if conflicts
                else "metadata_review"
                if missing
                else "keep",
            }
        )
    covered = {
        key for doc in documents for key in (doc.get("metadata") or {}).get("lookup_keys", [])
    }
    return {
        "schema_version": 1,
        "legacy_count": len(documents),
        "documents": issues,
        "official_article_keys_missing": sorted(official_keys - covered),
        "cloud_state": "not_inspected_unless_input_is_explicit_firestore_snapshot",
    }


def plan_import(documents: list[dict], existing: list[dict], collection: str, project: str) -> dict:
    if not collection or "/" in collection or not project:
        raise ValueError("An explicit project and collection are required")
    previous = {row["doc_id"]: row for row in existing}
    changes = []
    for doc in documents:
        old = previous.get(doc["doc_id"])
        old_payload = old.get("data", old) if old else None
        payload = {"content": doc["content"], "metadata": doc["metadata"]}
        old_comparable = {key: old_payload.get(key) for key in payload} if old_payload else None
        action = "unchanged" if old_comparable == payload else "update" if old else "create"
        if not doc["metadata"].get("corpus_active") and not old:
            action = "withhold"
        changes.append(
            {
                "doc_id": doc["doc_id"],
                "action": action,
                "before_sha256": digest(old_payload) if old else None,
                "payload": payload,
            }
        )
    # Only explicitly identified conflicting legacy rows are quarantined, and
    # their prior full value is captured by the apply transaction for rollback.
    legacy = [
        {"doc_id": row["doc_id"], **row.get("data", row)}
        for row in existing
        if row["doc_id"] not in {doc["doc_id"] for doc in documents}
    ]
    for issue in audit_legacy(legacy, documents)["documents"]:
        if not issue["conflicts"]:
            continue
        old = previous[issue["doc_id"]]
        old_payload = old.get("data", old)
        payload = {key: value for key, value in old_payload.items() if key != "doc_id"}
        payload["metadata"] = {
            **payload.get("metadata", {}),
            "corpus_active": False,
            "quarantine_reasons": issue["conflicts"],
        }
        changes.append(
            {
                "doc_id": issue["doc_id"],
                "action": "quarantine",
                "before_sha256": digest(old_payload),
                "payload": payload,
            }
        )
    result = {
        "schema_version": 1,
        "project": project,
        "collection": collection,
        "documents_sha256": digest(documents),
        "changes": changes,
        "counts": dict(Counter(change["action"] for change in changes)),
        "destructive_deletes": 0,
    }
    result["plan_sha256"] = digest(result)
    return result
