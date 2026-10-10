"""Run durable development jobs locally; never install an unauthenticated HTTP worker.

Use the same development environment/database settings as the local admin API.
Cloud Tasks settings must be empty. With --once (the default), process one bounded
batch; checkpoint continuations wait for the next invocation or polling pass.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime

from flask import has_app_context
from pydantic import ValidationError

from admin_app import infrastructure, jobs
from admin_app.config import get_admin_settings
from admin_app.main import create_app
from rag_core.core.config import get_settings


def ensure_local_configuration():
    """Reject deployed modes and accidental dispatch from local work to cloud."""
    config = get_admin_settings()
    if config.environment != "development":
        raise ValueError("Local worker requires ENVIRONMENT=development")
    if any(os.getenv(name) for name in ("K_SERVICE", "FUNCTION_TARGET", "FUNCTION_NAME")):
        raise ValueError("Local worker cannot run inside a deployed Function or Cloud Run service")
    shared = get_settings()
    if shared.environment != "development":
        raise ValueError("Shared runtime must also use ENVIRONMENT=development")
    if not os.getenv("FIRESTORE_EMULATOR_HOST") and (
        config.admin_private_database_id != "admin-private-dev"
        or shared.firestore_database_id != "app-dev"
    ):
        raise ValueError("Local worker requires app-dev and admin-private-dev databases")
    if any(
        (
            config.admin_tasks_queue,
            config.admin_tasks_target_url,
            config.admin_tasks_service_account,
            config.admin_tasks_audience,
        )
    ):
        raise ValueError(
            "Clear ADMIN_TASKS_QUEUE, ADMIN_TASKS_TARGET_URL, ADMIN_TASKS_SERVICE_ACCOUNT "
            "and ADMIN_TASKS_AUDIENCE for both the local API and local worker"
        )
    return config


def run_once(*, limit=20):
    """Execute at most limit local jobs, retaining all existing lease/cost checks."""
    from google.cloud.firestore_v1.base_query import FieldFilter

    ensure_local_configuration()
    if not has_app_context():
        raise ValueError("Local worker requires the admin application context")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Job limit must be between 1 and 100")
    current_time = jobs.now()
    examined, candidates = 0, []
    for status in ("queued", "running"):
        # Equality filters use Firestore index merging. Avoid querying all
        # environments and then letting older preview jobs consume the limit.
        query = (
            infrastructure.private_db()
            .collection("admin_jobs")
            .where(filter=FieldFilter("environment", "==", "development"))
            .where(filter=FieldFilter("status", "==", status))
            .limit(limit)
        )
        for snapshot in query.stream():
            examined += 1
            job = snapshot.to_dict()
            if not job or job.get("environment") != "development" or job.get("status") != status:
                continue
            if status == "running":
                lease_until = job.get("lease_until")
                if not isinstance(lease_until, datetime) or lease_until > current_time:
                    continue
            candidates.append((snapshot.id, job))
    candidates.sort(key=lambda item: (item[1].get("updated_at", current_time), item[0]))
    results = []
    for job_id, job in candidates[:limit]:
        try:
            jobs.execute_job(job_id, job["dispatch_generation"])
        except jobs.JobFailure as exc:
            # Another local process may have claimed, removed, or changed the
            # record after this bounded scan. Never retry it in the same pass.
            results.append({"job_id": job_id, "status": "skipped", "code": exc.error["code"]})
            continue
        current = jobs.job_ref(job_id).get().to_dict()
        results.append({"job_id": job_id, "status": current["status"] if current else "removed"})
    return {
        "environment": "development",
        "examined": examined,
        "processed": len(results),
        "statuses": dict(Counter(result["status"] for result in results)),
        "jobs": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process one bounded batch (default)")
    mode.add_argument("--poll-seconds", type=float, help="Repeat batches every 0.1 to 60 seconds")
    parser.add_argument("--limit", type=int, default=20, help="Maximum jobs per pass, 1 to 100")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    if args.poll_seconds is not None and not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")
    try:
        ensure_local_configuration()
    except ValidationError:
        # Settings validation may include model credentials in its input repr.
        parser.error("Invalid local worker configuration; check development runtime settings")
    except ValueError as exc:
        parser.error(str(exc))
    app = create_app()
    try:
        with app.app_context():
            while True:
                print(json.dumps(run_once(limit=args.limit), ensure_ascii=False), flush=True)
                if args.poll_seconds is None:
                    break
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
