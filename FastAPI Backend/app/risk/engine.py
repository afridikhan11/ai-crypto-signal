"""
Metric pipeline order - an internal implementation detail of
`app/risk/risk_engine.py`.

Kept in its own module so the reporting order is a single, reviewable
list, and so `risk_engine.py` remains the one and only definition of
`class RiskEngine` (enforced by tests/test_legacy_isolation.py).
"""
from __future__ import annotations

from app.risk.metrics import (
    evaluate_concentration,
    evaluate_correlation,
    evaluate_daily_loss,
    evaluate_drawdown,
    evaluate_leverage,
    evaluate_liquidation_ratio,
    evaluate_margin_usage,
    evaluate_open_risk,
)

# Reporting order - see risk_engine.py's module docstring for the tiers.
METRIC_PIPELINE = (
    evaluate_daily_loss,
    evaluate_drawdown,
    evaluate_liquidation_ratio,
    evaluate_margin_usage,
    evaluate_open_risk,
    evaluate_leverage,
    evaluate_concentration,
    evaluate_correlation,
)
