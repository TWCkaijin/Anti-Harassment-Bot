"""Offline evidence for the explicitly reviewed preview database migration."""

import copy
import os

import pytest
from google.cloud.firestore_v1.vector import Vector

from admin.tests.test_corpus import FakeDB, atomic
from admin_app.scripts.common.legal_corpus import digest
from admin_app.scripts.maintenance.migrate_business import (
    PROJECT,
    apply_clone,
    clone_plan,
    private_write,
    validate_clone_plan,
    verify_clone,
)


@pytest.fixture
def databases():
    source, target = FakeDB(), FakeDB()
    source.project = target.project = PROJECT
    source._database, target._database = "(default)", "app-dev"
    source.collection("runtime_config").document("app_main").set(
        {"openrouter_model": "offline-model", "pipeline": {"enable_analysis": True}}
    )
    source.collection("scenario_scripts").document("safe-skill").set({"enabled": True})
    source.collection("rag_documents").document("law-1").set(
        {
            "content": "synthetic text",
            "metadata": {"source": "synthetic"},
            "embedding": Vector([0.1, 0.2, 0.3]),
        }
    )
    source.collection("admin_credentials").document("shared_password").set({"must_not_copy": True})
    return source, target


def test_plan_is_read_only_scoped_and_reports_native_vectors(databases):
    source, target = databases
    before = copy.deepcopy(source.data)
    plan = clone_plan(source, target)
    assert not target.data and source.data == before
    assert plan["collections"]["rag_documents"]["native_vectors"] == 1
    assert plan["collections"]["rag_documents"]["dimensions"] == {3: 1}
    assert {entry["collection"] for entry in plan["entries"]} == {
        "runtime_config",
        "scenario_scripts",
        "rag_documents",
    }
    assert plan["actions"] == {"create": 3}


def test_clone_preserves_vectors_renames_config_and_is_idempotent(databases):
    source, target = databases
    source_before = copy.deepcopy(source.data)
    plan = clone_plan(source, target)
    assert apply_clone(source, target, plan, atomic) == {"created": 3}
    assert source.data == source_before
    assert isinstance(
        target.collection("rag_documents").document("law-1").get().to_dict()["embedding"], Vector
    )
    assert target.collection("runtime_config").document("app_dev").get().exists
    assert not target.collection("runtime_config").document("app_main").get().exists
    assert not target.collection("admin_credentials").document("shared_password").get().exists
    assert apply_clone(source, target, plan, atomic) == {"unchanged": 3}
    assert verify_clone(source, target, plan)["verified"] is True


def test_changed_target_is_never_overwritten(databases):
    source, target = databases
    plan = clone_plan(source, target)
    target.collection("runtime_config").document("app_dev").set({"independent_change": True})
    with pytest.raises(ValueError, match="Concurrent target"):
        apply_clone(source, target, plan, atomic)
    assert target.collection("runtime_config").document("app_dev").get().to_dict() == {
        "independent_change": True
    }


def test_changed_source_is_detected_before_target_write(databases):
    source, target = databases
    plan = clone_plan(source, target)
    source.collection("runtime_config").document("app_main").update({"openrouter_model": "changed"})
    with pytest.raises(ValueError, match="Source changed"):
        apply_clone(source, target, plan, atomic)
    assert not target.data


def test_plan_cannot_expand_to_private_collections(databases):
    source, target = databases
    plan = clone_plan(source, target)
    plan["entries"][0]["collection"] = "admin_credentials"
    plan["plan_sha256"] = digest(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    with pytest.raises(ValueError, match="Unexpected migration collection"):
        validate_clone_plan(plan)


def test_backups_are_owner_only_and_never_overwritten(tmp_path):
    path = tmp_path / "backup.json"
    private_write(path, {"snapshot": "synthetic"})
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        private_write(path, {"changed": True})
