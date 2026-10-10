"""Private persistence and immutable object storage, injectable in offline tests."""

from collections.abc import Callable
from contextlib import suppress
from functools import lru_cache
from typing import Any

import firebase_admin
from firebase_admin import firestore

from admin_app.config import get_admin_settings


def private_db():
    config = get_admin_settings()
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": config.google_cloud_project})
    return firestore.client(database_id=config.admin_private_database_id)


def atomic(callback: Callable[..., Any]):
    return firestore.transactional(callback)(private_db().transaction())


class ObjectStore:
    def __init__(self, bucket):
        self.bucket = bucket

    @staticmethod
    def _validate_key(key):
        if not isinstance(key, str) or not key or key.startswith("/") or ".." in key.split("/"):
            raise ValueError("Invalid immutable object key")

    def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> dict:
        self._validate_key(key)
        blob = self.bucket.blob(key)
        blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
        return {"bucket": self.bucket.name, "name": key, "generation": str(blob.generation)}

    def lookup_reference(self, key: str) -> dict | None:
        """Resolve one current object to a reference pinned to its exact generation."""
        self._validate_key(key)
        blob = self.bucket.get_blob(key)
        if blob is None:
            return None
        if blob.generation is None:
            raise RuntimeError("Object generation is unavailable")
        return {"bucket": self.bucket.name, "name": key, "generation": str(blob.generation)}

    def put_bytes_idempotent(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> dict:
        from google.api_core.exceptions import AlreadyExists, PreconditionFailed

        try:
            return self.put_bytes(key, data, content_type)
        except (AlreadyExists, PreconditionFailed):
            reference = self.lookup_reference(key)
            if reference is None:
                raise RuntimeError("Existing immutable object disappeared") from None
            if self.read_bytes(reference) != data:
                raise ValueError("Existing immutable object contains different bytes") from None
            return reference

    def _blob(self, reference: dict):
        if reference.get("bucket") != self.bucket.name:
            raise ValueError("Object reference belongs to another environment")
        name, generation = reference.get("name"), reference.get("generation")
        if not name or not generation or name.startswith("/") or ".." in name.split("/"):
            raise ValueError("Invalid immutable object reference")
        return self.bucket.blob(name, generation=int(generation))

    def read_bytes(self, reference: dict) -> bytes:
        return self._blob(reference).download_as_bytes(
            if_generation_match=int(reference["generation"])
        )

    def delete(self, reference: dict):
        from google.api_core.exceptions import NotFound

        with suppress(NotFound):
            self._blob(reference).delete(if_generation_match=int(reference["generation"]))

    put = put_bytes
    read = read_bytes


@lru_cache
def object_store() -> ObjectStore:
    from google.cloud import storage

    config = get_admin_settings()
    if not config.admin_storage_bucket:
        raise RuntimeError("Admin object storage is not configured")
    return ObjectStore(
        storage.Client(project=config.google_cloud_project).bucket(config.admin_storage_bucket)
    )
