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
`--database app-dev`, and apply requires the reviewed plan hash. Snapshot,
apply, and rollback accept opt-in `--use-gcloud-login` when application default
credentials are stale; the token stays in memory. For a reviewed preview
plan, the later import command is:

```bash
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.ingest.official_laws apply --project anti-harassment-bot --database app-dev --plan /tmp/verified-law-plan-app-dev.json --expected-plan-sha256 REVIEWED_SHA256 --backup /tmp/verified-law-preview-backup.jsonl --use-gcloud-login
```

This command calls the embedding provider and writes to preview Firestore;
review the plan and complete preview acceptance before running it. Rollback
uses the same `--project`, `--database`, `--backup`, and optional login flag,
plus `--apply` to restore records after checking their current hashes.

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

## Service content review in both databases

`maintenance.review_service_content` reads only `runtime_config/app_main` in
`(default)`, `runtime_config/app_dev` in `app-dev`, their Skills, and their
curated law/remedy collections. It does not read or change raw judgments. The
snapshot and plan are private JSON files (mode `0600`) containing complete
before values, including vectors, so store them securely. Snapshot reads
Firestore; plan is an offline dry run. Neither calls a model or browses the web.

```bash
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.review_service_content snapshot --output /tmp/service-content-snapshot.json
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.review_service_content plan --snapshot /tmp/service-content-snapshot.json --output /tmp/service-content-plan.json
```

If the local application default credential is stale but the active `gcloud`
login is valid, any Firestore command accepts opt-in `--use-gcloud-login`.
It obtains an access token without printing or saving it.

The plan changes only prompt overrides containing a detected policy conflict, private organization referral
Skills/curated records, `enable_image_upload=false`,
`enable_anonymization=true`, and the three pipeline masking switches. Other
main configuration values are preserved; changed configurations get a new
revision. An obsolete one-year claim attributed to the old Sex Harassment
Prevention Act article 13 is removed from curated law records; raw judgments
are untouched. Review every document ID and before/after value first. Apply
preview independently, finish its acceptance checks, then consider production.
Applying and rolling back require an explicit database, the exact reviewed
plan hash, and `--apply`:

```bash
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.review_service_content apply --database app-dev --plan /tmp/service-content-plan.json --expected-plan-sha256 REVIEWED_SHA256 --apply
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.review_service_content rollback --database app-dev --plan /tmp/service-content-plan.json --expected-plan-sha256 REVIEWED_SHA256 --apply
```

Each write compares the current Firestore document with the exact snapshot.
If an admin changes a document after the snapshot, the write stops rather than
overwriting that change. Rollback similarly checks the planned after value.

## Bounded official law summaries

`ingest/verified_sections.jsonl` is a tracked, dated summary of the official
MOJ text. The original three summaries cover 性騷擾防治法第 2、14 條 and
性別平等工作法第 13 條. Ten additional local candidates cover jurisdiction
(性騷擾防治法第 1 條), protection against improper differential treatment
of people participating in proceedings (性騷擾防治法第 9 條), compensation
(性騷擾防治法第 12 條), workplace local-authority complaints
(性別平等工作法第 32 條之 1), school investigation and review
(性別平等教育法第 31、32、36、37 條), and the conditional criminal complaint rule
(性騷擾防治法第 25 條、刑事訴訟法第 237 條). Every record includes an official source
URL, law revision, check date, article effective date where verified, and exact
article lookup key. The 30-day school period is for review after a written
result, and the six-month criminal complaint period applies only to an offense
that requires a complaint. Neither is a universal deadline for first reports.

These additions are a local import candidate. The MOJ law-level amendment date
does not establish the historical version applicable to a particular event.
The 2023 amendments of 性騷擾防治法第 14 條, 性別平等工作法第 13、32 條之 1,
and 性別平等教育法第 37 條 took effect on 2024-03-08 under their respective
effective-date provisions. The article-specific effective history of
刑事訴訟法第 237 條 has not been verified, so its metadata leaves that date null.

`maintenance.plan_verified_laws` makes a separate, offline import plan from
the same read-only snapshot. It targets only deterministic IDs for these thirteen
articles and leaves the NGO cleanup plan and all judgments independent.

```bash
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.plan_verified_laws --snapshot /tmp/service-content-snapshot.json --database app-dev --output /tmp/verified-law-plan-app-dev.json
```

For the reviewed Article 9 or 12 gap, select one document in an offline plan.
Check that its `changes` list has exactly the selected ID before applying to
Preview; an unreviewed ID such as Article 99 is rejected. Article 9 concerns
improper differential treatment for procedural participation; Article 12
concerns compensation for sexual harassment. The nonexistent Article 99
cannot be treated as a citation to either provision.

```bash
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.plan_verified_laws --snapshot /tmp/service-content-snapshot.json --database app-dev --only-doc-id law-D0050074-9 --output /tmp/article-9-plan-app-dev.json
PYTHONPATH=shared/python:admin/backend .venv/bin/python -m admin_app.scripts.maintenance.plan_verified_laws --snapshot /tmp/service-content-snapshot.json --database app-dev --only-doc-id law-D0050074-12 --output /tmp/article-12-plan-app-dev.json
```

After reviewing the generated plan and hash, use the `ingest.official_laws apply`
command above with that plan path, `--database app-dev`, its exact
`--expected-plan-sha256`, and a new private backup path for each plan. Each
plan imports one document to Preview and does not select the production database.

The resulting plan is compatible with `ingest.official_laws apply`: that
reviewed operation calls the embedding provider for each new article, stores
native Firestore vectors, and journals each previous value for rollback. Run
the preview import and verify exact article lookup plus vector retrieval before
making a separate production decision. No article in this seed is
an expert judgment about a user's case or the transitional law applicable to
an earlier event; event dates still need individual review.
