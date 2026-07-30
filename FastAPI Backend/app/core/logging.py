import sys
from loguru import logger
from app.core.config import get_settings

settings = get_settings()

logger.remove()
logger.add(
    sys.stdout,
    level=settings.log_level,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    colorize=True,
)
logger.add(
    "logs/app_{time:YYYY-MM-DD}.log",
    rotation="10 MB",
    retention="7 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
)

# Auto Trading Control Panel (2026-07-30) - additive third sink, a bounded
# in-memory ring buffer so GET /trading/logs has a real backing store. See
# app/core/log_buffer.py. Does not change either sink above.
from app.core.log_buffer import install as _install_log_buffer  # noqa: E402

_install_log_buffer()
