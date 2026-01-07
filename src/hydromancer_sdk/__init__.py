"""
Hydromancer SDK

Enhanced Hyperliquid data access via the Hydromancer API.

Sync example (recommended):
    >>> from hydromancer_sdk import OrderbookStream
    >>> with OrderbookStream(coins=["ETH"]) as stream:
    ...     print(stream.best_bid("ETH"), stream.best_ask("ETH"))
    ...     print(f"Spread: {stream.spread('ETH')}")

Async example:
    >>> from hydromancer_sdk import L4OrderbookClient
    >>> client = L4OrderbookClient(coins=["ETH", "BTC"])
    >>> await client.run()

Validation example:
    >>> ctx = client.start_validation()
    >>> await asyncio.sleep(30)
    >>> result = await client.complete_validation(ctx)
    >>> print(f"Valid: {result.valid}")
"""

from .orderbook import (
    SCALE,
    L4OrderbookClient,
    Order,
    OrderBook,
    OrderbookStream,
    format_price,
    format_size,
    parse_scaled,
    # Validation
    ValidationContext,
    ValidationResult,
    OrderMismatch,
    # Exceptions
    HydromancerError,
    RateLimitError,
    PermissionError,
    SubscriptionError,
)

__version__ = "0.1.0"

__all__ = [
    # Sync wrapper (recommended for most users)
    "OrderbookStream",
    # Async client (for advanced users)
    "L4OrderbookClient",
    # Data structures
    "Order",
    "OrderBook",
    # Scaling utilities
    "SCALE",
    "parse_scaled",
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
    # Version
    "__version__",
]
