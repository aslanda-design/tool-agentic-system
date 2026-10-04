import pytest

from app.domain.quant import models  # noqa: F401 - populates the registry
from app.domain.quant.registry import get, list_models, register
from app.domain.quant.types import ModelMetadata


def test_all_models_registered():
    keys = {m.key for m in list_models()}
    assert keys == {
        "linear_regression",
        "time_series_ar",
        "black_scholes_gbm",
        "heston",
        "hawkes_jump_diffusion",
        "rough_heston",
    }


def test_list_models_returns_param_specs():
    for meta in list_models():
        assert meta.param_specs, f"{meta.key} has no param_specs"


def test_get_unknown_key_returns_none():
    assert get("nonexistent_model") is None


def test_duplicate_registration_raises():
    class _Fake:
        metadata = ModelMetadata(
            key="linear_regression",
            display_name="dup",
            description="",
            family="regression",
            min_history_days=1,
            supports_calibration=True,
        )

    with pytest.raises(ValueError, match="duplicate"):
        register(_Fake())
