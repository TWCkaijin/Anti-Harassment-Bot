"""
測試：默認 RAG 實作
"""

import pytest

from backend.app.rag.base import RAGDocument
from backend.app.rag.default_rag import DefaultRAG
from backend.app.rag.firestore_vector import FirestoreVectorRAG


@pytest.mark.asyncio
async def test_default_rag_loads_builtin_documents():
    rag = DefaultRAG()
    assert rag.document_count() > 0


@pytest.mark.asyncio
async def test_retrieve_returns_results():
    rag = DefaultRAG()
    results = await rag.retrieve("性騷擾申訴")
    assert len(results) > 0
    assert all(isinstance(doc, RAGDocument) for doc in results)


@pytest.mark.asyncio
async def test_retrieve_as_context_returns_string():
    rag = DefaultRAG()
    context = await rag.retrieve_as_context("性騷擾申訴通報管道")
    assert isinstance(context, str)
    assert len(context) > 0


@pytest.mark.asyncio
async def test_add_documents():
    rag = DefaultRAG()
    initial_count = rag.document_count()
    new_doc = RAGDocument(content="測試文件內容", metadata={"source": "test"})
    await rag.add_documents([new_doc])
    assert rag.document_count() == initial_count + 1


class FakeFirestoreDoc:
    id = "doc-1"

    def to_dict(self):
        return {
            "content": "申訴期限為事件發生後一年內。",
            "metadata": {"source": "性騷擾防治法第13條"},
        }


class FakeVectorQuery:
    def get(self):
        return [FakeFirestoreDoc()]


class FakeCollection:
    def __init__(self, name: str):
        self.name = name
        self.kwargs = None

    def find_nearest(self, **kwargs):
        self.kwargs = kwargs
        return FakeVectorQuery()


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name: str):
        collection = FakeCollection(name)
        self.collections[name] = collection
        return collection


@pytest.mark.asyncio
async def test_firestore_vector_rag_retrieve_with_mocks(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.db = FakeDB()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)

    results = await rag.retrieve("申訴期限", top_k=1, data_type="judgment")

    assert len(results) == 1
    assert results[0].content == "申訴期限為事件發生後一年內。"
    assert results[0].metadata["source"] == "性騷擾防治法第13條"
    collection = rag.db.collections["rag_judgments"]
    assert collection.kwargs["vector_field"] == "embedding"
    assert collection.kwargs["query_vector"] == [0.1, 0.2, 0.3]
    assert collection.kwargs["limit"] == 1


@pytest.mark.asyncio
async def test_firestore_vector_rag_embedding_failure_returns_empty(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.db = FakeDB()

    async def missing_embedding(text: str):
        return None

    monkeypatch.setattr(rag, "_get_embedding", missing_embedding)

    assert await rag.retrieve("申訴期限") == []
    assert rag.db.collections == {}


@pytest.mark.asyncio
async def test_firestore_vector_rag_query_failure_returns_empty(monkeypatch):
    class BrokenCollection:
        def find_nearest(self, **kwargs):
            raise RuntimeError("index missing")

    rag = object.__new__(FirestoreVectorRAG)
    rag.db = type("BrokenDB", (), {"collection": lambda self, name: BrokenCollection()})()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)

    assert await rag.retrieve("申訴期限") == []


@pytest.mark.asyncio
async def test_firestore_vector_rag_interleaves_cross_collection_results(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    def fake_retrieve(collection_name: str, query_vector: list[float], limit: int):
        return [RAGDocument(content=f"{collection_name}-{index}") for index in range(limit)]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)
    monkeypatch.setattr(rag, "_retrieve_from_collection", fake_retrieve)

    results = await rag.retrieve("過往案例", top_k=3, data_type="all")

    assert [document.content for document in results] == [
        "rag_documents-0",
        "rag_judgments-0",
        "rag_remedies-0",
    ]
