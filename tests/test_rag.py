"""
測試：默認 RAG 實作
"""
import pytest

from backend.app.rag.base import RAGDocument
from backend.app.rag.default_rag import DefaultRAG


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
