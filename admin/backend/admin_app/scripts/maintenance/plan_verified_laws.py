"""Offline, bounded Firestore import plan from a reviewed cloud snapshot.

The output uses the existing official_laws import format. This script never
connects to Firestore, calls an embedding model, or writes cloud data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from admin_app.scripts.common.legal_corpus import digest, plan_import
from admin_app.scripts.ingest.official_laws import read_jsonl, verify_plan
from admin_app.scripts.maintenance.review_service_content import (
    DATABASE_DOCUMENTS,
    PROJECT,
    _validate_snapshot,
    private_write,
)

SEED = Path(__file__).resolve().parents[1] / "ingest" / "verified_sections.jsonl"
EXPECTED = {
    "law-D0050074-1": ("D0050074", "1", "性騷擾防治法", "2023-08-16"),
    "law-D0050074-2": ("D0050074", "2", "性騷擾防治法", "2023-08-16"),
    "law-D0050074-9": ("D0050074", "9", "性騷擾防治法", "2023-08-16"),
    "law-D0050074-12": ("D0050074", "12", "性騷擾防治法", "2023-08-16"),
    "law-D0050074-14": ("D0050074", "14", "性騷擾防治法", "2024-03-08"),
    "law-D0050074-25": ("D0050074", "25", "性騷擾防治法", "2023-08-16"),
    "law-N0030014-13": ("N0030014", "13", "性別平等工作法", "2024-03-08"),
    "law-N0030014-32-1": ("N0030014", "32-1", "性別平等工作法", "2024-03-08"),
    "law-H0080067-31": ("H0080067", "31", "性別平等教育法", "2023-08-16"),
    "law-H0080067-32": ("H0080067", "32", "性別平等教育法", "2023-08-16"),
    "law-H0080067-36": ("H0080067", "36", "性別平等教育法", "2023-08-16"),
    "law-H0080067-37": ("H0080067", "37", "性別平等教育法", "2024-03-08"),
    "law-C0010001-237": ("C0010001", "237", "刑事訴訟法", None),
}
LAW_REVISIONS = {
    "D0050074": "2023-08-16",
    "N0030014": "2023-08-16",
    "H0080067": "2023-08-16",
    "C0010001": "2026-05-13",
}


def make_verified_law_plan(
    snapshot: dict, database: str, *, only_doc_id: str | None = None
) -> dict:
    _validate_snapshot(snapshot)
    if database not in DATABASE_DOCUMENTS:
        raise ValueError("Unexpected database")
    seed = read_jsonl(SEED)
    if {row["doc_id"] for row in seed} != set(EXPECTED) or len(seed) != len(EXPECTED):
        raise ValueError("Verified law seed does not match the reviewed article set")
    for row in seed:
        metadata = row["metadata"]
        code, article, law, effective = EXPECTED[row["doc_id"]]
        if (
            metadata["source_url"]
            != f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={code}&flno={article}"
            or metadata["law_source_url"]
            != f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={code}"
            or metadata["law_code"] != code
            or metadata["article_number"] != article
            or metadata["law_name"] != law
            or metadata["version"] != f"{code}@{LAW_REVISIONS[code]}"
            or metadata["law_modified_date"] != LAW_REVISIONS[code]
            or metadata["article_effective_date"] != effective
            or metadata["effective_date"] != effective
            or metadata["effective_date_scope"]
            != (
                "reviewed_current_article_text_not_first_enactment"
                if effective
                else "article_history_not_verified"
            )
            or metadata["checked_at"] != "2026-11-10"
            or metadata["corpus_active"] is not True
            or metadata["article_status"] != "published"
            or metadata["review_status"] != "official_source_paraphrase_not_case_gold"
            or metadata["lookup_keys"] != [f"{metadata['law_name']}:{metadata['article_number']}"]
        ):
            raise ValueError("Verified law seed metadata changed; re-review official source")
    if only_doc_id is not None:
        if only_doc_id not in EXPECTED:
            raise ValueError("Only a reviewed article ID can be selected")
        seed = [row for row in seed if row["doc_id"] == only_doc_id]
    selected_ids = {row["doc_id"] for row in seed}
    existing = snapshot["databases"][database]["rag_documents"]
    # Only the reviewed deterministic target IDs are in this import. The separate
    # service-content plan handles legacy/NGO cleanup and raw judgments stay out.
    targets = [
        {"doc_id": doc_id, "data": existing[doc_id]}
        for doc_id in sorted(selected_ids)
        if doc_id in existing
    ]
    plan = plan_import(seed, targets, "rag_documents", PROJECT, database)
    plan.update(
        snapshot_status="cloud_snapshot",
        snapshot_sha256=snapshot["snapshot_sha256"],
        seed_sha256=digest(seed),
        selected_doc_ids=sorted(selected_ids),
        scope="reviewed_official_article_summaries_only",
    )
    plan["plan_sha256"] = digest(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    verify_plan(plan)
    if len(plan["changes"]) != len(selected_ids) or any(
        row["doc_id"] not in selected_ids or row["action"] not in {"create", "unchanged", "update"}
        for row in plan["changes"]
    ):
        raise ValueError("Unexpected official law import action")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--database", required=True, choices=tuple(DATABASE_DOCUMENTS))
    parser.add_argument("--only-doc-id", choices=tuple(sorted(EXPECTED)))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = make_verified_law_plan(
        json.loads(args.snapshot.read_text(encoding="utf-8")),
        args.database,
        only_doc_id=args.only_doc_id,
    )
    private_write(args.output, plan)
    print(
        json.dumps(
            {
                "database": args.database,
                "plan_sha256": plan["plan_sha256"],
                "counts": plan["counts"],
                "source_snapshot_sha256": plan["snapshot_sha256"],
            }
        )
    )


if __name__ == "__main__":
    main()
