"""
L4 Orderbook streaming module.

Provides real-time L4 orderbook data from Hyperliquid via Hydromancer.
"""

from .book import (
    SCALE,
    Order,
    OrderBook,
    PriceLevel,
    format_price,
    format_size,
    format_value,
    parse_scaled,
)
from .client import (
    L4OrderbookClient,
    HydromancerError,
    RateLimitError,
    PermissionError,
    SubscriptionError,
)
from .sync import OrderbookStream
from .validation import (
    ValidationContext,
    ValidationResult,
    OrderMismatch,
)

__all__ = [
    # Sync wrapper (recommended for most users)
    "OrderbookStream",
    # Async client (for advanced users)
    "L4OrderbookClient",
    # Data structures
    "Order",
    "OrderBook",
    "PriceLevel",
    # Scaling utilities
    "SCALE",
    "parse_scaled",
    "format_value",
    "format_price",
    "format_size",
    # Validation
    "ValidationContext",
    "ValidationResult",
    "OrderMismatch",
    # Exceptions
    "HydromancerError",
    "RateLimitError",
    "PermissionError",
    "SubscriptionError",
]
