"""EmbeddingPort via Ollama's /api/embed — the local-first default for tool
RAG (plans/tool_rag.md section 3.5), sibling to ai/common/llm.py's
OllamaChatClient on the chat side. `nomic-embed-text` is the default model
(768 dims, small, no new heavyweight dependency — same "numpy/statsmodels
over scikit-learn" posture as Quant Lab)."""

from __future__ import annotations

from ollama import Client as _OllamaSdkClient


class OllamaEmbeddingAdapter:
    def __init__(self, model: str, base_url: str, dimensions: int) -> None:
        self._model = model
        self._client = _OllamaSdkClient(host=base_url)
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embed(model=self._model, input=texts)
        return [list(vector) for vector in response.embeddings]
