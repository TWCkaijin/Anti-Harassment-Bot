"""Authenticated corpus routes; the admin application supplies session/CSRF middleware."""

import io
import json

from flask import Blueprint, jsonify, request, send_file

from .parsing import MAX_FILE_BYTES, CorpusError
from .service import CorpusService

corpus_bp = Blueprint("corpus", __name__)


@corpus_bp.errorhandler(CorpusError)
def corpus_error(error):
    return jsonify({"detail": error.detail, "code": error.code}), error.status


@corpus_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def page_options():
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError as exc:
        raise CorpusError("limit 必須為整數", status=400) from exc
    return {"limit": limit, "cursor": request.args.get("cursor")}


@corpus_bp.get("/records")
def records():
    kind = request.args.get("kind", "law")
    if kind not in {"law", "judgment", "remedy"}:
        raise CorpusError("不支援的資料類別", status=400)
    return jsonify(CorpusService().list_records(kind, **page_options()))


@corpus_bp.get("/sources")
def sources():
    return jsonify(CorpusService().list_sources(**page_options()))


@corpus_bp.get("/imports")
def imports():
    return jsonify(CorpusService().list_imports(**page_options()))


@corpus_bp.get("/jobs")
def jobs():
    from admin_app.jobs import list_corpus_jobs

    return jsonify(list_corpus_jobs(**page_options()))


@corpus_bp.get("/sources/<source_id>")
def source(source_id):
    return jsonify(CorpusService().source_records(source_id, **page_options()))


@corpus_bp.get("/sources/<source_id>/download")
def download(source_id):
    data, filename = CorpusService().download(source_id)
    return send_file(
        io.BytesIO(data),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@corpus_bp.post("/imports/preview")
def preview():
    upload = request.files.get("file")
    if upload is None:
        raise CorpusError("請選擇檔案")
    try:
        metadata = json.loads(request.form.get("metadata", "{}"))
        column_mapping = (
            json.loads(request.form["column_mapping"]) if "column_mapping" in request.form else None
        )
    except ValueError as exc:
        raise CorpusError("metadata 與 column_mapping 必須為 JSON") from exc
    raw = upload.stream.read(MAX_FILE_BYTES + 1)
    result = CorpusService().queue_preview(
        upload.filename,
        raw,
        request.form.get("kind", "law"),
        request.form.get("title"),
        metadata,
        column_mapping,
    )
    return jsonify(result), 202


@corpus_bp.get("/imports/<import_id>")
def import_detail(import_id):
    return jsonify(CorpusService().import_preview(import_id))


@corpus_bp.post("/imports/<import_id>/mapping")
def mapping(import_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise CorpusError("請提供 column_mapping 與 parse_revision")
    return jsonify(
        CorpusService().map_import(
            import_id, body.get("column_mapping"), body.get("parse_revision")
        )
    ), 202


@corpus_bp.post("/imports/<import_id>/parse")
def resume_parse(import_id):
    return jsonify(CorpusService().resume_parse(import_id)), 202


@corpus_bp.post("/imports/<import_id>/confirm")
def confirm(import_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("plan_hash"), str):
        raise CorpusError("請提供預覽的 plan_hash")
    result = CorpusService().confirm(import_id, body["plan_hash"])
    return jsonify(result), 200 if result.get("status") == "succeeded" else 202


@corpus_bp.delete("/sources/<source_id>")
def delete(source_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "revision" not in body:
        raise CorpusError("刪除完整來源需要目前 revision")
    result = CorpusService().delete_source(source_id, body["revision"])
    return jsonify(result), 200 if result.get("status") == "succeeded" else 202


def register_jobs():
    from admin_app.jobs import JobFailure, register_handler

    def run(method, job, context):
        try:
            return getattr(CorpusService(), method)(job, context)
        except CorpusError as exc:
            raise JobFailure(exc.code, exc.detail, http_status=exc.status) from exc

    register_handler("corpus_import", lambda job, context: run("import_job", job, context))
    register_handler("corpus_delete", lambda job, context: run("delete_job", job, context))
    register_handler("corpus_parse", lambda job, context: run("parse_job", job, context))
