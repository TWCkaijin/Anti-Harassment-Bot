"""Bounded cleanup of expired, never-confirmed import previews."""

import hashlib
from datetime import timedelta

from admin_app import infrastructure
from admin_app.config import get_admin_settings
from admin_app.jobs import now

UNCONFIRMED_STATES = {
    "parse_queued",
    "parsing",
    "mapping_required",
    "parse_failed",
    "preview",
    "expired_cleanup",
}


def _embedding_job_ids(draft):
    """Also protect an import committed before its response linked the job ID."""
    result = {draft["job_id"]} if draft.get("job_id") else set()
    if draft.get("plan_hash"):
        config = get_admin_settings()
        environments = {draft.get("environment") or config.environment}
        if not draft.get("environment") and config.admin_private_database_id == "admin-private-dev":
            environments.update({"development", "preview"})
        for environment in environments:
            key = f"{environment}:corpus_import:corpus-import:{draft['import_id']}:{draft['plan_hash']}"
            result.add(hashlib.sha256(key.encode()).hexdigest()[:32])
    return result


def _draft_assets(draft):
    prefix = f"corpus/imports/{draft['import_id']}/"
    references = [draft.get("original"), draft.get("preview"), *draft.get("draft_assets", [])]
    revision = draft.get("cleanup_parse_revision", draft.get("parse_revision"))
    if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1:
        if revision > 20:
            raise ValueError("Draft parse revision exceeds the supported cleanup bound")
        # Earlier parser attempts can die after upload but before linking their
        # output, then be replaced by a new mapping revision. Mapping is capped
        # at 20 revisions, so all possible immutable preview keys remain bounded.
        for attempt in range(1, revision + 1):
            references.append(
                infrastructure.object_store().lookup_reference(f"{prefix}preview-{attempt}.json")
            )
    unique = {}
    for reference in references:
        if not reference:
            continue
        if not reference.get("name", "").startswith(prefix):
            raise ValueError("Draft asset is outside its owned import prefix")
        key = (reference.get("bucket"), reference["name"], str(reference.get("generation")))
        unique[key] = reference
    return list(unique.values())


def cleanup_expired_previews(*, limit=20):
    from google.cloud.firestore_v1.base_query import FieldFilter

    db = infrastructure.private_db()
    cleaned = 0
    claimed = 0
    # The grace exceeds the deployed 15-minute request deadline, so an earlier
    # confirm request cannot still be in-flight when an expired draft is claimed.
    cutoff = now() - timedelta(hours=1)
    for status in sorted(UNCONFIRMED_STATES):
        snapshots = (
            db.collection("corpus_imports")
            .where(filter=FieldFilter("status", "==", status))
            .order_by("expires_at")
            .limit(limit)
            .stream()
        )
        for snapshot in snapshots:
            if snapshot.to_dict()["expires_at"] > cutoff:
                continue

            def claim(transaction, ref=snapshot.reference):
                draft = ref.get(transaction=transaction).to_dict()
                if (
                    not draft
                    or draft.get("status") not in UNCONFIRMED_STATES
                    or draft["expires_at"] > cutoff
                ):
                    return None
                assets = (
                    db.collection("corpus_source_assets")
                    .document(draft["source_id"])
                    .get(transaction=transaction)
                    if draft.get("source_id")
                    else None
                )
                embedding_jobs = [
                    db.collection("admin_jobs").document(job_id).get(transaction=transaction)
                    for job_id in _embedding_job_ids(draft)
                ]
                parse_ref = (
                    db.collection("admin_jobs").document(draft["parse_job_id"])
                    if draft.get("parse_job_id")
                    else None
                )
                parse_job = parse_ref.get(transaction=transaction).to_dict() if parse_ref else None
                if (assets and assets.exists) or any(job.exists for job in embedding_jobs):
                    return None
                if parse_job:
                    if parse_job.get("kind") != "corpus_parse" or (
                        draft.get("environment")
                        and parse_job.get("environment") != draft["environment"]
                    ):
                        return None
                    if parse_job.get("status") == "running" and (
                        not parse_job.get("lease_until") or parse_job["lease_until"] > now()
                    ):
                        return None
                    if parse_job.get("status") in {"queued", "running"}:
                        # The same transaction fences any older worker before
                        # deleting its input. A concurrent valid claim retries
                        # this transaction and is then skipped while leased.
                        transaction.update(
                            parse_ref,
                            {
                                "status": "cancelled",
                                "cancel_requested": True,
                                "dispatch_pending": False,
                                "dispatch_generation": parse_job["dispatch_generation"] + 1,
                                "lease_token": None,
                                "lease_until": None,
                                "updated_at": now(),
                                "error": {"code": "preview_expired", "detail": "匯入草稿已過期。"},
                            },
                        )
                changes = {"status": "expired_cleanup"}
                if draft.get("status") != "expired_cleanup" and "parse_revision" in draft:
                    changes.update(
                        cleanup_parse_revision=draft["parse_revision"],
                        parse_revision=draft["parse_revision"] + 1,
                    )
                transaction.update(ref, changes)
                return {**draft, **changes}

            draft = infrastructure.atomic(claim)
            if draft is None:
                continue
            claimed += 1
            for reference in _draft_assets(draft):
                infrastructure.object_store().delete(reference)

            def finish(transaction, ref=snapshot.reference, expected=draft):
                current = ref.get(transaction=transaction).to_dict()
                if (
                    not current
                    or current.get("status") != "expired_cleanup"
                    or current.get("parse_revision") != expected.get("parse_revision")
                ):
                    return False
                transaction.update(
                    ref,
                    {
                        "status": "expired",
                        "original": None,
                        "preview": None,
                        "draft_assets": [],
                        "cleanup_expires_at": now() + timedelta(days=7),
                    },
                )
                return True

            cleaned += int(infrastructure.atomic(finish))
            if claimed >= limit:
                return cleaned
    return cleaned
