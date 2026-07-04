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
    def __init__(self):
        self.kwargs = None

    def find_nearest(self, **kwargs):
        self.kwargs = kwargs
        return FakeVectorQuery()


@pytest.mark.asyncio
async def test_firestore_vector_rag_retrieve_with_mocks(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.collection = FakeCollection()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)

    results = await rag.retrieve("申訴期限", top_k=1)

    assert len(results) == 1
    assert results[0].content == "申訴期限為事件發生後一年內。"
    assert results[0].metadata["source"] == "性騷擾防治法第13條"
    assert rag.collection.kwargs["vector_field"] == "embedding"
    assert rag.collection.kwargs["query_vector"] == [0.1, 0.2, 0.3]
    assert rag.collection.kwargs["limit"] == 1


@pytest.mark.asyncio
async def test_firestore_vector_rag_embedding_failure_returns_empty(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.collection = FakeCollection()

    async def missing_embedding(text: str):
        return None

    monkeypatch.setattr(rag, "_get_embedding", missing_embedding)

    assert await rag.retrieve("申訴期限") == []
    assert rag.collection.kwargs is None


@pytest.mark.asyncio
async def test_firestore_vector_rag_query_failure_returns_empty(monkeypatch):
    class BrokenCollection:
        def find_nearest(self, **kwargs):
            raise RuntimeError("index missing")

    rag = object.__new__(FirestoreVectorRAG)
    rag.collection = BrokenCollection()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)

    assert await rag.retrieve("申訴期限") == []
