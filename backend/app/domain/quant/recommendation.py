"""Deterministic model recommendation — cheap statistical checks over an
asset's persisted daily closes, no LLM involved (see plans/quant_lab.md
section 6.2). Same "rules first, agent as escalation/explanation" posture
the security resolver (domain/listing_scoring.py) already established:
this is the read the `quant` MCP server's recommend_model tool and the
GET /api/quant/assets/{id}/recommendation route both call — an agent
explains this output, it doesn't produce its own competing answer."""

from __future__ import annotations

import numpy as np
from scipy import stats as scipy_stats
from statsmodels.stats.diagnostic import acorr_ljungbox

from app.domain.quant.types import ModelRecommendation

MIN_OBSERVATIONS = 30
KURTOSIS_FLAG_THRESHOLD = 1.0


def recommend_model(closes: list[float]) -> list[ModelRecommendation]:
    prices = np.array(closes, dtype=float)
    if len(prices) < MIN_OBSERVATIONS or np.any(prices <= 0):
        return []
    returns = np.diff(np.log(prices))

    # Volatility clustering: Ljung-Box test on squared returns. A low
    # p-value rejects "no autocorrelation in squared returns" — i.e.
    # evidence of clustering. Heston represents this directly (a real
    # stochastic-volatility process); time_series_ar only picks it up
    # indirectly through return autocorrelation — so Heston is scored
    # slightly above it on the same evidence (see
    # plans/quant_lab_phase7_8.md section 4.2).
    lb_pvalue = 1.0
    try:
        lb = acorr_ljungbox(returns**2, lags=[10], return_df=True)
        lb_pvalue = float(lb["lb_pvalue"].iloc[0])
    except (ValueError, ZeroDivisionError):
        pass  # e.g. a near-constant series — leave lb_pvalue at 1.0 (no evidence)
    clustering_score = max(0.0, 1.0 - lb_pvalue)
    clustering_evidence = f"Ljung-Box test on squared returns (10 lags): p={lb_pvalue:.3f}. " + (
        "Evidence of volatility clustering"
        if lb_pvalue < 0.05
        else "No strong evidence of volatility clustering in this window."
    )

    # Trend strength: R^2 of a linear fit on the raw price level.
    t = np.arange(len(prices), dtype=float)
    slope, intercept = np.polyfit(t, prices, 1)
    fitted = slope * t + intercept
    ss_res = float(np.sum((prices - fitted) ** 2))
    ss_tot = float(np.sum((prices - np.mean(prices)) ** 2))
    trend_r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    recommendations = [
        ModelRecommendation(
            model_key="heston",
            score=round(clustering_score, 3),
            reason=(
                f"{clustering_evidence} — a stochastic-volatility model (Heston) represents this "
                "directly; an AR model on returns only picks it up indirectly."
                if lb_pvalue < 0.05
                else f"{clustering_evidence} Still models a changing volatility level if you want to "
                "explore that, unlike the two simpler models below."
            ),
        ),
        ModelRecommendation(
            model_key="time_series_ar",
            score=round(clustering_score * 0.9, 3),
            reason=(
                f"{clustering_evidence} "
                + (
                    "An AR model on returns is more likely to fit the short-term structure than "
                    "plain regression, though it doesn't model volatility changing directly the way "
                    "Heston does."
                    if lb_pvalue < 0.05
                    else ""
                )
            ),
        ),
        ModelRecommendation(
            model_key="linear_regression",
            score=round(trend_r2, 3),
            reason=(
                f"Linear trend R²={trend_r2:.3f} over the window. "
                + (
                    "A real trend the regression's lag/mean features can pick up."
                    if trend_r2 > 0.3
                    else "A mostly directionless series — regression may not add much over the AR model."
                )
            ),
        ),
        ModelRecommendation(
            model_key="black_scholes_gbm",
            score=0.2,
            reason=(
                "The simplest baseline — constant drift and volatility. Useful as a reference point "
                "against the other models' fan charts, less useful if there's real structure in the "
                "return series (see the other scores here)."
            ),
        ),
    ]
    recommendations.sort(key=lambda r: r.score, reverse=True)

    excess_kurtosis = float(scipy_stats.kurtosis(returns, fisher=True))
    if abs(excess_kurtosis) > KURTOSIS_FLAG_THRESHOLD:
        recommendations.append(
            ModelRecommendation(
                model_key="hawkes_jump_diffusion",
                score=round(min(abs(excess_kurtosis) / 10.0, 1.0), 3),
                reason=(
                    f"Excess kurtosis {excess_kurtosis:.2f} — heavier tails than the other models assume. "
                    "A model with explicit jumps (Hawkes jump-diffusion) is likely a better fit."
                ),
            )
        )
    return recommendations
