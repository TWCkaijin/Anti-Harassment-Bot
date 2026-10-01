from types import SimpleNamespace

import pytest
from google.api_core.exceptions import FailedPrecondition

from backend.app.rag.base import RAGDocument
from backend.app.rag.firestore_vector import FirestoreVectorRAG


def document(identifier, *, active=True, distance=0.3):
    return SimpleNamespace(
        id=identifier,
        to_dict=lambda: {
            "content": f"完整條文 {identifier}",
            "vector_distance": distance,
            "metadata": {
                "source": "性別平等工作法第12條",
                "law_name": "性別平等工作法",
                "article_number": "12",
                "version": "N0030014@2023-08-16",
                "source_url": "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0030014&flno=12",
                "checked_at": "2026-10-01",
                "corpus_active": active,
            },
        },
    )


class Collection:
    def __init__(self, db, name, field_filter=None):
        self.db, self.name, self.field_filter = db, name, field_filter

    def where(self, *, filter):
        self.db.filters.append(filter)
        return Collection(self.db, self.name, filter)

    def limit(self, value):
        return self

    def get(self):
        return [document("article-12")]

    def find_nearest(self, **kwargs):
        self.db.vector_options.append(kwargs)
        if self.field_filter and self.db.missing_named_index:
            raise FailedPrecondition("index needed")
        if self.field_filter:
            rows = [document("article-12"), document("article-13", distance=0.4)]
        else:
            rows = [document("generic", distance=0.1), document("pending", active=False)]
        return SimpleNamespace(get=lambda: rows)


class DB:
    def __init__(self, *, missing_named_index=False):
        self.filters, self.vector_options = [], []
        self.missing_named_index = missing_named_index

    def collection(self, name):
        return Collection(self, name)


@pytest.mark.asyncio
async def test_hybrid_retrieval_uses_exact_article_and_preserves_metadata(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.db = DB()
    embedded = []

    async def embed(query):
        embedded.append(query)
        return [0.1, 0.2]

    monkeypatch.setattr(rag, "_get_embedding", embed)
    diagnostics = {}
    result = await rag.retrieve(
        "性別平等工作法第十二條", top_k=2, distance_threshold=0.6, diagnostics=diagnostics
    )
    assert [row.doc_id for row in result] == ["article-12", "article-13"]
    assert len(embedded) == 1
    assert result[0].metadata["retrieval_method"] == "exact"
    assert "distance" not in result[0].metadata
    assert result[1].metadata["distance"] == 0.4
    assert result[0].metadata["version"] == "N0030014@2023-08-16"
    assert all(options["distance_threshold"] == 0.6 for options in rag.db.vector_options)
    assert diagnostics["exact_match_count"] == 1
    assert {row["filter_reason"] for row in diagnostics["results"]} >= {
        "duplicate",
        "inactive_or_not_effective",
    }
    assert any(item.value == "性別平等工作法:12" for item in rag.db.filters)


@pytest.mark.asyncio
async def test_existing_exact_and_vector_work_before_named_vector_index_deployment(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.db = DB(missing_named_index=True)

    async def embed(query):
        return [0.1]

    monkeypatch.setattr(rag, "_get_embedding", embed)
    diagnostics = {}
    result = await rag.retrieve("性別平等工作法第12條", top_k=2, diagnostics=diagnostics)
    assert [row.doc_id for row in result] == ["article-12", "generic"]
    assert diagnostics["law_name_index_status"] == "missing_index"


def test_merge_does_not_remove_same_id_from_different_collections():
    documents = [
        RAGDocument("one", {"collection": "law"}, doc_id="same"),
        RAGDocument("two", {"collection": "remedy"}, doc_id="same"),
    ]
    assert len(FirestoreVectorRAG._merge_results([], documents, 2, {})) == 2


def test_known_legacy_bad_deadline_is_filtered_before_import_rollout():
    doc = RAGDocument(
        "性騷擾防治法第13條：事件發生後一年內申訴", {"source": "舊資料"}, doc_id="old"
    )
    diagnostics = {}
    assert FirestoreVectorRAG._merge_results([], [doc], 3, diagnostics) == []
    assert diagnostics["results"][0]["filter_reason"] == "legacy_outdated_article_13_one_year_rule"


@pytest.mark.asyncio
async def test_invalid_threshold_does_not_call_embedding(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)

    async def embed(query):
        raise AssertionError("invalid settings must not call paid embedding")

    monkeypatch.setattr(rag, "_get_embedding", embed)
    with pytest.raises(ValueError, match="threshold"):
        await rag.retrieve("query", distance_threshold=float("nan"))
