"""Exercise the deployed preview corpus using only a new synthetic two-row upload.

The default prints a plan without reading credentials or using the network.
--apply logs in with a local password, parses/maps/confirms the synthetic upload,
checks stored native-vector retrieval, downloads its original, then deletes the
whole generated source and verifies its exact Storage generations are gone.
No production database, preexisting source, or IAM policy is modified. Passwords,
cookies, access tokens, document contents, and vectors never enter the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

try:
    from scripts.verify_runtime_iam import PROJECT, ProbeError, access_token, write_report
except ModuleNotFoundError:
    from verify_runtime_iam import PROJECT, ProbeError, access_token, write_report

BASE_URL = "https://anti-harassment-bot-admin--dev-preview-q53x908r.web.app"
BUCKET = "anti-harassment-bot-admin-dev"
BUSINESS_DATABASE = "app-dev"
PRIVATE_DATABASE = "admin-private-dev"
API = "/api/v1/admin"
TERMINAL = {"succeeded", "failed", "cancelled", "needs_review"}


class SmokeError(RuntimeError):
    """Fixed diagnostic strings only; never expose raw HTTP or provider errors."""


def validate_base_url(value):
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not re.fullmatch(
            r"anti-harassment-bot-admin--dev-preview-[a-z0-9]+\.web\.app",
            parsed.netloc,
        )
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SmokeError("preview_host_required")
    return value.rstrip("/")


def synthetic_fixture(run_id):
    rows = [
        {
            "body_text": (
                f"Synthetic preview corpus fixture item {letter}. "
                "This exists only to verify import, retrieval, and deletion."
            ),
            "extra_json": {"synthetic": True, "fixture_item": letter},
        }
        for letter in ("A", "B")
    ]
    return (
        rows,
        json.dumps(rows, ensure_ascii=False).encode(),
        {
            "synthetic": True,
            "smoke_run_id": run_id,
        },
    )


def smoke_plan(base_url):
    return {
        "base_url": validate_base_url(base_url),
        "project": PROJECT,
        "business_database": BUSINESS_DATABASE,
        "private_database": PRIVATE_DATABASE,
        "bucket": BUCKET,
        "network_calls": False,
        "upload": "generated JSON with two fixed nonpersonal synthetic paragraphs",
        "provider_calls": "only embedding the two confirmed synthetic chunks",
        "steps": [
            "verify preview health and log in",
            "queue structured inspection, require explicit field mapping",
            "queue parsed preview and verify no embedding stage before confirmation",
            "confirm idempotently, wait for publication, replay confirmation",
            "paginate synthetic source, verify native 1024-vector and findNearest",
            "download and byte-compare synthetic original",
            "delete generated source and verify chunks, draft, and exact object generations gone",
            "repeat findNearest with the stored synthetic vector and log out",
        ],
    }


class CorpusSmoke:
    def __init__(self, base_url, password_file, *, job_timeout=300):
        self.base_url = validate_base_url(base_url)
        self.password_file = Path(password_file)
        self.job_timeout = job_timeout
        self.client = httpx.Client(timeout=60, follow_redirects=False)
        self.observer = httpx.Client(timeout=30, follow_redirects=False)
        self.csrf = None
        self.import_id = None
        self.import_job_id = None
        self.source_id = None
        self.chunk_ids = set()
        self.storage_objects = {}
        self.query_vector = None
        self.query_vectors = {}
        self.business = None
        self.private = None
        self.token = None
        self.phase = "initialization"
        self.report = {
            "run_id": uuid.uuid4().hex,
            "base_url": self.base_url,
            "business_database": BUSINESS_DATABASE,
            "private_database": PRIVATE_DATABASE,
            "checks": [],
            "http_requests": [],
            "passed": False,
        }
        self.filename = f"synthetic-{self.report['run_id']}.json"
        self.title = f"Synthetic smoke {self.report['run_id']}"

    def check(self, label, condition, **evidence):
        if not condition:
            raise SmokeError(label)
        row = {"check": label, "passed": True, **evidence}
        self.report["checks"].append(row)
        print(json.dumps(row), flush=True)

    def request(self, method, path, *, expected=(200,), **kwargs):
        headers = {"Origin": self.base_url}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        started = time.monotonic()
        evidence = {
            "timestamp": datetime.now(UTC).isoformat(),
            "method": method,
            "path": path,
        }
        try:
            response = self.client.request(
                method, self.base_url + API + path, headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            evidence.update(status=None, elapsed_ms=round((time.monotonic() - started) * 1000))
            self.report["http_requests"].append(evidence)
            self.report.setdefault("http_failure", evidence)
            raise SmokeError("admin_transport_failed") from exc
        evidence.update(
            status=response.status_code,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        evidence["content_type"] = (
            content_type
            if content_type
            in {"application/json", "text/plain", "text/html", "application/octet-stream"}
            else "other"
        )
        trace = response.headers.get("x-cloud-trace-context", "")
        if re.fullmatch(r"[a-fA-F0-9]{32}(?:/\d+(?:;o=[01])?)?", trace):
            evidence["trace"] = trace
        self.report["http_requests"].append(evidence)
        if response.status_code not in expected:
            if content_type == "application/json":
                try:
                    error_id = response.json().get("error_id")
                    if isinstance(error_id, str) and re.fullmatch(r"[a-f0-9]{32}", error_id):
                        evidence["error_id"] = error_id
                except (ValueError, AttributeError):
                    pass
            self.report.setdefault("http_failure", evidence)
            raise SmokeError(f"unexpected_admin_status_{response.status_code}")
        return response

    def json_request(self, method, path, **kwargs):
        response = self.request(method, path, **kwargs)
        try:
            result = response.json()
        except ValueError as exc:
            raise SmokeError("invalid_admin_json") from exc
        if not isinstance(result, dict):
            raise SmokeError("invalid_admin_object")
        return result

    def wait_job(self, job_id, expected_kind, *, require_success=True):
        deadline = time.monotonic() + self.job_timeout
        while time.monotonic() < deadline:
            job = self.json_request("GET", f"/jobs/{job_id}")
            if job.get("kind") != expected_kind:
                raise SmokeError("unexpected_job_kind")
            if job.get("status") in TERMINAL:
                if require_success and job["status"] != "succeeded":
                    code = (job.get("error") or {}).get("code", "unknown")
                    safe_code = code if re.fullmatch(r"[a-z_]{1,64}", str(code)) else "unknown"
                    raise SmokeError(f"job_{job['status']}_{safe_code}")
                return job
            time.sleep(2)
        raise SmokeError("job_wait_timeout")

    def connect_observer(self):
        from google.cloud import firestore
        from google.oauth2.credentials import Credentials

        self.token = access_token()
        credentials = Credentials(self.token)
        self.business = firestore.Client(
            project=PROJECT, database=BUSINESS_DATABASE, credentials=credentials
        )
        self.private = firestore.Client(
            project=PROJECT, database=PRIVATE_DATABASE, credentials=credentials
        )

    def storage_metadata(self, key, generation=None):
        params = {"fields": "name,generation,size"}
        if generation:
            params["generation"] = generation
        try:
            response = self.observer.get(
                f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{quote(key, safe='')}",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
            )
        except httpx.HTTPError as exc:
            raise SmokeError("storage_observer_transport_failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise SmokeError(f"storage_observer_status_{response.status_code}")
        data = response.json()
        if data.get("name") != key or not str(data.get("generation", "")).isdigit():
            raise SmokeError("storage_reference_invalid")
        return data

    def synthetic_nearest(self):
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

        matches = (
            self.business.collection("rag_documents")
            .select([])
            .find_nearest(
                vector_field="embedding",
                query_vector=self.query_vector,
                limit=3,
                distance_measure=DistanceMeasure.COSINE,
            )
            .get(timeout=30, retry=None)
        )
        # The empty projection returns document IDs only, no fields or vectors. Report only IDs
        # belonging to this invocation, never unrelated result identifiers.
        return sorted(snapshot.id for snapshot in matches if snapshot.id in self.chunk_ids)

    def replay_completed_confirmation(self, path, body, job_id):
        self.phase = "completed_confirmation_replay"
        try:
            replay = self.json_request("POST", path, expected=(200, 202), json=body)
        except SmokeError as exc:
            if str(exc) != "unexpected_admin_status_500":
                raise
            # Preserve this failed acceptance check. The source is already
            # published, so its readback and safe deletion remain independently
            # verifiable without retrying confirmation or provider work.
            row = {
                "check": "completed_confirmation_replays_same_job",
                "passed": False,
                "status": 500,
            }
            self.report["checks"].append(row)
            self.report.update(error=str(exc), failed_phase=self.phase)
            print(json.dumps(row), flush=True)
            return
        self.check("completed_confirmation_replays_same_job", replay.get("job_id") == job_id)

    def exercise(self):
        self.phase = "health"
        health = self.json_request("GET", "/health")
        self.check(
            "preview_health",
            health.get("environment") == "preview" and health.get("service") == "admin",
        )
        self.phase = "login"
        password = self.password_file.read_text().strip()
        login = self.json_request("POST", "/auth/login", json={"password": password})
        password = None
        self.csrf = login.get("csrf_token")
        self.check("session_created", isinstance(self.csrf, str) and bool(self.csrf))
        self.connect_observer()

        rows, raw, metadata = synthetic_fixture(self.report["run_id"])
        self.phase = "queued_scan"
        upload = self.json_request(
            "POST",
            "/corpus/imports/preview",
            expected=(202,),
            files={"file": (self.filename, raw, "application/json")},
            data={
                "kind": "law",
                "title": self.title,
                "metadata": json.dumps(metadata),
            },
        )
        self.import_id, self.source_id = upload["import_id"], upload["source_id"]
        self.check(
            "generated_source_identity",
            bool(re.fullmatch(r"[a-f0-9]{32}", self.import_id))
            and self.source_id == f"src_{self.import_id}",
        )
        self.report.update(import_id=self.import_id, source_id=self.source_id)
        self.wait_job(upload["job"]["job_id"], "corpus_parse")
        draft = self.json_request("GET", f"/corpus/imports/{self.import_id}")
        self.check(
            "structured_mapping_required",
            draft.get("status") == "mapping_required"
            and {"body_text", "extra_json"}.issubset(draft.get("available_fields", []))
            and not draft.get("chunks")
            and not draft.get("job_id")
            and not draft.get("plan_hash"),
        )
        self.phase = "queued_mapping"
        mapped = self.json_request(
            "POST",
            f"/corpus/imports/{self.import_id}/mapping",
            expected=(202,),
            json={
                "column_mapping": {"content": "body_text", "metadata": "extra_json"},
                "parse_revision": draft["parse_revision"],
            },
        )
        self.wait_job(mapped["job"]["job_id"], "corpus_parse")
        draft = self.json_request("GET", f"/corpus/imports/{self.import_id}")
        binding = draft.get("embedding", {})
        self.check(
            "mapped_preview_ready",
            draft.get("status") == "preview"
            and len(draft.get("chunks", [])) == 2
            and [chunk["content"] for chunk in draft["chunks"]]
            == [row["body_text"] for row in rows]
            and binding.get("project") == PROJECT
            and binding.get("database") == BUSINESS_DATABASE
            and binding.get("dimensions") == 1024,
        )
        self.request("GET", f"/corpus/sources/{self.source_id}", expected=(404,))
        staged = list(
            self.private.collection("corpus_imports")
            .document(self.import_id)
            .collection("embeddings")
            .select(["chunk_hash"])
            .limit(1)
            .stream(timeout=30, retry=None)
        )
        self.check(
            "no_publication_or_embedding_before_confirmation",
            not staged and not draft.get("job_id"),
        )
        for suffix in ("original", f"preview-{draft['parse_revision']}.json"):
            key = f"corpus/imports/{self.import_id}/{suffix}"
            reference = self.storage_metadata(key)
            self.check(
                "synthetic_object_retained",
                reference is not None,
                object=key,
                generation=reference.get("generation") if reference else None,
            )
            self.storage_objects[key] = reference["generation"]

        self.phase = "embedding_confirmation"
        confirm_path = f"/corpus/imports/{self.import_id}/confirm"
        confirm_body = {"plan_hash": draft["plan_hash"]}
        first = self.json_request("POST", confirm_path, expected=(200, 202), json=confirm_body)
        self.import_job_id = first.get("job_id")
        self.report["import_job_id"] = self.import_job_id
        second = self.json_request("POST", confirm_path, expected=(200, 202), json=confirm_body)
        self.check(
            "confirmation_idempotent",
            first.get("job_id") == second.get("job_id") and bool(first.get("job_id")),
        )
        completed = self.wait_job(first["job_id"], "corpus_import")
        result = completed.get("result") or {}
        self.check(
            "synthetic_import_published",
            result.get("source_id") == self.source_id
            and result.get("chunk_count") == 2
            and result.get("duplicate") is False,
        )
        self.replay_completed_confirmation(confirm_path, confirm_body, first["job_id"])

        self.phase = "source_readback"
        source = self.json_request("GET", f"/corpus/sources/{self.source_id}", params={"limit": 1})
        self.check(
            "source_active_and_paginated",
            source.get("status") == "active"
            and source.get("original_available") is True
            and len(source.get("chunks", [])) == 1
            and bool(source.get("next_cursor")),
        )
        page2 = self.json_request(
            "GET",
            f"/corpus/sources/{self.source_id}",
            params={"limit": 1, "cursor": source["next_cursor"]},
        )
        chunks = source["chunks"] + page2["chunks"]
        self.chunk_ids = {chunk["id"] for chunk in chunks}
        self.check(
            "synthetic_chunks_and_metadata_readable",
            len(self.chunk_ids) == 2
            and not page2.get("next_cursor")
            and [chunk["content"] for chunk in chunks] == [row["body_text"] for row in rows]
            and all(
                chunk["metadata"].get("smoke_run_id") == self.report["run_id"]
                and chunk["embedding"]["dimensions"] == 1024
                for chunk in chunks
            ),
            chunk_ids=sorted(self.chunk_ids),
        )
        original = self.request("GET", f"/corpus/sources/{self.source_id}/download")
        self.check(
            "original_download_exact",
            original.content == raw,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        from google.cloud.firestore_v1.vector import Vector

        for chunk in chunks:
            stored = (
                self.business.collection("rag_documents")
                .document(chunk["id"])
                .get(field_paths=["embedding", "source_id"], timeout=30, retry=None)
            )
            stored_data = stored.to_dict() or {}
            self.query_vector = stored_data.get("embedding")
            self.check(
                "native_vector_1024",
                stored_data.get("source_id") == self.source_id
                and isinstance(self.query_vector, Vector)
                and len(self.query_vector.to_map_value()["value"]) == 1024,
                chunk_id=chunk["id"],
            )
            self.query_vectors[chunk["id"]] = self.query_vector
            nearest = self.synthetic_nearest()
            self.check(
                "synthetic_find_nearest_before_delete",
                chunk["id"] in nearest,
                query_chunk_id=chunk["id"],
                synthetic_match_count=len(nearest),
                synthetic_ids=nearest,
            )

        self.phase = "whole_source_delete"
        self.delete_owned_source(source)
        self.verify_deleted()
        self.report["passed"] = all(check["passed"] for check in self.report["checks"])

    def delete_owned_source(self, source):
        if (
            source.get("source_id") != self.source_id
            or self.source_id != f"src_{self.import_id}"
            or source.get("filename") != self.filename
            or source.get("title") != self.title
            or source.get("kind") != "law"
            or (
                source.get("status") == "active"
                and (
                    not source.get("chunks")
                    or any(
                        chunk.get("metadata", {}).get("smoke_run_id") != self.report["run_id"]
                        for chunk in source["chunks"]
                    )
                )
            )
        ):
            raise SmokeError("cleanup_source_mismatch")
        if source.get("status") == "deleted":
            return
        deletion = self.json_request(
            "DELETE",
            f"/corpus/sources/{self.source_id}",
            expected=(200, 202),
            json={"revision": source["revision"]},
        )
        if deletion.get("job_id"):
            self.wait_job(deletion["job_id"], "corpus_delete")
        elif deletion.get("status") != "succeeded":
            raise SmokeError("delete_job_missing")

    def verify_deleted(self):
        source = self.json_request("GET", f"/corpus/sources/{self.source_id}")
        self.check(
            "source_tombstone_and_empty_chunks",
            source.get("status") == "deleted"
            and source.get("original_available") is False
            and source.get("chunks") == [],
        )
        self.request("GET", f"/corpus/sources/{self.source_id}/download", expected=(409,))
        self.request("GET", f"/corpus/imports/{self.import_id}", expected=(404,))
        self.check("original_download_and_draft_unavailable", True)
        staged = list(
            self.private.collection("corpus_imports")
            .document(self.import_id)
            .collection("embeddings")
            .select(["chunk_hash"])
            .limit(1)
            .stream(timeout=30, retry=None)
        )
        assets = (
            self.private.collection("corpus_source_assets")
            .document(self.source_id)
            .get(field_paths=["source_id"], timeout=30, retry=None)
        )
        self.check("synthetic_staging_and_assets_deleted", not staged and not assets.exists)
        missing = all(
            not self.business.collection("rag_documents")
            .document(chunk_id)
            .get(field_paths=["source_id"], timeout=30, retry=None)
            .exists
            for chunk_id in self.chunk_ids
        )
        self.check("synthetic_chunk_documents_deleted", bool(self.chunk_ids) and missing)
        for key, generation in self.storage_objects.items():
            self.check(
                "synthetic_object_generation_deleted",
                self.storage_metadata(key, generation) is None
                and self.storage_metadata(key) is None,
                object=key,
                generation=generation,
            )
        for chunk_id, vector in self.query_vectors.items():
            self.query_vector = vector
            nearest = self.synthetic_nearest()
            self.check(
                "synthetic_find_nearest_after_delete",
                not nearest,
                query_chunk_id=chunk_id,
                synthetic_match_count=len(nearest),
            )
        self.report["cleanup_complete"] = True

    def run(self):
        try:
            self.exercise()
        except (SmokeError, ProbeError) as exc:
            self.report.update(error=str(exc), failed_phase=self.phase)
        except Exception as exc:
            # SDK exceptions can carry request bodies or provider diagnostics.
            self.report.update(
                error="unexpected_smoke_failure",
                error_type=type(exc).__name__,
                failed_phase=self.phase,
            )
        finally:
            if self.source_id and not self.report.get("cleanup_complete"):
                try:
                    if self.import_job_id:
                        self.wait_job(self.import_job_id, "corpus_import", require_success=False)
                    response = self.request(
                        "GET", f"/corpus/sources/{self.source_id}", expected=(200, 404)
                    )
                    if response.status_code == 200:
                        self.delete_owned_source(response.json())
                        self.report["recovery_source_deleted"] = True
                    else:
                        self.report["recovery_source_absent"] = True
                        self.report["unpublished_draft_cleanup_pending"] = True
                except Exception:
                    self.report["cleanup_error"] = "owned_source_cleanup_incomplete"
            if self.csrf:
                try:
                    self.request("POST", "/auth/logout", expected=(200, 204))
                    self.report["logged_out"] = True
                except Exception:
                    self.report["logged_out"] = False
                    self.report["passed"] = False
            self.client.close()
            self.observer.close()
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--password-file", type=Path, default=Path(".admin-local/preview.password"))
    parser.add_argument("--job-timeout", type=int, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = (
        CorpusSmoke(args.base_url, args.password_file, job_timeout=args.job_timeout).run()
        if args.apply
        else smoke_plan(args.base_url)
    )
    if args.output:
        write_report(args.output, report)
    print(json.dumps(report), flush=True)
    if args.apply and not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
