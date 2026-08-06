"""SQLAlchemy models.

Importing every model here guarantees they are all registered on
``Base.metadata`` (needed by ``create_all`` and Alembic autogenerate).
"""

from app.models.base import Base
from app.models.coin import Coin
from app.models.signal import Signal
from app.models.user import User

__all__ = ["Base", "Coin", "Signal", "User"]
