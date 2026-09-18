"""Picks an EmbeddingPort backend from settings — mirrors
ai/common/llm.py::build_chat_client exactly (swap models/providers via
.env, never code). See plans/tool_rag.md section 3.5."""

from __future__ import annotations

from app.adapters.embeddings.ollama_embedding_adapter import OllamaEmbeddingAdapter
from app.adapters.embeddings.openai_embedding_adapter import OpenAiCompatibleEmbeddingAdapter
from app.config import settings
from app.ports.tool_rag import EmbeddingPort


def build_embedding_port() -> EmbeddingPort:
    provider = settings.tool_rag_embedding_provider
    if provider == "ollama":
        return OllamaEmbeddingAdapter(
            model=settings.tool_rag_embedding_model,
            base_url=settings.tool_rag_embedding_base_url,
            dimensions=settings.tool_rag_embedding_dimensions,
        )
    if provider == "openai":
        return OpenAiCompatibleEmbeddingAdapter(
            model=settings.tool_rag_embedding_model,
            base_url=settings.tool_rag_embedding_base_url,
            api_key=settings.tool_rag_embedding_api_key,
            dimensions=settings.tool_rag_embedding_dimensions,
        )
    raise ValueError(f"Unknown TOOL_RAG_EMBEDDING_PROVIDER {provider!r} (expected 'ollama' or 'openai')")
