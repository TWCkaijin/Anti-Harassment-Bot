"""
測試：默認 RAG 實作
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from backend.app.rag.base import RAGDocument, RAGEmbeddingError, RAGVectorSearchError
from backend.app.rag.default_rag import DefaultRAG
from backend.app.rag.embeddings import EmbeddingClient
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
            "vector_distance": 0.125,
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
    assert results[0].metadata["distance"] == 0.125
    collection = rag.db.collections["rag_judgments"]
    assert collection.kwargs["vector_field"] == "embedding"
    assert collection.kwargs["query_vector"] == [0.1, 0.2, 0.3]
    assert collection.kwargs["limit"] == 1
    assert collection.kwargs["distance_result_field"] == "vector_distance"


@pytest.mark.asyncio
async def test_firestore_vector_rag_embedding_failure_is_not_an_empty_result(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.db = FakeDB()

    async def missing_embedding(text: str):
        return None

    monkeypatch.setattr(rag, "_get_embedding", missing_embedding)

    with pytest.raises(RAGEmbeddingError, match="empty vector"):
        await rag.retrieve("申訴期限")
    assert rag.db.collections == {}


@pytest.mark.asyncio
async def test_firestore_vector_rag_query_failure_is_not_an_empty_result(monkeypatch):
    class BrokenCollection:
        def find_nearest(self, **kwargs):
            raise RuntimeError("index missing")

    rag = object.__new__(FirestoreVectorRAG)
    rag.db = type("BrokenDB", (), {"collection": lambda self, name: BrokenCollection()})()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)

    with pytest.raises(RAGVectorSearchError, match="rag_documents"):
        await rag.retrieve("申訴期限")


@pytest.mark.asyncio
async def test_firestore_vector_rag_returns_empty_only_for_successful_no_data(monkeypatch):
    class EmptyVectorQuery:
        def get(self):
            return []

    class EmptyCollection:
        def find_nearest(self, **kwargs):
            return EmptyVectorQuery()

    rag = object.__new__(FirestoreVectorRAG)
    rag.db = type("EmptyDB", (), {"collection": lambda self, name: EmptyCollection()})()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)

    assert await rag.retrieve("不存在的資料") == []


@pytest.mark.asyncio
async def test_firestore_vector_rag_passes_optional_distance_threshold(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.db = FakeDB()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)

    await rag.retrieve("申訴期限", top_k=1, distance_threshold=0.3)

    assert rag.db.collections["rag_documents"].kwargs["distance_threshold"] == 0.3


@pytest.mark.asyncio
async def test_firestore_vector_rag_interleaves_cross_collection_results(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    def fake_retrieve(
        collection_name: str,
        query_vector: list[float],
        limit: int,
        distance_threshold: float | None = None,
    ):
        return [RAGDocument(content=f"{collection_name}-{index}") for index in range(limit)]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)
    monkeypatch.setattr(rag, "_retrieve_from_collection", fake_retrieve)

    results = await rag.retrieve("過往案例", top_k=3, data_type="all")

    assert [document.content for document in results] == [
        "rag_documents-0",
        "rag_judgments-0",
        "rag_remedies-0",
    ]


@pytest.mark.asyncio
async def test_firestore_vector_rag_globally_ranks_cross_collection_distances(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    distances = {
        "rag_documents": [0.4, 0.9],
        "rag_judgments": [0.1],
        "rag_remedies": [0.25],
    }

    def fake_retrieve(
        collection_name: str,
        query_vector: list[float],
        limit: int,
        distance_threshold: float | None = None,
    ):
        return [
            RAGDocument(
                content=f"{collection_name}-{distance}",
                metadata={"distance": distance},
            )
            for distance in distances[collection_name]
        ]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)
    monkeypatch.setattr(rag, "_retrieve_from_collection", fake_retrieve)

    results = await rag.retrieve("申訴與判決", top_k=3, data_type="all")

    assert [document.metadata["distance"] for document in results] == [0.1, 0.25, 0.4]


@pytest.mark.asyncio
async def test_firestore_vector_rag_queries_independent_collections_concurrently(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    started = threading.Barrier(3, timeout=2)

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    def fake_retrieve(collection_name, **kwargs):
        # Each request must be in flight before any collection can finish.
        started.wait()
        return [RAGDocument(content=collection_name)]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)
    monkeypatch.setattr(rag, "_retrieve_from_collection", fake_retrieve)

    results = await rag.retrieve("申訴與判決", top_k=3, data_type="all")

    assert [document.content for document in results] == [
        "rag_documents",
        "rag_judgments",
        "rag_remedies",
    ]


@pytest.mark.asyncio
async def test_firestore_vector_rag_keeps_event_loop_responsive_during_query(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    loop = asyncio.get_running_loop()
    loop_responded = threading.Event()

    async def fake_embedding(text: str):
        return [0.1, 0.2, 0.3]

    def fake_retrieve(**kwargs):
        loop.call_soon_threadsafe(loop_responded.set)
        assert loop_responded.wait(timeout=2), "Firestore query blocked the stream event loop"
        return [RAGDocument(content="query result")]

    monkeypatch.setattr(rag, "_get_embedding", fake_embedding)
    monkeypatch.setattr(rag, "_retrieve_from_collection", fake_retrieve)

    assert (await rag.retrieve("申訴期限"))[0].content == "query result"


def test_embedding_client_can_be_reused_across_request_event_loops(monkeypatch):
    clients = []

    class LoopBoundClient:
        def __init__(self, **kwargs):
            self.loop = None
            self.closed = False
            self.embeddings = self
            clients.append(self)

        async def create(self, **kwargs):
            current_loop = asyncio.get_running_loop()
            if self.loop is not None and self.loop is not current_loop:
                raise RuntimeError("Embedding transport belongs to a closed request loop")
            self.loop = current_loop
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

    monkeypatch.setattr("backend.app.rag.embeddings.AsyncOpenAI", LoopBoundClient)
    client = EmbeddingClient()
    client.provider = "openrouter"

    assert asyncio.run(client.embed("申訴期限", mode="query")) == [0.1, 0.2, 0.3]
    assert asyncio.run(client.embed("相關案例", mode="query")) == [0.1, 0.2, 0.3]
    assert len(clients) == 2
    assert all(client.closed for client in clients)
