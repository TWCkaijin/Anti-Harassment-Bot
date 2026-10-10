"""Immutable Storage retries must compare the exact object generation."""

import pytest
from google.api_core.exceptions import PreconditionFailed

from admin_app.infrastructure import ObjectStore


class Bucket:
    name = "private-fixture"

    def __init__(self):
        self.objects = {}
        self.reads = []

    def blob(self, name, generation=None):
        return Blob(self, name, generation)

    def get_blob(self, name):
        value = self.objects.get(name)
        return self.blob(name, value[0]) if value else None


class Blob:
    def __init__(self, bucket, name, generation):
        self.bucket, self.name, self.generation = bucket, name, generation

    def upload_from_string(self, data, *, content_type, if_generation_match):
        assert if_generation_match == 0
        if self.name in self.bucket.objects:
            raise PreconditionFailed("exists")
        self.generation = 1
        self.bucket.objects[self.name] = (self.generation, data)

    def download_as_bytes(self, *, if_generation_match):
        self.bucket.reads.append((self.name, self.generation, if_generation_match))
        current = self.bucket.objects[self.name]
        if current[0] != self.generation or current[0] != if_generation_match:
            raise PreconditionFailed("generation changed")
        return current[1]

    def delete(self, *, if_generation_match):
        if self.bucket.objects[self.name][0] != if_generation_match:
            raise PreconditionFailed("generation changed")
        del self.bucket.objects[self.name]


def test_idempotent_upload_accepts_only_identical_generation_pinned_bytes():
    bucket = Bucket()
    store = ObjectStore(bucket)
    original = store.put_bytes_idempotent("corpus/imports/fixture/preview-1.json", b"one")
    assert store.put_bytes_idempotent(original["name"], b"one") == original
    assert bucket.reads == [(original["name"], 1, 1)]
    with pytest.raises(ValueError, match="different bytes"):
        store.put_bytes_idempotent(original["name"], b"two")
    assert bucket.objects[original["name"]] == (1, b"one")
    # The original non-idempotent operation still rejects every overwrite.
    with pytest.raises(PreconditionFailed):
        store.put_bytes(original["name"], b"one")


def test_lookup_is_exact_and_stale_references_cannot_delete_replacement():
    bucket = Bucket()
    store = ObjectStore(bucket)
    assert store.lookup_reference("missing") is None
    reference = store.put_bytes("one", b"original")
    assert store.lookup_reference("one") == reference
    bucket.objects["one"] = (2, b"replacement")
    with pytest.raises(PreconditionFailed):
        store.delete(reference)
    assert bucket.objects["one"] == (2, b"replacement")
    with pytest.raises(ValueError, match="another environment"):
        store.read_bytes({**reference, "bucket": "other-private-environment"})


@pytest.mark.parametrize("key", ["../other", "/absolute", "one/../other", "", None])
def test_lookup_rejects_invalid_keys_before_storage_access(key):
    with pytest.raises(ValueError, match="Invalid immutable object key"):
        ObjectStore(Bucket()).lookup_reference(key)
