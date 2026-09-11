"""Gold Signal Bot - Engulfing Break setups on XAU/USD, delivered to WhatsApp.

Deliberately standalone. Nothing here imports from the trading bot in
`FastAPI Backend/`, and nothing there imports from here: different instrument
(OANDA XAU_USD, not Binance XAUUSDT), different data source, no exchange
client, no orders, no shared database. The two can be deployed, broken and
restarted independently - which is the entire point, because the trading bot
is mid-measurement and must not be disturbed.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
