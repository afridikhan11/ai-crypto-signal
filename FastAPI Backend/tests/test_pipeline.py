"""Bias engine + end-to-end signal generator tests."""

from datetime import datetime, timezone

from app.strategy.bias import compute_bias
from app.strategy.signal_generator import SignalGenerator
from tests.conftest import (
    build_df, uptrend_closes, downtrend_closes, bullish_ltf_with_setup,
)


# ---------------------------------------------------------------- bias
def test_bias_bullish():
    frames = {"4h": build_df(uptrend_closes(250), freq="4h"),
              "1h": build_df(uptrend_closes(250), freq="1h")}
    bias = compute_bias(frames, ["4h", "1h"])
    assert bias.direction == "LONG"
    assert bias.is_actionable
    assert 0 < bias.strength <= 1.0


def test_bias_bearish():
    frames = {"4h": build_df(downtrend_closes(250), freq="4h"),
              "1h": build_df(downtrend_closes(250), freq="1h")}
    bias = compute_bias(frames, ["4h", "1h"])
    assert bias.direction == "SHORT"


# ---------------------------------------------------------------- generator
def test_generator_returns_wellformed_or_none():
    """With default thresholds the pipeline must never raise and must return
    either None or a structurally valid signal."""
    gen = SignalGenerator("BTCUSDT")
    frames = {"4h": build_df(uptrend_closes(250), freq="4h"),
              "1h": build_df(uptrend_closes(250), freq="1h")}
    ltf = bullish_ltf_with_setup()
    sig = gen.generate(ltf, frames, ["4h", "1h"], btc_trend="up",
                       now=datetime(2024, 1, 1, 13, 0, tzinfo=timezone.utc))
    if sig is not None:
        assert sig["direction"] in ("LONG", "SHORT")
        assert sig["stop_loss"] < sig["entry"] < sig["tp1"] < sig["tp2"] < sig["tp3"]
        assert sig["risk_reward"] >= 1.5
        assert 0 <= sig["confidence"] <= 100


def test_generator_produces_long_when_thresholds_relaxed():
    """A crafted bullish MTF setup should yield a valid LONG when we don't
    gate on confidence — this exercises the full risk model."""
    gen = SignalGenerator("BTCUSDT", min_confidence=0, min_rr=0.0)
    frames = {"4h": build_df(uptrend_closes(250), freq="4h"),
              "1h": build_df(uptrend_closes(250), freq="1h")}
    ltf = bullish_ltf_with_setup()
    sig = gen.generate(ltf, frames, ["4h", "1h"], btc_trend="up",
                       now=datetime(2024, 1, 1, 13, 0, tzinfo=timezone.utc))
    assert sig is not None
    assert sig["direction"] == "LONG"
    # Risk model invariants
    assert sig["stop_loss"] < sig["entry"]
    assert sig["tp1"] < sig["tp2"] < sig["tp3"]
    assert sig["risk_reward"] > 0
    assert sig["ai_model_version"] == "2.0.0"


def test_generator_no_bias_returns_none():
    """Flat/rangebound HTF => no actionable bias => no signal."""
    flat = [100.0 + (i % 2) * 0.01 for i in range(250)]
    frames = {"4h": build_df(flat, freq="4h"), "1h": build_df(flat, freq="1h")}
    gen = SignalGenerator("BTCUSDT")
    sig = gen.generate(build_df(flat, freq="15min"), frames, ["4h", "1h"])
    assert sig is None
