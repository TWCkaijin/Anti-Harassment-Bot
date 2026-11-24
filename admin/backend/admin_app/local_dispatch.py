"""Opt-in development dispatch for jobs created by this API process only.

Never scan the shared dev database. Durable leases and external-call fences
remain owned by jobs.execute_job, exactly as with the deployed worker.
"""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import current_app, has_app_context

logger = logging.getLogger(__name__)


class LocalDispatcher:
    def __init__(self, app):
        self.app = app
        self.id = uuid.uuid4().hex
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="admin-local-job")
        self.lock = threading.Lock()
        self.pending = set()

    def enqueue(self, job):
        if job.get("local_dispatch_id") != self.id:
            raise RuntimeError("Job belongs to another local run; use the explicit worker CLI")
        key = (job["job_id"], job["dispatch_generation"])
        with self.lock:
            if key in self.pending:
                return
            if len(self.pending) >= 20:
                raise RuntimeError("Local worker is busy")
            self.pending.add(key)
        try:
            self.pool.submit(self._execute, key)
        except Exception:
            with self.lock:
                self.pending.discard(key)
            raise

    def _execute(self, key):
        from admin_app.jobs import execute_job

        try:
            with self.app.app_context():
                execute_job(*key)
        except Exception:
            logger.warning("Local job interrupted job_id=%s", key[0])
        finally:
            with self.lock:
                self.pending.discard(key)


def current_dispatcher():
    return current_app.extensions.get("admin_local_dispatcher") if has_app_context() else None


def install(app):
    from admin_app.worker_cli import ensure_local_configuration

    ensure_local_configuration()
    app.extensions["admin_local_dispatcher"] = LocalDispatcher(app)
