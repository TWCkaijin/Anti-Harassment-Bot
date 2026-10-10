# Admin ingestion and migration scripts

Run modules from the repository root with
`PYTHONPATH=shared/python:admin/backend`. The shared configuration selects the
business database: production uses `(default)`; preview uses `app-dev`.

## Legacy Markdown ingestion

Use these after standardizing source data under `data/`:

```bash
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.ingest.documents_to_firestore
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.ingest.judgments_to_firestore
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.ingest.remedies_to_firestore
```

These commands are dry runs by default. `--upload` calls the embedding provider
and writes to the selected business database. The admin web import workflow
also retains the uploaded original and creates the source catalog.

`official_laws` provides fetch/build/audit/snapshot/plan/apply/rollback commands.
Its Firestore commands require an explicit `--database '(default)'` or
`--database app-dev`, and apply requires the reviewed plan hash.

## Production to preview migration

`maintenance.migrate_business` supports `plan`, `apply`, and `verify`. The fixed
scope is project `anti-harassment-bot`, source `(default)`, target `app-dev`:

- `runtime_config/app_main` maps to `runtime_config/app_dev`.
- `scenario_scripts`, `rag_documents`, `rag_judgments`, and `rag_remedies` retain
  document IDs, field values, and native Firestore vectors.
- Admin credentials, sessions, jobs, and other collections are excluded.

```bash
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.migrate_business plan --output /tmp/business-migration.json
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.migrate_business apply --plan /tmp/business-migration.json --expected-plan-sha256 REVIEWED_SHA256
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.migrate_business verify --plan /tmp/business-migration.json
```

Plan reads both databases and writes an exact local snapshot with mode `0600`.
Apply is resumable, never changes production, and refuses differing existing or
concurrently changed target documents. Verify compares every copied document
and checks native vector types. None of these commands calls an embedding or
chat model. A plan file contains corpus contents and must be handled as data.

## Existing-source catalogs

`ingest.catalog_legacy_source` previews/applies one explicit `metadata.source_file`
group. The migration module also provides `catalog-plan --database ... --output
...` and `catalog-apply --database ... --plan ... --expected-plan-sha256 ...` for
all verifiable groups in one environment.

Cataloging validates complete chunk groups before adding source metadata and a
private manifest. It leaves existing RAG records unchanged. Missing original
files are not reconstructed, and records without a verified group remain
browse-only. Plan hashes are bound to the project and database.
