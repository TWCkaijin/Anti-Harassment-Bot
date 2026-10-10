"""Verify revision propagation through independent process-local TTL caches."""

import importlib.util
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag_core.core import runtime_config


@pytest.mark.parametrize("initial_revision", [None, "before-update"])
def test_independent_caches_adopt_published_revision_at_their_own_ttl(
    monkeypatch, initial_revision
):
    clock = SimpleNamespace(seconds=100.0)
    store = SimpleNamespace(
        data=(
            {"revision": initial_revision, "enable_image_upload": True}
            if initial_revision is not None
            else None
        )
    )

    def instance(name):
        # Loading the actual module twice isolates its globals as two workers would.
        module_name = f"{runtime_config.__name__}_ttl_{name}"
        spec = importlib.util.spec_from_file_location(module_name, runtime_config.__file__)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module_name, module)
        spec.loader.exec_module(module)
        module.settings = runtime_config.settings.model_copy(
            update={
                "runtime_config_cache_ttl_seconds": 30,
                "runtime_config_collection_name": "runtime_config",
                "runtime_config_document_id": "app_dev",
            }
        )
        database = Mock()
        document = database.collection.return_value.document.return_value

        def read(**kwargs):
            value = deepcopy(store.data)
            return SimpleNamespace(exists=value is not None, to_dict=lambda: deepcopy(value))

        document.get.side_effect = read
        module.get_business_db = lambda: database
        module.monotonic = lambda: clock.seconds
        return module, database, document.get

    first, first_database, first_read = instance("first")
    second, second_database, second_read = instance("second")
    first_old = first.get_runtime_config()
    clock.seconds = 110.0
    second_old = second.get_runtime_config()
    old_revision = initial_revision or "defaults"
    assert first_old.revision == second_old.revision == old_revision
    assert first_old.enable_image_upload is second_old.enable_image_upload is True
    assert first_old is not second_old

    # A committed Admin update does not invalidate another process's memory.
    clock.seconds = 121.0
    store.data = {"revision": "published-revision", "enable_image_upload": False}
    for before_expiry in (121.0, 125.0, 129.999):
        clock.seconds = before_expiry
        assert first.get_runtime_config() is first_old
        assert second.get_runtime_config() is second_old
    assert first_read.call_count == second_read.call_count == 1

    # Frequent cache hits must not slide the expiry or refresh the other instance.
    clock.seconds = 130.0
    first_new = first.get_runtime_config()
    assert first_new.revision == "published-revision"
    assert first_new.source == "firestore"
    assert first_new.enable_image_upload is False
    assert second.get_runtime_config() is second_old
    assert first_read.call_count == 2
    assert second_read.call_count == 1
    clock.seconds = 139.999
    assert second.get_runtime_config() is second_old

    clock.seconds = 140.0
    second_new = second.get_runtime_config()
    assert second_new.revision == first_new.revision == store.data["revision"]
    assert second_new.source == "firestore"
    assert second_new.enable_image_upload is False
    assert first.get_runtime_config() is first_new
    assert first_read.call_count == second_read.call_count == 2
    assert first_old.revision == second_old.revision == old_revision
    assert first_old.enable_image_upload is second_old.enable_image_upload is True
    for database, read in ((first_database, first_read), (second_database, second_read)):
        assert all(call.args == ("runtime_config",) for call in database.collection.call_args_list)
        assert all(
            call.args == ("app_dev",)
            for call in database.collection.return_value.document.call_args_list
        )
        assert all(call.kwargs == {"timeout": 5.0, "retry": None} for call in read.call_args_list)
