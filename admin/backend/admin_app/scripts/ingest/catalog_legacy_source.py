"""Preview/apply a verified legacy source catalog; never embed or change RAG rows.

python -m admin_app.scripts.ingest.catalog_legacy_source preview --kind law \
  --source-file data/documents/example.md --output /tmp/source-plan.json
python -m admin_app.scripts.ingest.catalog_legacy_source apply \
  --plan /tmp/source-plan.json --expected-plan-sha256 REVIEWED_SHA256

The preview reads the explicitly configured business database. Apply only adds
catalog metadata and a private manifest. Source originals are not reconstructed.
"""

import argparse
import json
from pathlib import Path

from admin_app.corpus.legacy import apply_legacy_catalog, prepare_legacy_catalog
from admin_app.corpus.service import CorpusService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview")
    preview.add_argument("--kind", choices=["law", "judgment", "remedy"], required=True)
    preview.add_argument("--source-file", required=True)
    preview.add_argument("--title")
    preview.add_argument("--output", type=Path, required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    service = CorpusService()
    if args.command == "preview":
        plan = prepare_legacy_catalog(service, args.kind, args.source_file, args.title)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(plan, output, ensure_ascii=False, indent=2)
        print(json.dumps({"plan_sha256": plan["plan_hash"], "chunk_count": plan["chunk_count"]}))
    else:
        result = apply_legacy_catalog(
            service, json.loads(args.plan.read_text()), args.expected_plan_sha256
        )
        print(json.dumps({"source_id": result["source_id"], "chunk_count": result["chunk_count"]}))


if __name__ == "__main__":
    main()
