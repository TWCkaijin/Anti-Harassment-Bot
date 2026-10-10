"""Transaction fixtures verify stale editors cannot overwrite current config."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag_core.core import runtime_config as runtime


class Document:
    def __init__(self, database):
        self.database = database
        self.transaction_reads = 0

    def get(self, *, transaction=None, **kwargs):
        assert transaction is not None, "CAS must read through its transaction"
        self.transaction_reads += 1
        value = deepcopy(self.database.data)
        return SimpleNamespace(exists=value is not None, to_dict=lambda: deepcopy(value))

    def set(self, *args, **kwargs):
        raise AssertionError("CAS must not write outside its transaction")


class Transaction:
    def __init__(self, database):
        self.database = database
        self.pending = []

    def set(self, reference, value, *, merge=False):
        assert reference is self.database.document_ref
        self.pending.append((deepcopy(value), merge))

    def commit(self):
        for value, merge in self.pending:
            self.database.data = {**(self.database.data or {}), **value} if merge else value
            self.database.writes += 1


class Database:
    def __init__(self, data):
        self.data = deepcopy(data)
        self.document_ref = Document(self)
        self.collections = []
        self.documents = []
        self.writes = 0
        self.concurrent_revision = None

    def collection(self, name):
        self.collections.append(name)
        return self

    def document(self, name):
        self.documents.append(name)
        return self.document_ref

    def transaction(self):
        return Transaction(self)

    def transactional(self, callback):
        def run(transaction):
            callback(transaction)
            if self.concurrent_revision is not None:
                self.data = {
                    **(self.data or {}),
                    "revision": self.concurrent_revision,
                    "temperature": 0.7,
                }
                self.concurrent_revision = None
                transaction = Transaction(self)
                callback(transaction)
            transaction.commit()

        return run


@pytest.fixture
def store(monkeypatch):
    database = Database(
        {"revision": "initial", "openrouter_model": "saved/model", "temperature": 0.2}
    )
    monkeypatch.setattr(runtime, "get_business_db", lambda: database)
    monkeypatch.setattr(runtime, "transactional", database.transactional)
    monkeypatch.setattr(
        runtime,
        "get_runtime_config",
        lambda **kwargs: runtime._build_config(database.data, "firestore"),
    )
    invalidate = Mock()
    monkeypatch.setattr(runtime, "invalidate_runtime_config_cache", invalidate)
    database.invalidate = invalidate
    return database


def mutate(operation, revision):
    if operation == "update":
        return runtime.update_runtime_config(
            {"temperature": 0.4}, "fixture-admin", expected_revision=revision
        )
    if operation == "seed":
        return runtime.seed_runtime_config_if_missing("fixture-admin", expected_revision=revision)
    return runtime.reset_runtime_config("fixture-admin", expected_revision=revision)


@pytest.mark.parametrize("operation", ["update", "seed", "reset"])
def test_stale_editor_does_not_write_or_invalidate_cache(store, operation):
    before = deepcopy(store.data)
    with pytest.raises(runtime.ConfigConflictError, match="Configuration changed"):
        mutate(operation, "stale-revision")
    assert store.data == before
    assert store.writes == 0
    assert store.document_ref.transaction_reads == 1
    store.invalidate.assert_not_called()


@pytest.mark.parametrize("operation", ["update", "seed", "reset"])
def test_successful_cas_changes_revision_and_only_selected_document(store, operation):
    result = mutate(operation, "initial")
    assert store.writes == 1
    assert store.document_ref.transaction_reads == 1
    assert set(store.collections) == {runtime.settings.runtime_config_collection_name}
    assert set(store.documents) == {runtime.settings.runtime_config_document_id}
    assert result.revision == store.data["revision"] != "initial"
    assert store.data["updated_by"] == "fixture-admin"
    store.invalidate.assert_called_once_with()
    if operation == "seed":
        assert store.data["openrouter_model"] == "saved/model"
    if operation == "update":
        assert store.data["temperature"] == 0.4


@pytest.mark.parametrize("operation", ["update", "seed", "reset"])
def test_missing_document_uses_defaults_revision_for_atomic_creation(store, operation):
    store.data = None
    result = mutate(operation, "defaults")
    assert store.writes == 1
    assert result.revision != "defaults"


@pytest.mark.parametrize("operation", ["update", "seed", "reset"])
def test_firestore_retry_rechecks_revision_before_any_commit(store, operation):
    store.concurrent_revision = "other-editor-won"
    with pytest.raises(runtime.ConfigConflictError, match="Configuration changed"):
        mutate(operation, "initial")
    assert store.document_ref.transaction_reads == 2
    assert store.writes == 0
    assert store.data["revision"] == "other-editor-won"
    assert store.data["temperature"] == 0.7
    store.invalidate.assert_not_called()


def test_second_editor_cannot_reuse_first_editors_revision(store):
    first = mutate("update", "initial")
    with pytest.raises(runtime.ConfigConflictError):
        mutate("reset", "initial")
    assert store.writes == 1
    assert store.data["revision"] == first.revision
    assert store.data["temperature"] == 0.4


def test_complete_seed_is_noop_and_preserves_revision(store):
    store.data = runtime._default_runtime_document() | {"revision": "complete"}
    result = mutate("seed", "complete")
    assert result.revision == "complete"
    assert store.writes == 0


def test_cas_rechecks_cross_field_values_against_live_document(store, monkeypatch):
    stale_cache = runtime._build_config(
        {"rag_retrieval_top_k": 3, "rag_rerank_candidate_k": 20}, "firestore"
    )
    store.data.update(rag_retrieval_top_k=15, rag_rerank_candidate_k=20)
    monkeypatch.setattr(runtime, "get_runtime_config", lambda **kwargs: stale_cache)
    with pytest.raises(ValueError, match="candidate_k must be at least"):
        runtime.update_runtime_config(
            {"rag_rerank_candidate_k": 10}, "fixture-admin", expected_revision="initial"
        )
    assert store.writes == 0
    assert store.data["rag_rerank_candidate_k"] == 20


def test_cas_partial_nested_updates_keep_other_saved_settings(store):
    store.data.update(
        agent_prompt_sections={"language": "saved language", "core_mission": "saved mission"},
        pipeline={"enable_analysis": False, "history_max_messages": 12},
    )
    runtime.update_runtime_config(
        {
            "agent_prompt_sections": {"language": "new language"},
            "pipeline": {"history_max_messages": 8},
        },
        "fixture-admin",
        expected_revision="initial",
    )
    assert store.data["agent_prompt_sections"]["language"] == "new language"
    assert store.data["agent_prompt_sections"]["core_mission"] == "saved mission"
    assert store.data["pipeline"]["history_max_messages"] == 8
    assert store.data["pipeline"]["enable_analysis"] is False
    assert store.data["pipeline"]["mask_message"] is True


def test_cas_valid_update_is_not_rejected_by_stale_cached_counts(store, monkeypatch):
    stale_cache = runtime._build_config(
        {"rag_retrieval_top_k": 3, "rag_rerank_candidate_k": 5}, "firestore"
    )
    store.data.update(rag_retrieval_top_k=3, rag_rerank_candidate_k=20)
    monkeypatch.setattr(runtime, "get_runtime_config", lambda **kwargs: stale_cache)
    runtime.update_runtime_config(
        {"rag_retrieval_top_k": 10}, "fixture-admin", expected_revision="initial"
    )
    assert store.writes == 1
    assert store.data["rag_retrieval_top_k"] == 10
    assert store.data["rag_rerank_candidate_k"] == 20


def test_legacy_document_revision_is_stable_but_changes_with_content():
    original = {
        "temperature": 0.2,
        "pipeline": {"enable_analysis": False, "history_max_messages": 12},
    }
    reordered = {
        "pipeline": {"history_max_messages": 12, "enable_analysis": False},
        "temperature": 0.2,
    }
    revision = runtime.configuration_revision(original)
    assert len(revision) == 64
    assert revision == runtime.configuration_revision(reordered)
    assert revision != runtime.configuration_revision(original | {"temperature": 0.3})
    assert runtime.configuration_revision(None) == "defaults"
    assert runtime.configuration_revision(original | {"revision": "explicit"}) == "explicit"
