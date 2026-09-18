"""EmbeddingPort via any OpenAI-compatible /embeddings endpoint — sibling
of ai/common/llm.py's OpenAiCompatibleChatClient. Opt-in only (see
plans/tool_rag.md section 10): the local Ollama adapter is the default so
tool-doc/query text never has to leave the machine unless explicitly
configured otherwise, same posture as AGENT_PROVIDER=openai already being
an accepted opt-in for chat."""

from __future__ import annotations

import httpx


class OpenAiCompatibleEmbeddingAdapter:
    def __init__(self, model: str, base_url: str, api_key: str, dimensions: int) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        response = httpx.post(
            f"{self._base_url}/embeddings",
            json={"model": self._model, "input": texts},
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
        return [row["embedding"] for row in rows]
