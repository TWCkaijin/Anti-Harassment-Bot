"""Durable, fenced jobs dispatched to this same HTTP function by Cloud Tasks.

In deployed runtimes no work continues after an HTTP response. External calls are marked before they
start; an abandoned call requires an explicit operator retry instead of silently
charging the provider twice.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from flask import Blueprint, g, has_request_context, jsonify, request

from admin_app import infrastructure
from admin_app.config import get_admin_settings

logger = logging.getLogger(__name__)
jobs_bp = Blueprint("admin_jobs", __name__)
internal_bp = Blueprint("admin_internal", __name__)
HANDLERS = {}
OBSERVERS = {}
TERMINAL = {"succeeded", "failed", "cancelled", "needs_review"}
CORPUS_JOB_KINDS = {"corpus_parse", "corpus_import", "corpus_delete"}


class JobFailure(Exception):
    def __init__(self, code, detail, *, retryable=False, http_status=422):
        super().__init__(detail)
        self.error = {
            "code": code,
            "detail": detail,
            "retryable": retryable,
            "http_status": http_status,
        }


class JobCancelled(Exception):
    pass


class LeaseLost(Exception):
    pass


class ContinueJob(Exception):
    """A handler checkpointed a bounded unit and requests another dispatch."""


def now():
    return datetime.now(UTC)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def register_handler(kind, handler):
    HANDLERS[kind] = handler


def register_observer(kind, observer):
    OBSERVERS[kind] = observer


def notify_observer(job):
    observer = OBSERVERS.get((job or {}).get("kind"))
    if observer:
        observer(job)


def job_ref(job_id):
    if (
        not isinstance(job_id, str)
        or len(job_id) != 32
        or any(c not in "0123456789abcdef" for c in job_id)
    ):
        raise JobFailure("invalid_job", "Invalid job ID", http_status=404)
    return infrastructure.private_db().collection("admin_jobs").document(job_id)


def enqueue_job(job):
    from admin_app.local_dispatch import current_dispatcher

    local = current_dispatcher()
    if local is not None:
        local.enqueue(job)
        return
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from google.protobuf.duration_pb2 import Duration

    config = get_admin_settings()
    if not all(
        (
            config.admin_tasks_queue,
            config.admin_tasks_target_url,
            config.admin_tasks_service_account,
            config.admin_tasks_audience,
        )
    ):
        raise RuntimeError("Admin queue is not configured")
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(
        config.google_cloud_project, config.admin_tasks_location, config.admin_tasks_queue
    )
    generation = job["dispatch_generation"]
    task = tasks_v2.Task(
        name=f"{parent}/tasks/j{job['job_id']}-g{generation}",
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{config.admin_tasks_target_url.rstrip('/')}/internal/jobs/{job['job_id']}/run",
            headers={"Content-Type": "application/json"},
            body=canonical({"dispatch_generation": generation}),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=config.admin_tasks_service_account,
                audience=config.admin_tasks_audience,
            ),
        ),
        dispatch_deadline=Duration(seconds=config.admin_job_lease_seconds),
    )
    with suppress(AlreadyExists):
        client.create_task(parent=parent, task=task)


def ensure_enqueued(job):
    if job.get("status") != "queued" or not job.get("dispatch_pending"):
        return
    try:
        enqueue_job(job)
    except Exception:
        # Durable queued state survives a transient Tasks outage. Poll/retry can
        # reconcile the same deterministic task name without duplicate work.
        logger.warning("Job dispatch pending job_id=%s", job["job_id"], exc_info=False)
        return
    ref = job_ref(job["job_id"])

    def mark(transaction):
        current = ref.get(transaction=transaction).to_dict()
        if (
            current
            and current.get("dispatch_generation") == job["dispatch_generation"]
            and current.get("status") == "queued"
        ):
            transaction.update(ref, {"dispatch_pending": False})

    infrastructure.atomic(mark)


def _request_session_id():
    session = getattr(g, "admin_session", None) if has_request_context() else None
    session_id = session.get("session_id") if isinstance(session, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise JobFailure("authentication_required", "Authentication required", http_status=401)
    return session_id


def _check_job_access(job):
    """Jobs stay in one environment; diagnostics also belong to one session."""
    if job.get("environment") != get_admin_settings().environment:
        raise JobFailure("job_not_found", "Job not found", http_status=404)
    if job.get("kind") != "chat_test":
        return
    session_id = _request_session_id()
    owner = job.get("owner_session_id")
    # Old unbound diagnostics are deliberately unavailable through the HTTP API.
    # Returning the ordinary not-found response also avoids revealing expiry or
    # whether another session's guessed job ID exists.
    if not isinstance(owner, str) or not hmac.compare_digest(owner, session_id):
        raise JobFailure("job_not_found", "Job not found", http_status=404)


def submit_job(kind, payload, snapshot=None, idempotency_key=None):
    from admin_app.local_dispatch import current_dispatcher

    if kind not in HANDLERS:
        raise JobFailure("unknown_job_kind", "Unsupported job type")
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200
    ):
        raise JobFailure("invalid_idempotency_key", "Invalid Idempotency-Key")
    config = get_admin_settings()
    owner_session_id = (
        _request_session_id() if kind == "chat_test" and has_request_context() else None
    )
    envelope = {"payload": payload, "snapshot": snapshot}
    content = canonical(envelope)
    fingerprint = hashlib.sha256(content).hexdigest()
    job_id = (
        hashlib.sha256(f"{config.environment}:{kind}:{idempotency_key}".encode()).hexdigest()[:32]
        if idempotency_key
        else uuid.uuid4().hex
    )
    ref = job_ref(job_id)
    existing = ref.get().to_dict()
    if existing:
        if has_request_context():
            _check_job_access(existing)
        if existing["expires_at"] <= now():
            raise JobFailure(
                "job_expired", "工作已過期，請使用新的 Idempotency-Key。", http_status=410
            )
        if existing["fingerprint"] != fingerprint:
            raise JobFailure(
                "idempotency_conflict",
                "This Idempotency-Key already identifies different input",
                http_status=409,
            )
        ensure_enqueued(existing)
        return existing
    input_ref = None
    if len(content) > 200000:
        input_ref = infrastructure.object_store().put_bytes(
            f"jobs/{kind}/{job_id}/input-{uuid.uuid4().hex}.json", content, "application/json"
        )
    current = now()
    job = {
        "job_id": job_id,
        "kind": kind,
        "status": "queued",
        "environment": config.environment,
        "fingerprint": fingerprint,
        "input_ref": input_ref,
        "input": envelope if input_ref is None else None,
        "created_at": current,
        "updated_at": current,
        "expires_at": current
        + timedelta(seconds=86400 if kind == "chat_test" else config.admin_job_retention_seconds),
        "dispatch_generation": 0,
        "dispatch_pending": True,
        "attempt": 0,
        "checkpoint": {},
        "progress": {},
        "cancel_requested": False,
        "external_call": None,
        "result": None,
        "result_ref": None,
        "error": None,
        "submitted_by": getattr(g, "admin_identity", "system")
        if has_request_context()
        else "system",
        "owner_session_id": owner_session_id,
    }
    local = current_dispatcher()
    if local is not None:
        job["local_dispatch_id"] = local.id

    def create(transaction):
        current_job = ref.get(transaction=transaction).to_dict()
        if current_job:
            if has_request_context():
                _check_job_access(current_job)
            if current_job["fingerprint"] != fingerprint:
                raise JobFailure(
                    "idempotency_conflict",
                    "This Idempotency-Key already identifies different input",
                    http_status=409,
                )
            return current_job
        transaction.set(ref, job)
        return job

    try:
        actual = infrastructure.atomic(create)
    except Exception:
        if input_ref:
            infrastructure.object_store().delete(input_ref)
        raise
    if actual.get("input_ref") != input_ref and input_ref:
        infrastructure.object_store().delete(input_ref)
    ensure_enqueued(actual)
    return actual


class JobContext:
    def __init__(self, job_id, lease_token):
        self.job_id, self.lease_token = job_id, lease_token

    def _mutate(self, changes, *, allow_cancel=False):
        ref, current_time = job_ref(self.job_id), now()

        def update(transaction):
            job = ref.get(transaction=transaction).to_dict()
            if (
                not job
                or job.get("lease_token") != self.lease_token
                or job.get("status") != "running"
                or job["lease_until"] <= current_time
            ):
                raise LeaseLost()
            if job.get("cancel_requested") and not allow_cancel:
                raise JobCancelled()
            transaction.update(ref, {**changes, "updated_at": current_time})
            return job

        return infrastructure.atomic(update)

    def checkpoint(self, data):
        if len(canonical(data)) > 200000:
            raise ValueError("Job checkpoint must remain small; use immutable object storage")
        self._mutate({"checkpoint": data})

    def progress(self, data):
        self._mutate({"progress": data})

    def ensure_active(self):
        self._mutate({})

    def external_call_started(self, label="model"):
        marker = {"label": str(label)[:80], "started_at": now()}
        self._mutate({"external_call": marker})
        return marker

    def external_call_finished(self, checkpoint=None):
        # Persist the known outcome even when cancellation arrived mid-request.
        changes = {"external_call": None}
        if checkpoint is not None:
            if len(canonical(checkpoint)) > 200000:
                raise ValueError("External result checkpoint must use object storage when large")
            changes["checkpoint"] = checkpoint
        self._mutate(changes, allow_cancel=True)

    def finish(self, status, *, result=None, error=None):
        result_ref = None
        if result is not None and len(canonical(result)) > 200000:
            kind = job_ref(self.job_id).get().to_dict()["kind"]
            result_ref = infrastructure.object_store().put_bytes(
                f"jobs/{kind}/{self.job_id}/result-{self.lease_token}.json",
                canonical(result),
                "application/json",
            )
        self._mutate(
            {
                "status": status,
                "result": None if result_ref else result,
                "result_ref": result_ref,
                "error": error,
                "lease_token": None,
                "lease_until": None,
            },
            allow_cancel=True,
        )
        notify_observer(job_ref(self.job_id).get().to_dict())


def _unknown_error():
    return {
        "code": "external_outcome_unknown",
        "detail": "外部請求的結果無法確認；請檢查後明確重試，可能產生重複費用。",
        "retryable": False,
        "http_status": 502,
    }


def _claim(job_id, generation):
    ref, current_time = job_ref(job_id), now()
    config = get_admin_settings()

    def claim(transaction):
        job = ref.get(transaction=transaction).to_dict()
        if not job:
            raise JobFailure("job_not_found", "Job not found", http_status=404)
        if job.get("environment") != config.environment:
            raise JobFailure("wrong_environment", "Job environment mismatch", http_status=403)
        if job["dispatch_generation"] != generation or job["status"] in TERMINAL:
            return None
        if job["expires_at"] <= current_time:
            transaction.update(
                ref,
                {
                    "status": "failed",
                    "error": {"code": "job_expired", "detail": "工作已過期。"},
                    "updated_at": current_time,
                },
            )
            return None
        if job["status"] == "running" and job["lease_until"] > current_time:
            raise JobFailure(
                "job_busy", "Worker lease is still active", retryable=True, http_status=429
            )
        if job.get("external_call"):
            transaction.update(
                ref,
                {"status": "needs_review", "error": _unknown_error(), "updated_at": current_time},
            )
            return None
        if job.get("cancel_requested"):
            transaction.update(ref, {"status": "cancelled", "updated_at": current_time})
            return None
        token = secrets.token_hex(16)
        changes = {
            "status": "running",
            "lease_token": token,
            "lease_until": current_time + timedelta(seconds=config.admin_job_lease_seconds),
            "attempt": job["attempt"] + 1,
            "updated_at": current_time,
        }
        transaction.update(ref, changes)
        return {**job, **changes}

    return infrastructure.atomic(claim)


def execute_job(job_id, generation):
    job = _claim(job_id, generation)
    if job is None:
        notify_observer(job_ref(job_id).get().to_dict())
        return
    context = JobContext(job_id, job["lease_token"])
    try:
        envelope = (
            json.loads(infrastructure.object_store().read_bytes(job["input_ref"]))
            if job.get("input_ref")
            else job["input"]
        )
        job.update(envelope)
        result = HANDLERS[job["kind"]](job, context)
        context.ensure_active()
        current = job_ref(job_id).get().to_dict()
        if current.get("external_call"):
            raise JobFailure("unclosed_external_call", "External operation requires review")
        context.finish("succeeded", result=result)
    except ContinueJob:
        current = job_ref(job_id).get().to_dict()
        if current.get("external_call"):
            context.finish("needs_review", error=_unknown_error())
            return
        try:
            context._mutate(
                {
                    "status": "queued",
                    "dispatch_generation": generation + 1,
                    "dispatch_pending": True,
                    "lease_token": None,
                    "lease_until": None,
                }
            )
        except JobCancelled:
            context.finish("cancelled")
            return
        ensure_enqueued(job_ref(job_id).get().to_dict())
    except JobCancelled:
        current = job_ref(job_id).get().to_dict()
        context.finish(
            "needs_review" if current.get("external_call") else "cancelled",
            error=_unknown_error() if current.get("external_call") else None,
        )
    except LeaseLost:
        # A stale worker cannot commit a result after another worker owns it.
        return
    except Exception as exc:
        current = job_ref(job_id).get().to_dict()
        if current.get("lease_token") != context.lease_token:
            return
        uncertain = bool(current.get("external_call"))
        error = (
            _unknown_error()
            if uncertain
            else exc.error
            if isinstance(exc, JobFailure)
            else {
                "code": "job_failed",
                "detail": "工作執行失敗，請檢查後重試。",
                "retryable": False,
                "http_status": 500,
            }
        )
        context.finish("needs_review" if uncertain else "failed", error=error)
        logger.warning("Job stopped job_id=%s code=%s", job_id, error["code"])


def verify_worker_request():
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import verify_oauth2_token

    config = get_admin_settings()
    authorization = request.headers.get("Authorization", "")
    if (
        not authorization.startswith("Bearer ")
        or not config.admin_tasks_audience
        or not config.admin_tasks_service_account
    ):
        return False
    try:
        claims = verify_oauth2_token(
            authorization[7:], Request(), audience=config.admin_tasks_audience
        )
        return (
            claims.get("email") == config.admin_tasks_service_account
            and claims.get("email_verified") is True
        )
    except Exception:
        return False


@internal_bp.post("/jobs/<job_id>/run")
def run_job(job_id):
    if not verify_worker_request():
        return jsonify({"detail": "Worker authentication required"}), 401
    data = request.get_json(silent=True)
    generation = data.get("dispatch_generation") if isinstance(data, dict) else None
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        return jsonify({"detail": "Invalid task generation"}), 422
    execute_job(job_id, generation)
    return "", 204


def public_job(job):
    if has_request_context():
        _check_job_access(job)
    result = job.get("result")
    if job.get("result_ref"):
        result = json.loads(infrastructure.object_store().read_bytes(job["result_ref"]))
    payload = (job.get("input") or {}).get("payload") or {}
    targets = (
        {
            name: payload[name]
            for name in ("import_id", "source_id")
            if isinstance(payload.get(name), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", payload[name])
        }
        if job.get("kind") in CORPUS_JOB_KINDS and isinstance(payload, dict)
        else {}
    )
    return {
        "job_id": job["job_id"],
        "kind": job.get("kind"),
        "status": job["status"],
        "result": result,
        "error": job.get("error"),
        "progress": job.get("progress", {}),
        "dispatch_pending": job.get("dispatch_pending", False),
        "cancel_requested": bool(job.get("cancel_requested")),
        "created_at": job["created_at"].isoformat() if job.get("created_at") else None,
        "updated_at": job["updated_at"].isoformat() if job.get("updated_at") else None,
        **targets,
    }


def list_corpus_jobs(*, limit=50, cursor=None):
    """Bounded newest-first history, excluding diagnostic inputs and results."""
    from google.cloud.firestore_v1.base_query import FieldFilter

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise JobFailure("invalid_page", "limit 必須介於 1 與 100", http_status=400)
    environment = get_admin_settings().environment
    scope = f"corpus-jobs:{environment}"
    collection = infrastructure.private_db().collection("admin_jobs")
    query = (
        collection.where(filter=FieldFilter("environment", "==", environment))
        .where(filter=FieldFilter("kind", "in", sorted(CORPUS_JOB_KINDS)))
        .order_by("created_at", direction="DESCENDING")
        .order_by("__name__", direction="DESCENDING")
    )
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError("cursor length")
            value = json.loads(
                base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            )
            if value["scope"] != scope:
                raise ValueError("cursor scope")
            job_id = value["job_id"]
            if (
                not isinstance(job_id, str)
                or len(job_id) != 32
                or any(char not in "0123456789abcdef" for char in job_id)
            ):
                raise ValueError("cursor ID")
            created_at = datetime.fromisoformat(value["created_at"])
            if created_at.tzinfo is None:
                raise ValueError("cursor timestamp")
        except Exception as exc:
            raise JobFailure("invalid_cursor", "分頁游標無效", http_status=400) from exc
        query = query.start_after(
            {"created_at": created_at, "__name__": collection.document(job_id)}
        )
    snapshots = list(query.limit(limit + 1).stream())
    page = snapshots[:limit]
    items = []
    for snapshot in page:
        job = snapshot.to_dict()
        # Repeat the boundary before public_job can read a result object. This
        # also makes an accidentally broadened future query fail closed.
        if (
            job.get("environment") == environment
            and job.get("kind") in CORPUS_JOB_KINDS
            and job["expires_at"] > now()
        ):
            items.append(public_job(job))
    next_cursor = None
    if len(snapshots) > limit:
        last = page[-1]
        next_cursor = (
            base64.urlsafe_b64encode(
                canonical(
                    {
                        "scope": scope,
                        "created_at": last.to_dict()["created_at"].isoformat(),
                        "job_id": last.id,
                    }
                )
            )
            .decode()
            .rstrip("=")
        )
    return {"items": items, "next_cursor": next_cursor}


def get_job(job_id, *, internal=False):
    ref = job_ref(job_id)

    def reconcile(transaction):
        job = ref.get(transaction=transaction).to_dict()
        if job and has_request_context() and not internal:
            _check_job_access(job)
        if job and job["expires_at"] <= now():
            return job
        if job and job["status"] == "running" and job["lease_until"] <= now():
            changes = {"lease_token": None, "lease_until": None, "updated_at": now()}
            if job.get("external_call"):
                changes.update(status="needs_review", error=_unknown_error())
            elif job.get("cancel_requested"):
                changes.update(status="cancelled")
            else:
                changes.update(
                    status="queued",
                    dispatch_pending=True,
                    dispatch_generation=job["dispatch_generation"] + 1,
                )
            transaction.update(ref, changes)
            job.update(changes)
        return job

    job = infrastructure.atomic(reconcile)
    if not job:
        raise JobFailure("job_not_found", "Job not found", http_status=404)
    notify_observer(job)
    if job["expires_at"] <= now():
        raise JobFailure("job_expired", "工作已過期，請建立新的工作。", http_status=410)
    ensure_enqueued(job)
    return job


def reconcile_jobs(*, limit=100):
    """Recover a bounded oldest-first batch without depending on an open browser."""
    from google.cloud.firestore_v1.base_query import FieldFilter

    recovered = 0
    examined = 0
    for status in ("queued", "running"):
        query = (
            infrastructure.private_db()
            .collection("admin_jobs")
            .where(filter=FieldFilter("status", "==", status))
            .where(filter=FieldFilter("environment", "==", get_admin_settings().environment))
            .order_by("updated_at")
            .limit(limit)
        )
        for snapshot in query.stream():
            examined += 1
            before = snapshot.to_dict()
            try:
                job = get_job(snapshot.id, internal=True)
            except JobFailure as exc:
                if exc.error["code"] == "job_expired":
                    continue
                raise
            if (
                job["status"] == "queued"
                and not job.get("dispatch_pending")
                and job["updated_at"] < now() - timedelta(minutes=30)
            ):
                # Tasks has finite retries. An undelivered queued job can outlive
                # its task; a fresh generation restores delivery with the same
                # transactional lease protecting against any older duplicate.
                def redrive(
                    transaction, ref=snapshot.reference, generation=job["dispatch_generation"]
                ):
                    current = ref.get(transaction=transaction).to_dict()
                    if (
                        current
                        and current.get("environment") == get_admin_settings().environment
                        and current["status"] == "queued"
                        and current["dispatch_generation"] == generation
                    ):
                        changes = {
                            "dispatch_generation": current["dispatch_generation"] + 1,
                            "dispatch_pending": True,
                            "updated_at": now(),
                        }
                        transaction.update(ref, changes)
                        return {**current, **changes}
                    return None

                renewed = infrastructure.atomic(redrive)
                if renewed:
                    ensure_enqueued(renewed)
                    recovered += 1
            elif job["status"] != before["status"] or before.get("dispatch_pending"):
                recovered += 1
    return {"examined": examined, "recovered": recovered}


@internal_bp.post("/maintenance/reconcile")
def scheduled_reconcile():
    if not verify_worker_request():
        return jsonify({"detail": "Worker authentication required"}), 401
    from admin_app.maintenance import cleanup_expired_previews

    result = reconcile_jobs()
    result["expired_previews_cleaned"] = cleanup_expired_previews()
    return jsonify(result)


@jobs_bp.get("/jobs/<job_id>")
@jobs_bp.get("/test-jobs/<job_id>")
def job_status(job_id):
    return jsonify(public_job(get_job(job_id)))


@jobs_bp.post("/jobs/<job_id>/cancel")
@jobs_bp.post("/test-jobs/<job_id>/cancel")
def cancel_job(job_id):
    ref = job_ref(job_id)

    def cancel(transaction):
        job = ref.get(transaction=transaction).to_dict()
        if not job:
            raise JobFailure("job_not_found", "Job not found", http_status=404)
        _check_job_access(job)
        if job["expires_at"] <= now():
            raise JobFailure("job_expired", "工作已過期，請建立新的工作。", http_status=410)
        if job["status"] not in TERMINAL:
            changes = {"cancel_requested": True, "updated_at": now()}
            if job["status"] == "queued":
                changes["status"] = "cancelled"
            transaction.update(ref, changes)

    infrastructure.atomic(cancel)
    return jsonify(public_job(get_job(job_id)))


@jobs_bp.post("/jobs/<job_id>/retry")
@jobs_bp.post("/test-jobs/<job_id>/retry")
def retry_job(job_id):
    data = request.get_json(silent=True) or {}
    ref = job_ref(job_id)

    def retry(transaction):
        job = ref.get(transaction=transaction).to_dict()
        if not job:
            raise JobFailure("job_not_found", "Job not found", http_status=404)
        _check_job_access(job)
        if job["expires_at"] <= now():
            raise JobFailure("job_expired", "工作已過期，請建立新的工作。", http_status=410)
        if job["status"] not in {"failed", "needs_review", "queued"}:
            raise JobFailure("job_not_retryable", "Job is not ready for retry", http_status=409)
        if job["status"] == "needs_review" and data.get("acknowledge_duplicate_cost") is not True:
            raise JobFailure(
                "retry_confirmation_required",
                "Explicit duplicate-cost acknowledgement is required",
                http_status=409,
            )
        changes = {
            "status": "queued",
            "error": None,
            "external_call": None,
            "cancel_requested": False,
            "dispatch_pending": True,
            "updated_at": now(),
        }
        if job["status"] != "queued":
            changes["dispatch_generation"] = job["dispatch_generation"] + 1
            if job["kind"] == "chat_test":
                changes["checkpoint"] = {}
        transaction.update(ref, changes)

    infrastructure.atomic(retry)
    job = get_job(job_id)
    return jsonify(public_job(job)), 202
