"""Rule-based scoring and decision for the security resolver — see
plans/agentic_asset_mapping.md Phase 4. Pure functions, no I/O: everything
here operates on `Candidate`/`ResolutionContext` already populated by
generation (application/resolve_security.py::generate_candidates).

`SCORER_VERSION` is stored on every `asset_resolutions` row so a later
change to the scoring recipe never silently reinterprets an old decision —
bump it whenever the point values, features, or thresholds below change.
"""

from __future__ import annotations

import difflib
import re
from datetime import date

from app.domain.exchanges import MIC_COUNTRY
from app.domain.listings import Candidate, ResolutionContext, ResolutionStatus

SCORER_VERSION = "rules-v1"

# A candidate with no trade in this many days is never auto-acceptable —
# see `has_recent_price` below.
STALE_AFTER_DAYS = 10

# Decision thresholds (see decide()). Deliberately named constants, not
# magic numbers, so a future retune (once there's labelled data — see
# plans/agentic_asset_mapping.md Phase 7) touches one place.
AUTO_ACCEPT_MIN_SCORE = 80
AUTO_ACCEPT_MIN_MARGIN = 10
AGENT_MIN_SCORE = 50

# Points per feature — chosen so a confirmed ISIN + matching currency alone
# (55 points) is never enough to auto-accept on its own; max = 100.
POINTS_ISIN_CONFIRMED = 25
POINTS_CURRENCY_MATCH = 30
POINTS_EXCHANGE_MATCH = 15  # scaled by exchange_match's 0 / 0.5 / 1.0
POINTS_SYMBOL_MATCH = 10
POINTS_NAME_SIMILARITY = 10  # scaled by name_similarity's 0..1
POINTS_MOST_LIQUID = 10

# An asset with no confirmed ISIN can never auto-accept — see decide()'s
# AUTO_ACCEPT_MIN_SCORE. Capping below that (rather than a fixed 79)
# guarantees the invariant holds even if the point weights above change.
NO_ISIN_SCORE_CAP = AUTO_ACCEPT_MIN_SCORE - 1

# Stripped from both names before comparing — generic fund-naming tokens
# that add noise, not signal, to a similarity score (every UCITS ETF has
# "UCITS ETF" in its name, so it tells us nothing about *which* one this is).
_NAME_NOISE_TOKENS = re.compile(
    r"\b(ucits|etf|acc|dist|inc|class|shares|plc|fund|fonds|sicav)\b", re.IGNORECASE
)
_NAME_PUNCTUATION = re.compile(r"[^\w\s]")


def _normalize_name(name: str) -> str:
    stripped = _NAME_NOISE_TOKENS.sub(" ", name)
    stripped = _NAME_PUNCTUATION.sub(" ", stripped)
    return " ".join(stripped.lower().split())


def _name_similarity(a: str, b: str) -> float:
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _exchange_match(ctx: ResolutionContext, candidate: Candidate) -> float:
    if not ctx.broker_mic:
        # The broker didn't tell us an exchange (e.g. most MyInvestor rows)
        # — don't penalize every candidate for something we can't check.
        return 1.0
    if candidate.mic == ctx.broker_mic:
        return 1.0
    if candidate.mic and MIC_COUNTRY.get(candidate.mic) == MIC_COUNTRY.get(ctx.broker_mic):
        return 0.5
    return 0.0


def _symbol_match(ctx: ResolutionContext, candidate: Candidate) -> bool:
    if ctx.broker_symbol.upper() == (ctx.isin or "").upper():
        # The broker's "symbol" column IS the ISIN (typical of MyInvestor
        # fund exports) — there's no real ticker to compare against.
        return True
    base_symbol = candidate.symbol.split(".", 1)[0].upper()
    return base_symbol == ctx.broker_symbol.upper()


def _most_liquid(candidate: Candidate, gated: list[Candidate]) -> bool:
    if candidate.info is None or candidate.info.avg_volume is None:
        return False
    currency = candidate.info.currency
    same_currency_volumes = [
        c.info.avg_volume
        for c in gated
        if c.info is not None and c.info.currency == currency and c.info.avg_volume is not None
    ]
    if not same_currency_volumes:
        return False
    return candidate.info.avg_volume >= max(same_currency_volumes)


def score_candidates(ctx: ResolutionContext, candidates: list[Candidate], today: date | None = None) -> None:
    """Scores every candidate in place (fills `.features` and `.score`).
    `has_recent_price` is a GATE, not a scored feature: a candidate that
    fails it can never be chosen by decide(), regardless of how high the
    rest of its score is — an unpriceable listing is useless no matter how
    good a match it otherwise looks."""
    today = today or date.today()

    # has_recent_price/days_since_trade need computing before most_liquid
    # (which only looks at "gated" candidates), so this is two passes.
    for candidate in candidates:
        info = candidate.info
        days_since_trade = (today - info.last_trade_date).days if info and info.last_trade_date else None
        candidate.features["days_since_trade"] = days_since_trade
        candidate.features["has_recent_price"] = (
            info is not None and info.currency is not None and days_since_trade is not None and days_since_trade <= STALE_AFTER_DAYS
        )

    gated = [c for c in candidates if c.features["has_recent_price"]]

    for candidate in candidates:
        isin_confirmed = bool(ctx.isin) and bool({"openfigi", "yahoo_isin"} & candidate.found_by)
        currency_match = candidate.info is not None and candidate.info.currency == ctx.currency
        exchange_value = _exchange_match(ctx, candidate)
        symbol_match = _symbol_match(ctx, candidate)
        name_similarity_value = (
            1.0
            if not ctx.broker_name or ctx.broker_name.upper() in {ctx.broker_symbol.upper(), (ctx.isin or "").upper()}
            else _name_similarity(ctx.broker_name, candidate.info.name if candidate.info else candidate.symbol)
        )
        most_liquid = _most_liquid(candidate, gated)

        score = 0
        if isin_confirmed:
            score += POINTS_ISIN_CONFIRMED
        if currency_match:
            score += POINTS_CURRENCY_MATCH
        score += round(POINTS_EXCHANGE_MATCH * exchange_value)
        if symbol_match:
            score += POINTS_SYMBOL_MATCH
        score += round(POINTS_NAME_SIMILARITY * name_similarity_value)
        if most_liquid:
            score += POINTS_MOST_LIQUID
        if not isin_confirmed:
            score = min(score, NO_ISIN_SCORE_CAP)
        if not candidate.features["has_recent_price"]:
            score = 0  # the gate — see docstring

        candidate.features.update(
            {
                "isin_confirmed": isin_confirmed,
                "currency_match": currency_match,
                "exchange_match": exchange_value,
                "symbol_match": symbol_match,
                "name_similarity": round(name_similarity_value, 4),
                "most_liquid": most_liquid,
                "source_count": len(candidate.found_by),
            }
        )
        candidate.score = score


def decide(candidates: list[Candidate], agent_enabled: bool) -> tuple[ResolutionStatus, Candidate | None, str]:
    """Which status this resolution should have, and which candidate (if
    any) to apply. Assumes score_candidates() has already run — reads
    `.features["has_recent_price"]` and `.score`, doesn't compute them."""
    gated = sorted((c for c in candidates if c.features.get("has_recent_price")), key=lambda c: c.score, reverse=True)
    if not gated:
        return ResolutionStatus.NEEDS_REVIEW, None, "no candidate with recent prices"

    top = gated[0]
    second = gated[1] if len(gated) > 1 else None
    margin = top.score - (second.score if second else 0)

    if top.score >= AUTO_ACCEPT_MIN_SCORE and margin >= AUTO_ACCEPT_MIN_MARGIN:
        return ResolutionStatus.AUTO_ACCEPTED, top, ""
    if top.score >= AGENT_MIN_SCORE:
        if agent_enabled:
            return ResolutionStatus.NEEDS_AGENT, None, f"top candidate {top.symbol!r} scored {top.score} — ambiguous, needs review"
        return ResolutionStatus.NEEDS_REVIEW, None, f"top candidate {top.symbol!r} scored {top.score} — ambiguous, needs review"
    return ResolutionStatus.NEEDS_REVIEW, None, f"top candidate {top.symbol!r} only scored {top.score}"
