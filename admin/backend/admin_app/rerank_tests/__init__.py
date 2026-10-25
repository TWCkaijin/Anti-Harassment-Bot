"""Admin rerank evaluation endpoints and durable workers."""

import csv
import io
import json

from flask import Blueprint, Response, jsonify, request
from pydantic import ValidationError

from admin_app import jobs
from admin_app.rerank_tests import service
from admin_app.rerank_tests.contracts import (
    Annotation,
    CaseInput,
    DatasetInput,
    PrepareInput,
    RunInput,
)

rerank_bp = Blueprint("rerank_tests", __name__)


def body(model):
    try:
        return model.model_validate(request.get_json(silent=True))
    except ValidationError as exc:
        # Validation errors may embed raw input; expose locations and messages only.
        service.fail(
            "invalid_request",
            "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                for e in exc.errors(include_input=False)
            ),
        )


@rerank_bp.errorhandler(ValueError)
def invalid_scope(_error):
    return jsonify(
        {"error": {"code": "invalid_request", "detail": "參數或資料類型範圍無效。"}}
    ), 422


def revision():
    value = request.headers.get("If-Match", "").strip('"')
    if not value.isdigit() or int(value) < 1:
        service.fail("revision_required", "需要有效的 If-Match revision。", 428)
    return int(value)


def idempotency():
    key = request.headers.get("Idempotency-Key", "")
    if not 1 <= len(key) <= 200:
        service.fail("idempotency_required", "需要 Idempotency-Key。", 428)
    return key


def respond(value, status=200):
    response = jsonify(value)
    response.status_code = status
    if isinstance(value.get("revision"), int):
        response.set_etag(str(value["revision"]))
    return response


@rerank_bp.get("/datasets")
def datasets():
    return respond(service.list_items(service.DATASETS, request.args.get("cursor")))


@rerank_bp.post("/datasets")
def create_dataset():
    return respond(service.create_dataset(body(DatasetInput).name), 201)


@rerank_bp.get("/datasets/<identifier>")
def dataset(identifier):
    return respond(service.dataset(identifier))


@rerank_bp.patch("/datasets/<identifier>")
def update_dataset(identifier):
    value = body(DatasetInput)
    return respond(
        service.update_dataset(identifier, revision(), name=value.name, archived=value.archived)
    )


@rerank_bp.post("/datasets/<identifier>/cases")
def create_case(identifier):
    return respond(service.save_case(identifier, revision(), body(CaseInput)), 201)


@rerank_bp.put("/datasets/<identifier>/cases/<case_id>")
def update_case(identifier, case_id):
    return respond(service.save_case(identifier, revision(), body(CaseInput), case_id))


@rerank_bp.delete("/datasets/<identifier>/cases/<case_id>")
def delete_case(identifier, case_id):
    current = service.dataset(identifier)
    if current["archived"]:
        service.fail("archived", "資料集已封存。", 409)
    if not any(c["id"] == case_id for c in current["cases"]):
        service.fail("not_found", "找不到題目。", 404)
    return respond(
        service.update_dataset(
            identifier, revision(), cases=[c for c in current["cases"] if c["id"] != case_id]
        )
    )


@rerank_bp.post("/datasets/<identifier>/publish")
def publish(identifier):
    return respond(service.publish(identifier, revision()), 201)


@rerank_bp.get("/datasets/<identifier>/revisions/<int:number>")
def published(identifier, number):
    value = service.read(service.REVISIONS, f"{identifier}-{number}")
    return respond({**value, "cases": service.load_object(value["cases_ref"])["cases"]})


@rerank_bp.post("/documents/resolve")
def resolve_document():
    value = body(Annotation)
    return respond(service.document(value.kind, value.doc_id, manual_unit_id=value.manual_unit_id))


@rerank_bp.post("/datasets/<identifier>/prepare")
def prepare(identifier):
    return respond(service.prepare(identifier, revision(), body(PrepareInput), idempotency()), 202)


@rerank_bp.get("/datasets/<identifier>/cases/<case_id>/preparation")
def preparation(identifier, case_id):
    return respond(service.get_preparation(identifier, case_id))


@rerank_bp.get("/runs")
def runs():
    return respond(service.list_items(service.RUNS, request.args.get("cursor")))


@rerank_bp.post("/runs")
def create_run():
    return respond(service.create_run(body(RunInput), idempotency()), 202)


@rerank_bp.get("/runs/<identifier>")
def run(identifier):
    return respond(service.get_run(identifier))


@rerank_bp.post("/runs/<identifier>/archive")
def archive_run(identifier):
    service.read(service.RUNS, identifier)
    service.reference(service.RUNS, identifier).update({"archived": True})
    return respond(service.get_run(identifier))


@rerank_bp.get("/runs/<identifier>/export")
def export_run(identifier):
    value = service.get_run(identifier)
    if request.args.get("format", "json") == "json":
        content, mime, extension = (
            json.dumps(value, ensure_ascii=False, indent=2),
            "application/json",
            "json",
        )
    elif request.args.get("format") == "csv":
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            [
                "case_id",
                "query",
                "status",
                "candidate_recall",
                "before_recall",
                "model_recall",
                "final_recall",
                "delta",
                "rerank_status",
                "elapsed_ms",
            ]
        )
        for row in value["results"]:
            metrics = row.get("metrics") or {}
            query = row["query"]
            if query.lstrip().startswith(("=", "+", "-", "@")):
                query = "'" + query
            writer.writerow(
                [
                    row["case_id"],
                    query,
                    row["status"],
                    *[
                        (metrics.get("stages", {}).get(stage, {}).get("recall"))
                        for stage in ("candidate", "before", "model", "final")
                    ],
                    metrics.get("delta"),
                    row.get("rerank", {}).get("status"),
                    row.get("elapsed_ms"),
                ]
            )
        content, mime, extension = "\ufeff" + stream.getvalue(), "text/csv", "csv"
    else:
        service.fail("invalid_format", "只支援 JSON 或 CSV。")
    return Response(
        content,
        mimetype=mime,
        headers={"Content-Disposition": f'attachment; filename="rerank-{identifier}.{extension}"'},
    )


def register_jobs():
    jobs.register_handler("rerank_test", service.execute_run)
    jobs.register_handler("rerank_prepare", service.execute_prepare)
    jobs.register_observer("rerank_test", service.project_status)


@rerank_bp.get("/documents")
def browse_documents():
    return respond(service.browse_documents(request.args.get("kind"), request.args.get("cursor")))
