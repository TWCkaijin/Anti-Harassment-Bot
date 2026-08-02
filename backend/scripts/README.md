# Backend Scripts

Scripts here can import backend settings and may talk to OpenRouter, Firebase,
or Firestore.

```text
backend/scripts/common/   Shared ingestion utilities
backend/scripts/ingest/   Firestore Vector Search ingestion entrypoints
backend/scripts/legacy/   Compatibility wrappers for old script names or flows
```

Use these after source data has already been standardized under `data/`.

```bash
python backend/scripts/ingest/documents_to_firestore.py
python backend/scripts/ingest/judgments_to_firestore.py
python backend/scripts/ingest/remedies_to_firestore.py
```

Add `--upload` to actually embed and write to Firestore.
