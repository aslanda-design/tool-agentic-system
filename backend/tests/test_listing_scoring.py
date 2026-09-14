"""Table-driven tests for domain/listing_scoring.py — see
plans/agentic_asset_mapping.md Phase 4."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.listing_scoring import decide, score_candidates
from app.domain.listings import Candidate, ListingInfo, ResolutionContext, ResolutionStatus

TODAY = date(2026, 9, 14)


def _ctx(**overrides) -> ResolutionContext:
    base = {
        "asset_id": 1,
        "isin": "IE00B4L5Y983",
        "broker_symbol": "EUNL",
        "broker_name": "",
        "broker_exchange": "IBIS2",
        "broker_mic": "XETR",
        "currency": "EUR",
        "broker_key": "interactive_brokers",
    }
    base.update(overrides)
    return ResolutionContext(**base)


def _candidate(
    symbol: str,
    *,
    found_by=("openfigi", "yahoo_isin"),
    currency="EUR",
    mic="XETR",
    name="iShares Core MSCI World",
    last_trade_date=TODAY,
    avg_volume="1000",
) -> Candidate:
    info = ListingInfo(
        symbol=symbol,
        name=name,
        currency=currency,
        quote_type="ETF",
        last_close=Decimal(100),
        last_trade_date=last_trade_date,
        avg_volume=Decimal(avg_volume) if avg_volume is not None else None,
    )
    return Candidate(symbol=symbol, found_by=set(found_by), info=info, mic=mic)


# --- individual features -----------------------------------------------


def test_isin_confirmed_true_for_openfigi_or_yahoo_isin_source():
    c = _candidate("EUNL.DE", found_by=("openfigi",))
    score_candidates(_ctx(), [c])
    assert c.features["isin_confirmed"] is True


def test_isin_confirmed_false_without_source_caps_score_below_auto_accept():
    c = _candidate("EUNL.DE", found_by=("yahoo_text",))
    score_candidates(_ctx(), [c])
    assert c.features["isin_confirmed"] is False
    assert c.score <= 79


def test_no_isin_in_context_caps_score_below_auto_accept():
    c = _candidate("VUSA.L", found_by=("yahoo_text",), currency="GBP", mic="XLON")
    score_candidates(_ctx(isin=None, broker_mic="XLON", currency="GBP", broker_symbol="VUSA"), [c])
    assert c.features["isin_confirmed"] is False
    assert c.score <= 79


def test_currency_match_true_when_equal():
    c = _candidate("EUNL.DE", currency="EUR")
    score_candidates(_ctx(currency="EUR"), [c])
    assert c.features["currency_match"] is True


def test_currency_mismatch_scores_lower():
    match = _candidate("EUNL.DE", currency="EUR")
    mismatch = _candidate("EUNL.L", currency="GBP", mic="XLON")
    score_candidates(_ctx(currency="EUR"), [match, mismatch])
    assert match.features["currency_match"] is True
    assert mismatch.features["currency_match"] is False
    assert match.score > mismatch.score


def test_exchange_match_same_mic_full_credit():
    c = _candidate("EUNL.DE", mic="XETR")
    score_candidates(_ctx(broker_mic="XETR"), [c])
    assert c.features["exchange_match"] == 1.0


def test_exchange_match_same_country_half_credit():
    # XFRA and XETR are both Germany (see domain/exchanges.py MIC_COUNTRY)
    c = _candidate("EUNL.F", mic="XFRA")
    score_candidates(_ctx(broker_mic="XETR"), [c])
    assert c.features["exchange_match"] == 0.5


def test_exchange_match_different_country_zero_credit():
    c = _candidate("EUNL.L", mic="XLON")
    score_candidates(_ctx(broker_mic="XETR"), [c])
    assert c.features["exchange_match"] == 0.0


def test_exchange_match_neutral_when_broker_exchange_unknown():
    """MyInvestor rows have no exchange column — must not penalize every
    candidate for something the broker never told us."""
    c = _candidate("EUNL.L", mic="XLON")
    score_candidates(_ctx(broker_mic=None), [c])
    assert c.features["exchange_match"] == 1.0


def test_symbol_match_true_when_base_ticker_equals_broker_symbol():
    c = _candidate("EUNL.DE")
    score_candidates(_ctx(broker_symbol="EUNL"), [c])
    assert c.features["symbol_match"] is True


def test_symbol_match_false_when_tickers_differ():
    c = _candidate("IWDA.AS")
    score_candidates(_ctx(broker_symbol="EUNL"), [c])
    assert c.features["symbol_match"] is False


def test_symbol_match_true_for_everyone_when_broker_symbol_is_the_isin():
    # MyInvestor: no separate ticker column — resolve_asset uses the ISIN as
    # the asset's "symbol" (see application/asset_resolution.py). Comparing
    # a Yahoo ticker against an ISIN would always fail and unfairly punish
    # every candidate for something that isn't a real mismatch.
    c = _candidate("IWDA.AS")
    score_candidates(_ctx(broker_symbol="IE00B4L5Y983", isin="IE00B4L5Y983"), [c])
    assert c.features["symbol_match"] is True


def test_name_similarity_neutral_when_broker_name_empty():
    c = _candidate("EUNL.DE", name="Completely Different Name")
    score_candidates(_ctx(broker_name=""), [c])
    assert c.features["name_similarity"] == 1.0


def test_name_similarity_reflects_closeness():
    close = _candidate("EUNL.DE", name="iShares Core MSCI World UCITS ETF Acc")
    far = _candidate("XYZ.DE", name="Totally Unrelated Bond Fund")
    score_candidates(_ctx(broker_name="ISHARES CORE MSCI WORLD"), [close, far])
    assert close.features["name_similarity"] > far.features["name_similarity"]
    assert close.score > far.score


def test_most_liquid_picks_highest_volume_within_same_currency():
    low = _candidate("EUNL.DE", avg_volume="100")
    high = _candidate("IWDA.AS", avg_volume="9000", mic="XAMS")
    score_candidates(_ctx(), [low, high])
    assert low.features["most_liquid"] is False
    assert high.features["most_liquid"] is True


def test_most_liquid_compares_only_within_same_currency():
    eur_only = _candidate("EUNL.DE", currency="EUR", avg_volume="10")
    usd_high_volume = _candidate("IWDA.L", currency="USD", mic="XLON", avg_volume="999999")
    score_candidates(_ctx(), [eur_only, usd_high_volume])
    # eur_only is the ONLY EUR candidate, so it's trivially "most liquid" in
    # its own currency bucket even though usd_high_volume has more volume overall.
    assert eur_only.features["most_liquid"] is True


# --- the has_recent_price GATE -------------------------------------------


def test_stale_candidate_is_gated_to_zero_regardless_of_other_features():
    stale = _candidate("EUNL.DE", last_trade_date=date(2026, 8, 1))  # >10 days before TODAY
    score_candidates(_ctx(), [stale], today=TODAY)
    assert stale.features["has_recent_price"] is False
    assert stale.score == 0


def test_candidate_with_no_info_is_gated():
    c = Candidate(symbol="MISSING.DE", found_by={"openfigi"}, info=None, mic="XETR")
    score_candidates(_ctx(), [c], today=TODAY)
    assert c.features["has_recent_price"] is False
    assert c.score == 0


def test_candidate_missing_currency_is_gated_even_with_recent_price():
    info = ListingInfo(
        symbol="X.DE", name="X", currency=None, quote_type=None, last_close=Decimal(1),
        last_trade_date=TODAY, avg_volume=None,
    )
    c = Candidate(symbol="X.DE", found_by={"openfigi"}, info=info, mic="XETR")
    score_candidates(_ctx(), [c], today=TODAY)
    assert c.features["has_recent_price"] is False


# --- decide() -------------------------------------------------------------


def test_decide_auto_accepts_a_clear_winner():
    winner = _candidate("EUNL.DE", mic="XETR", currency="EUR", avg_volume="1000")
    loser = _candidate("IWDA.AS", mic="XAMS", currency="EUR", found_by=("openfigi",), avg_volume="1")
    score_candidates(_ctx(), [winner, loser], today=TODAY)

    status, chosen, note = decide([winner, loser], agent_enabled=False)

    assert status is ResolutionStatus.AUTO_ACCEPTED
    assert chosen is winner
    assert note == ""


def test_decide_needs_agent_when_close_and_agent_enabled():
    # Same exchange, currency and liquidity — differ only in a ticker that
    # matches neither the broker's symbol, so both score identically: a
    # genuine tie (margin 0 < AUTO_ACCEPT_MIN_MARGIN).
    a = _candidate("A.DE", mic="XETR", currency="EUR", avg_volume="1000")
    b = _candidate("B.DE", mic="XETR", currency="EUR", avg_volume="1000")
    score_candidates(_ctx(), [a, b], today=TODAY)
    assert a.score == b.score

    status, chosen, note = decide([a, b], agent_enabled=True)

    assert status is ResolutionStatus.NEEDS_AGENT
    assert chosen is None
    assert note != ""


def test_decide_needs_review_when_close_and_agent_disabled():
    a = _candidate("A.DE", mic="XETR", currency="EUR", avg_volume="1000")
    b = _candidate("B.DE", mic="XETR", currency="EUR", avg_volume="1000")
    score_candidates(_ctx(), [a, b], today=TODAY)

    status, chosen, _note = decide([a, b], agent_enabled=False)

    assert status is ResolutionStatus.NEEDS_REVIEW
    assert chosen is None


def test_decide_needs_review_when_top_score_too_low():
    weak = _candidate("X.DE", found_by=("yahoo_text",), currency="USD", mic="XNYS")
    score_candidates(_ctx(), [weak], today=TODAY)

    status, chosen, _note = decide([weak], agent_enabled=True)

    assert status is ResolutionStatus.NEEDS_REVIEW
    assert chosen is None


def test_decide_needs_review_when_no_candidate_has_recent_price():
    stale = _candidate("EUNL.DE", last_trade_date=date(2026, 1, 1))
    score_candidates(_ctx(), [stale], today=TODAY)

    status, chosen, note = decide([stale], agent_enabled=True)

    assert status is ResolutionStatus.NEEDS_REVIEW
    assert chosen is None
    assert "no candidate" in note


def test_decide_ignores_stale_candidates_when_picking_the_winner():
    stale_high_score_shape = _candidate("STALE.DE", mic="XETR", currency="EUR", last_trade_date=date(2026, 1, 1))
    valid = _candidate("EUNL.DE", mic="XETR", currency="EUR", found_by=("openfigi",))
    score_candidates(_ctx(), [stale_high_score_shape, valid], today=TODAY)

    _status, chosen, _note = decide([stale_high_score_shape, valid], agent_enabled=False)

    assert chosen is valid
