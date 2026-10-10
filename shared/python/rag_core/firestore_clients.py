"""Explicit, environment-bound access to the shared business database.

Firestore Rules deny browser access. Server identities are constrained by IAM;
callers never supply a database or an arbitrary collection path.
"""

import os

import firebase_admin
from firebase_admin import credentials, firestore

from rag_core.core.config import get_settings


def initialize_firebase() -> None:
    if firebase_admin._apps:
        return
    settings = get_settings()
    project_id = settings.firebase_project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
    if settings.environment in {"ci", "test"} or os.getenv("FIRESTORE_EMULATOR_HOST"):
        project_id = project_id or "demo-rag-harass-bot"
        firebase_admin.initialize_app(options={"projectId": project_id})
        return
    options = {"projectId": project_id} if project_id else None
    path = settings.firebase_admin_credential_path
    if settings.environment == "development" and path.is_file():
        firebase_admin.initialize_app(credentials.Certificate(str(path)), options=options)
    else:
        firebase_admin.initialize_app(options=options)


def get_business_db():
    settings = get_settings()
    if not settings.firestore_database_id:
        raise RuntimeError("Business database identity is missing")
    initialize_firebase()
    return firestore.client(database_id=settings.firestore_database_id)


def collection_for_kind(kind: str) -> str:
    settings = get_settings()
    mapping = {
        "law": settings.rag_collection_name,
        "judgment": settings.rag_judgment_collection_name,
        "remedy": settings.rag_remedy_collection_name,
    }
    if kind not in mapping:
        raise ValueError("Unsupported retrieval collection kind")
    return mapping[kind]
