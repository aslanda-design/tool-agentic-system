"""Import every model module so its @register decorator runs at import
time and populates domain.quant.registry.MODEL_REGISTRY. Explicit list, on
purpose — same shape as container.BROKER_ADAPTERS, no auto-discovery
magic. Adding a model: one new file here, one import line below."""

from . import (  # noqa: F401
    black_scholes_gbm,
    hawkes_jump_diffusion,
    heston,
    linear_regression,
    rough_heston,
    time_series_ar,
)
