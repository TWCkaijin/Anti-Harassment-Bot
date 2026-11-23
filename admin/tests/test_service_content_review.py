"""Review plans use synthetic documents; no Firebase client or model is created."""

import copy
import os

import pytest

from admin_app.scripts.common.firestore_ingest import build_remedy_documents
from admin_app.scripts.common.legal_corpus import digest
from admin_app.scripts.ingest.official_laws import verify_plan
from admin_app.scripts.maintenance.plan_verified_laws import EXPECTED, make_verified_law_plan
from admin_app.scripts.maintenance.review_service_content import (
    PROJECT,
    make_plan,
    private_write,
    run_plan,
    validate_plan,
)
from rag_core.core.referral_policy import (
    is_private_referral_action,
    is_private_referral_resource,
    is_private_referral_sentence,
)
from rag_core.rag.legal_lookup import legacy_conflict_reason


def snapshot():
    main = {
        "openrouter_model": "model-choice-to-preserve",
        "temperature": 0.7,
        "custom_main_value": {"keep": True},
        "enable_image_upload": True,
        "enable_anonymization": False,
        "pipeline": {"enable_rag": True, "mask_message": False},
        "agent_prompt_sections": {
            "core_mission": "Custom safe mission",
            "important_resources": "可聯絡勵馨基金會求助。",
            "analysis_rules": "性騷擾防治法第13條申訴期限為一年。",
            "retrieval_instructions": "資料不足就拒絕回答任何法律方向。",
            "language": "繁體中文",
        },
    }
    dev = {"openrouter_model": "dev-model", "pipeline": {}, "agent_prompt_sections": {}}
    databases = {}
    for database, config_id, config in (
        ("(default)", "app_main", main),
        ("app-dev", "app_dev", dev),
    ):
        databases[database] = {
            "runtime_config": {config_id: config},
            "scenario_scripts": {
                "official": {
                    "name": "公部門窗口",
                    "enabled": True,
                    "instruction": "不要轉介民間基金會或協會，請使用官方窗口。",
                    "actions": [{"action": "tel", "phone_number": "113", "label": "113 保護專線"}],
                }
            },
            "rag_documents": {
                "law": {"content": "性騷擾防治法條文", "metadata": {"data_type": "law"}},
                "legacy": {
                    "content": "性騷擾防治法第13條申訴期限為一年。",
                    "metadata": {"data_type": "document"},
                },
            },
            "rag_remedies": {
                "private": {
                    "content": "可聯絡某某基金會求助。",
                    "metadata": {"data_type": "remedy"},
                    "embedding": {"$firestore_vector": [0.1, 0.2]},
                },
                "official": {
                    "content": "請洽地方主管機關。",
                    "metadata": {"data_type": "remedy"},
                },
            },
        }
    result = {"schema_version": 1, "project": PROJECT, "databases": databases}
    return {**result, "snapshot_sha256": digest(result)}


def test_plan_preserves_unrelated_main_values_and_raw_evidence():
    original = snapshot()
    plan = make_plan(copy.deepcopy(original))
    validate_plan(plan)
    rows = {
        (item["database"], item["collection"], item["doc_id"]): item for item in plan["changes"]
    }
    main = rows[("(default)", "runtime_config", "app_main")]
    assert main["after"]["custom_main_value"] == {"keep": True}
    assert main["after"]["openrouter_model"] == "model-choice-to-preserve"
    assert main["after"]["temperature"] == 0.7
    assert main["after"]["pipeline"] == {
        "enable_rag": True,
        "mask_message": True,
        "mask_case_context": True,
        "mask_retrieval_query": True,
    }
    assert main["after"]["agent_prompt_sections"] == {
        "core_mission": "Custom safe mission",
        "language": "繁體中文",
    }
    assert main["after"]["enable_image_upload"] is False
    assert main["after"]["enable_anonymization"] is True
    assert main["after"]["revision"] != main["before"].get("revision")
    assert main["after"]["updated_by"] == "service-content-review"
    assert rows[("(default)", "rag_remedies", "private")]["action"] == "delete"
    assert rows[("(default)", "rag_documents", "legacy")]["reasons"] == [
        "legacy_outdated_article_13_one_year_rule"
    ]
    assert rows[("(default)", "rag_remedies", "private")]["before"]["embedding"] == {
        "$firestore_vector": [0.1, 0.2]
    }
    assert all(row["collection"] != "rag_judgments" for row in plan["changes"])
    assert all(row["doc_id"] != "official" for row in plan["changes"])
    assert (
        original["databases"]["(default)"]["runtime_config"]["app_main"]["enable_image_upload"]
        is True
    )


def test_recomputed_hash_does_not_allow_unscoped_change():
    plan = make_plan(snapshot())
    plan["changes"][0]["after"]["openrouter_model"] = "tampered"
    plan["changes"][0]["after_sha256"] = digest(plan["changes"][0]["after"])
    plan["plan_sha256"] = digest(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    with pytest.raises(ValueError, match="outside the policy"):
        validate_plan(plan)


def test_safe_prompt_overrides_survive_targeted_config_migration():
    source = snapshot()
    source["databases"]["(default)"]["runtime_config"]["app_main"]["agent_prompt_sections"] = {
        "important_resources": "可向地方主管機關詢問。",
        "analysis_rules": "依個案事實判斷。",
        "retrieval_instructions": "請使用本地資料庫。",
    }
    source["snapshot_sha256"] = digest(
        {key: value for key, value in source.items() if key != "snapshot_sha256"}
    )
    plan = make_plan(source)
    config = next(
        row["after"]
        for row in plan["changes"]
        if row["database"] == "(default)" and row["collection"] == "runtime_config"
    )
    assert config["agent_prompt_sections"] == {
        "important_resources": "可向地方主管機關詢問。",
        "analysis_rules": "依個案事實判斷。",
        "retrieval_instructions": "請使用本地資料庫。",
    }


def test_snapshot_output_is_private_and_will_not_overwrite(tmp_path):
    path = tmp_path / "snapshot.json"
    private_write(path, snapshot())
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        private_write(path, snapshot())


class _FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data)


class _FakeRef:
    def __init__(self, state, collection, doc_id):
        self.state = state
        self.collection = collection
        self.doc_id = doc_id

    def get(self, transaction=None):
        return _FakeSnapshot(self.doc_id, self.state[self.collection].get(self.doc_id))


class _FakeCollection:
    def __init__(self, state, collection):
        self.state = state
        self.name = collection

    def document(self, doc_id):
        return _FakeRef(self.state, self.name, doc_id)


class _FakeTransaction:
    def set(self, ref, value):
        ref.state[ref.collection][ref.doc_id] = copy.deepcopy(value)

    def delete(self, ref):
        ref.state[ref.collection].pop(ref.doc_id, None)


class _FakeClient:
    project = PROJECT

    def __init__(self, database, collections):
        self._database = database
        self.state = copy.deepcopy(collections)

    def collection(self, name):
        return _FakeCollection(self.state, name)

    def transaction(self):
        return _FakeTransaction()


def test_apply_and_rollback_are_compare_and_set(monkeypatch):
    from google.cloud import firestore

    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    baseline = snapshot()
    plan = make_plan(baseline)
    clients = {
        database: _FakeClient(database, collections)
        for database, collections in baseline["databases"].items()
    }
    # A concurrent admin edit must stop before changing that document.
    clients["(default)"].state["runtime_config"]["app_main"]["temperature"] = 0.8
    with pytest.raises(ValueError, match="changed since reviewed plan"):
        run_plan(plan, clients, database="(default)")
    clients["(default)"].state["runtime_config"]["app_main"]["temperature"] = 0.7
    preview_applied = run_plan(plan, clients, database="app-dev")
    assert preview_applied["applied"] == sum(
        row["database"] == "app-dev" for row in plan["changes"]
    )
    assert clients["(default)"].state["runtime_config"]["app_main"]["enable_image_upload"] is True
    main_applied = run_plan(plan, clients, database="(default)")
    assert main_applied["applied"] + preview_applied["applied"] == len(plan["changes"])
    assert "private" not in clients["(default)"].state["rag_remedies"]
    main_restored = run_plan(plan, clients, database="(default)", rollback=True)
    preview_restored = run_plan(plan, clients, database="app-dev", rollback=True)
    assert main_restored["restored"] + preview_restored["restored"] == len(plan["changes"])
    assert clients["(default)"].state["runtime_config"]["app_main"]["temperature"] == 0.7
    assert "private" in clients["(default)"].state["rag_remedies"]


def test_legacy_remedy_seed_cannot_reintroduce_private_referral(tmp_path):
    source = tmp_path / "remedy.md"
    source.write_text("# 一般求助\n\n可向某某基金會尋求協助。", encoding="utf-8")
    with pytest.raises(ValueError, match="curate before uploading"):
        build_remedy_documents(source)


def test_stale_legacy_law_row_is_filtered_but_original_judgment_is_preserved():
    text = "申訴期限為一年。"
    law_meta = {"law_name": "性騷擾防治法", "article_number": "13"}
    assert legacy_conflict_reason(text, law_meta) == "legacy_outdated_article_13_one_year_rule"
    assert legacy_conflict_reason(text, {**law_meta, "data_type": "judgment"}) is None


@pytest.mark.parametrize("name", ["勵馨", "現代婦女"])
def test_bare_private_organization_names_are_filtered(name):
    assert is_private_referral_resource(f"請聯絡{name}求助", {"data_type": "remedy"})
    assert is_private_referral_action(
        type("Action", (), {"label": f"聯絡{name}", "phone_number": ""})()
    )
    assert is_private_referral_sentence(f"可聯絡{name}尋求協助。")


def test_generic_private_legal_service_referral_does_not_hide_public_legal_help():
    assert is_private_referral_sentence("也可諮詢公益法律服務機構。")
    assert not is_private_referral_sentence("可諮詢地方政府提供的公益法律服務機構。")


def test_call_support_migration_retains_official_action():
    source = snapshot()
    source["databases"]["(default)"]["scenario_scripts"]["call_support"] = {
        "name": "電話求助",
        "enabled": True,
        "instruction": "請聯絡勵馨求助。",
        "trigger_keywords": ["求助", "勵馨"],
        "actions": [
            {"action": "tel", "phone_number": "113", "label": "113 保護專線"},
            {"action": "tel", "phone_number": "02-23917133", "label": "聯絡勵馨"},
        ],
    }
    source["snapshot_sha256"] = digest(
        {key: value for key, value in source.items() if key != "snapshot_sha256"}
    )
    plan = make_plan(source)
    row = next(row for row in plan["changes"] if row["doc_id"] == "call_support")
    assert row["after"]["enabled"] is True
    assert row["after"]["actions"] == [
        {"action": "tel", "phone_number": "113", "label": "113 保護專線"}
    ]
    assert row["after"]["trigger_keywords"] == ["求助"]
    assert "勵馨" not in row["after"]["instruction"]


def test_independent_verified_law_plan_is_source_bound_creates():
    plan = make_verified_law_plan(snapshot(), "app-dev")
    verify_plan(plan)
    assert plan["snapshot_status"] == "cloud_snapshot"
    assert plan["counts"] == {"create": len(EXPECTED)}
    assert {row["doc_id"] for row in plan["changes"]} == set(EXPECTED)
    assert all(row["payload"]["metadata"]["checked_at"] == "2026-11-10" for row in plan["changes"])
    assert all(row["payload"]["metadata"]["lookup_keys"] for row in plan["changes"])
