"""The model plugin registry. Adding a model is: one new file in
domain/quant/models/ implementing QuantModel, one import line in
domain/quant/models/__init__.py. Nothing here, in the use case, the API
route, the MCP server, or the frontend needs to change — see
plans/quant_lab.md section 4.3. Same explicit-registration shape as
container.BROKER_ADAPTERS: no auto-discovery magic."""

from __future__ import annotations

from app.domain.quant.types import ModelMetadata, QuantModel

MODEL_REGISTRY: dict[str, QuantModel] = {}


def register(model: QuantModel) -> QuantModel:
    key = model.metadata.key
    if key in MODEL_REGISTRY:
        raise ValueError(f"duplicate quant model key: {key!r}")
    MODEL_REGISTRY[key] = model
    return model


def get(key: str) -> QuantModel | None:
    return MODEL_REGISTRY.get(key)


def list_models() -> list[ModelMetadata]:
    return [m.metadata for m in MODEL_REGISTRY.values()]
