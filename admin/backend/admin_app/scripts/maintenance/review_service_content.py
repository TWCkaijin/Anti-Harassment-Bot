"""Review and optionally apply narrow service-content corrections in both databases.

``snapshot`` is read-only. ``plan`` is offline and is the default review step.
``apply`` and ``rollback`` require both an explicit ``--apply`` switch and the
reviewed plan SHA. No model, embedding provider, or public web call is made.
The 0600 snapshot and plan contain complete original documents for rollback.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from admin_app.scripts.common.gcloud_login import get_gcloud_login_credentials
from admin_app.scripts.common.legal_corpus import digest
from admin_app.scripts.ingest.official_laws import decode_firestore, encode_firestore
from rag_core.core.referral_policy import (
    is_private_referral_action,
    is_private_referral_resource,
    is_private_referral_sentence,
)
from rag_core.core.scenario_scripts import _builtin_scenario_documents
from rag_core.rag.legal_lookup import legacy_conflict_reason

PROJECT = "anti-harassment-bot"
DATABASE_DOCUMENTS = {"(default)": "app_main", "app-dev": "app_dev"}
COLLECTIONS = ("scenario_scripts", "rag_documents", "rag_remedies")
MASK_FIELDS = ("mask_message", "mask_case_context", "mask_retrieval_query")
PRIVATE_ORG = re.compile(r"勵馨|現代婦女|基金會|協會|公益團體|民間團體|社團法人|財團法人")
NEGATED_REFERRAL = re.compile(
    r"(?:不要|不得|禁止|避免|不主動).{0,16}(?:轉介|推薦|提供).{0,16}(?:基金會|協會|團體)"
)
OBSOLETE_DEADLINE = re.compile(r"性騷擾防治法第(?:13|十三)條.{0,80}(?:一年|1年)", re.S)
OLD_BOUNDARY_REFUSAL = re.compile(
    r"(?:資料不足|資訊不足|事實未明).{0,24}(?:拒絕回答|不得回答|不能回答|無法回答|不提供法律資訊)"
)


def private_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")


def _signed(payload: dict, field: str) -> dict:
    result = dict(payload)
    result[field] = digest(result)
    return result


def _verify_signed(payload: dict, field: str) -> None:
    if not isinstance(payload, dict) or digest(
        {k: v for k, v in payload.items() if k != field}
    ) != payload.get(field):
        raise ValueError(f"{field} mismatch")


def _client(database: str, *, credentials=None):
    from google.cloud import firestore

    if database not in DATABASE_DOCUMENTS:
        raise ValueError("Unexpected database")
    client = firestore.Client(project=PROJECT, database=database, credentials=credentials)
    _check_client(client, database)
    return client


def _check_client(client, database: str) -> None:
    if client.project != PROJECT or client._database != database:
        raise ValueError("Unexpected Firestore project/database")


def take_snapshot(clients: dict) -> dict:
    """Read only the settings, Skills and curated corpus that may need changes."""
    databases = {}
    for database, config_id in DATABASE_DOCUMENTS.items():
        client = clients[database]
        _check_client(client, database)
        config = client.collection("runtime_config").document(config_id).get(timeout=10, retry=None)
        if not config.exists:
            raise ValueError(f"Missing runtime_config/{config_id} in {database}")
        databases[database] = {
            "runtime_config": {config_id: encode_firestore(config.to_dict())},
            **{
                collection: {
                    snapshot.id: encode_firestore(snapshot.to_dict())
                    for snapshot in client.collection(collection).stream(timeout=10, retry=None)
                }
                for collection in COLLECTIONS
            },
        }
    return _signed(
        {
            "schema_version": 1,
            "project": PROJECT,
            "created_at": datetime.now(UTC).isoformat(),
            "databases": databases,
        },
        "snapshot_sha256",
    )


def _validate_snapshot(snapshot: dict) -> None:
    _verify_signed(snapshot, "snapshot_sha256")
    if snapshot.get("schema_version") != 1 or snapshot.get("project") != PROJECT:
        raise ValueError("Unexpected snapshot project/schema")
    if set(snapshot.get("databases", {})) != set(DATABASE_DOCUMENTS):
        raise ValueError("Snapshot must contain both business databases")
    for database, config_id in DATABASE_DOCUMENTS.items():
        collections = snapshot["databases"][database]
        if set(collections) != {"runtime_config", *COLLECTIONS}:
            raise ValueError("Unexpected snapshot collection")
        if set(collections["runtime_config"]) != {config_id}:
            raise ValueError("Unexpected runtime config document")
        active = collections["runtime_config"][config_id].get("rag_collections") or {}
        if not isinstance(active, dict) or any(
            active.get(kind, expected) != expected
            for kind, expected in (("law", "rag_documents"), ("remedy", "rag_remedies"))
        ):
            raise ValueError("Active law/remedy collection is outside this review tool")
        if any(
            not isinstance(value, dict) for rows in collections.values() for value in rows.values()
        ):
            raise ValueError("Snapshot document must be an object")


def _config_after(before: dict, snapshot_time: str | None = None) -> tuple[dict, list[str]]:
    after = copy.deepcopy(before)
    reasons = []
    sections = after.get("agent_prompt_sections")
    if isinstance(sections, dict):
        for key in tuple(sections):
            value = sections[key]
            if isinstance(value, str) and _prompt_conflicts(value):
                del sections[key]
                reasons.append(f"reset_conflicting_prompt:{key}")
    for key, value in (("enable_image_upload", False), ("enable_anonymization", True)):
        if after.get(key) is not value:
            after[key] = value
            reasons.append(f"set_{key}")
    pipeline = after.get("pipeline")
    if not isinstance(pipeline, dict):
        pipeline = {}
        after["pipeline"] = pipeline
    for key in MASK_FIELDS:
        if pipeline.get(key) is not True:
            pipeline[key] = True
            reasons.append(f"set_pipeline.{key}")
    if reasons:
        after["updated_by"] = "service-content-review"
        if snapshot_time:
            after["updated_at"] = {"$firestore_timestamp": snapshot_time}
        after["revision"] = digest(
            {key: value for key, value in after.items() if key != "revision"}
        )
        reasons.append("bump_config_revision")
    return after, reasons


def _prompt_conflicts(value: str) -> bool:
    if OBSOLETE_DEADLINE.search(value) or OLD_BOUNDARY_REFUSAL.search(value):
        return True
    return any(
        PRIVATE_ORG.search(part)
        and not NEGATED_REFERRAL.search(part)
        and (is_private_referral_sentence(part) or "勵馨" in part or "現代婦女" in part)
        for part in re.split(r"[。！？!?\n]", value)
    )


def _script_after(before: dict, script_id: str) -> tuple[dict, list[str]]:
    after = copy.deepcopy(before)
    reasons = []
    text = " ".join(
        str(value)
        for value in (
            before.get("name", ""),
            before.get("instruction", ""),
            *(before.get("trigger_keywords") or []),
        )
    )
    # A private organization in active Skill prose can trigger a referral even
    # when its button was removed. Restore the packaged guidance where possible.
    sentences = re.split(r"[。！？!?\n]", text)
    has_private_instruction = any(
        ("勵馨" in part or "現代婦女" in part or is_private_referral_sentence(part))
        and not NEGATED_REFERRAL.search(part)
        for part in sentences
    )
    if has_private_instruction:
        packaged = _builtin_scenario_documents().get(script_id)
        if packaged:
            if after.get("instruction") != packaged["instruction"]:
                after["instruction"] = packaged["instruction"]
                reasons.append("restore_packaged_skill_instruction")
        elif after.get("enabled") is not False:
            after["enabled"] = False
            reasons.append("disable_private_referral_skill")
    if PRIVATE_ORG.search(str(before.get("name", ""))) and after.get("enabled") is not False:
        after["enabled"] = False
        reasons.append("disable_private_named_skill")
    keywords = before.get("trigger_keywords")
    if isinstance(keywords, list):
        safe_keywords = [keyword for keyword in keywords if not PRIVATE_ORG.search(str(keyword))]
        if len(safe_keywords) != len(keywords):
            after["trigger_keywords"] = safe_keywords
            reasons.append("remove_private_trigger_keywords")
            if not safe_keywords and after.get("enabled") is not False:
                after["enabled"] = False
                reasons.append("disable_triggerless_skill")
    actions = before.get("actions")
    if isinstance(actions, list):
        retained = [
            action
            for action in actions
            if not (
                isinstance(action, dict)
                and is_private_referral_action(type("Action", (), action)())
            )
        ]
        if len(retained) != len(actions):
            after["actions"] = retained
            reasons.append("remove_private_referral_actions")
            if not retained and after.get("enabled") is not False:
                after["enabled"] = False
                reasons.append("disable_actionless_skill")
    return after, reasons


def _corpus_after(collection: str, before: dict) -> tuple[dict | None, list[str]]:
    metadata = before.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if is_private_referral_resource(
        str(before.get("content", "")), {**metadata, "collection": collection}
    ):
        return None, ["remove_curated_private_referral"]
    if collection == "rag_documents" and (
        reason := legacy_conflict_reason(
            str(before.get("content", "")), {**metadata, "collection": collection}
        )
    ):
        return None, [reason]
    return before, []


def make_plan(snapshot: dict) -> dict:
    """Build a reviewable exact before/after plan without connecting to cloud."""
    _validate_snapshot(snapshot)
    changes = []
    for database in DATABASE_DOCUMENTS:
        collections = snapshot["databases"][database]
        for collection in ("runtime_config", *COLLECTIONS):
            for doc_id, before in sorted(collections[collection].items()):
                if collection == "runtime_config":
                    after, reasons = _config_after(before, snapshot.get("created_at"))
                elif collection == "scenario_scripts":
                    after, reasons = _script_after(before, doc_id)
                else:
                    after, reasons = _corpus_after(collection, before)
                if after == before:
                    continue
                changes.append(
                    {
                        "database": database,
                        "collection": collection,
                        "doc_id": doc_id,
                        "action": "delete" if after is None else "update",
                        "reasons": reasons,
                        "before": before,
                        "before_sha256": digest(before),
                        "after": after,
                        "after_sha256": digest(after) if after is not None else None,
                    }
                )
    return _signed(
        {
            "schema_version": 1,
            "project": PROJECT,
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "snapshot_created_at": snapshot.get("created_at"),
            "changes": changes,
            "summary": dict(
                Counter(f"{row['database']}/{row['collection']}/{row['action']}" for row in changes)
            ),
        },
        "plan_sha256",
    )


def validate_plan(plan: dict) -> None:
    _verify_signed(plan, "plan_sha256")
    if plan.get("schema_version") != 1 or plan.get("project") != PROJECT:
        raise ValueError("Unexpected plan project/schema")
    if not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("snapshot_sha256", ""))):
        raise ValueError("Plan lacks source snapshot hash")
    for row in plan.get("changes", []):
        database, collection, doc_id = row.get("database"), row.get("collection"), row.get("doc_id")
        if database not in DATABASE_DOCUMENTS or collection not in {"runtime_config", *COLLECTIONS}:
            raise ValueError("Unexpected migration target")
        if not isinstance(doc_id, str) or not doc_id or "/" in doc_id:
            raise ValueError("Invalid document ID")
        if collection == "runtime_config" and doc_id != DATABASE_DOCUMENTS[database]:
            raise ValueError("Unexpected runtime document")
        before, after = row.get("before"), row.get("after")
        if not isinstance(before, dict) or digest(before) != row.get("before_sha256"):
            raise ValueError("Invalid migration before image")
        if (after is None) != (row.get("action") == "delete"):
            raise ValueError("Invalid migration action")
        if after is not None and (
            not isinstance(after, dict) or digest(after) != row.get("after_sha256")
        ):
            raise ValueError("Invalid migration after image")
        if (
            collection == "runtime_config"
            and _config_after(before, plan.get("snapshot_created_at"))[0] != after
        ):
            raise ValueError("Runtime plan changes fields outside the policy")
        if collection == "scenario_scripts" and _script_after(before, doc_id)[0] != after:
            raise ValueError("Skill plan changes fields outside the policy")
        if (
            collection in {"rag_documents", "rag_remedies"}
            and _corpus_after(collection, before)[0] != after
        ):
            raise ValueError("Corpus plan changes fields outside the policy")


def run_plan(plan: dict, clients: dict, *, database: str, rollback: bool = False) -> dict:
    """Compare-and-set every row; a partial failure can be resumed or rolled back."""
    from google.cloud import firestore

    validate_plan(plan)
    if database not in DATABASE_DOCUMENTS:
        raise ValueError("Unexpected database")
    counts = Counter()
    rows = reversed(plan["changes"]) if rollback else plan["changes"]
    for row in rows:
        if row["database"] != database:
            continue
        client = clients[database]
        _check_client(client, database)
        ref = client.collection(row["collection"]).document(row["doc_id"])
        expected = row["after"] if rollback else row["before"]
        replacement = row["before"] if rollback else row["after"]

        @firestore.transactional
        def write(
            transaction, ref=ref, expected=expected, replacement=replacement, rollback=rollback
        ):
            snapshot = ref.get(transaction=transaction)
            current = encode_firestore(snapshot.to_dict()) if snapshot.exists else None
            if current == replacement:
                return "already_at_target"
            if current != expected:
                raise ValueError("Cloud document changed since reviewed plan")
            if replacement is None:
                transaction.delete(ref)
            else:
                transaction.set(ref, decode_firestore(replacement))
            return "restored" if rollback else "applied"

        counts[write(client.transaction())] += 1
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot_cmd = sub.add_parser("snapshot", help="Read-only cloud snapshot of both databases")
    snapshot_cmd.add_argument("--output", type=Path, required=True)
    snapshot_cmd.add_argument("--use-gcloud-login", action="store_true")
    plan_cmd = sub.add_parser("plan", help="Offline dry-run; does not connect to Firestore")
    plan_cmd.add_argument("--snapshot", type=Path, required=True)
    plan_cmd.add_argument("--output", type=Path, required=True)
    for command in ("apply", "rollback"):
        cmd = sub.add_parser(command, help="Explicit, reviewed Firestore write")
        cmd.add_argument("--plan", type=Path, required=True)
        cmd.add_argument("--expected-plan-sha256", required=True)
        cmd.add_argument("--database", required=True, choices=tuple(DATABASE_DOCUMENTS))
        cmd.add_argument("--apply", action="store_true", required=True)
        cmd.add_argument("--use-gcloud-login", action="store_true")
    args = parser.parse_args()
    if args.command == "snapshot":
        try:
            credentials = get_gcloud_login_credentials(PROJECT) if args.use_gcloud_login else None
            payload = take_snapshot(
                {
                    database: _client(database, credentials=credentials)
                    for database in DATABASE_DOCUMENTS
                }
            )
        except Exception as exc:
            print(json.dumps({"error_type": type(exc).__name__}), file=sys.stderr)
            raise SystemExit(1) from None
        private_write(args.output, payload)
        print(
            json.dumps({"snapshot_sha256": payload["snapshot_sha256"], "output": str(args.output)})
        )
    elif args.command == "plan":
        payload = make_plan(json.loads(args.snapshot.read_text(encoding="utf-8")))
        private_write(args.output, payload)
        print(
            json.dumps(
                {
                    "plan_sha256": payload["plan_sha256"],
                    "changes": len(payload["changes"]),
                    "summary": payload["summary"],
                },
                ensure_ascii=False,
            )
        )
    else:
        if not args.apply:
            parser.error("Cloud mutation requires --apply")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        validate_plan(plan)
        if plan["plan_sha256"] != args.expected_plan_sha256:
            parser.error("Reviewed plan SHA mismatch")
        try:
            credentials = get_gcloud_login_credentials(PROJECT) if args.use_gcloud_login else None
            result = run_plan(
                plan,
                {args.database: _client(args.database, credentials=credentials)},
                database=args.database,
                rollback=args.command == "rollback",
            )
        except Exception as exc:
            print(json.dumps({"error_type": type(exc).__name__}), file=sys.stderr)
            raise SystemExit(1) from None
        print(json.dumps({"database": args.database, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
