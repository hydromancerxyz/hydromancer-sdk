"""
Synchronous wrapper for L4 orderbook streaming.

Runs the async WebSocket client in a background thread, providing
thread-safe sync access to live orderbook data.

Example:
    >>> from hydromancer_sdk import OrderbookStream
    >>> with OrderbookStream(coins=["ETH"]) as stream:
    ...     print(stream.best_bid("ETH"), stream.best_ask("ETH"))
    ...     orders = stream.orders_by_user("ETH", "0xabc...")
"""

import asyncio
import copy
import threading
from typing import Callable, Optional

from .book import Order, OrderBook
from .client import (
    L4OrderbookClient,
    HydromancerError,
    PermissionError,
    RateLimitError,
    SubscriptionError,
)


class OrderbookStream:
    """
    Synchronous orderbook stream with background WebSocket.

    Provides fast, thread-safe access to:
    - best_bid / best_ask (O(1))
    - orders_by_user (O(k) where k = user's order count)

    Args:
        coins: List of coins to subscribe to, or None for all markets
        api_key: Hydromancer API key (or set HYDROMANCER_API_KEY env var)
        on_update: Optional callback after each update.
                   Signature: (stream: OrderbookStream, height: int) -> None

    Example:
        >>> with OrderbookStream(coins=["ETH"]) as stream:
        ...     bid = stream.best_bid("ETH")
        ...     ask = stream.best_ask("ETH")
        ...     whale_orders = stream.orders_by_user("ETH", "0xabc...")
    """

    def __init__(
        self,
        coins: list[str] | None = None,
        api_key: str | None = None,
        on_update: Optional[Callable[["OrderbookStream", int], None]] = None,
    ):
        self._coins = coins
        self._api_key = api_key
        self._user_callback = on_update

        # Thread and async state
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[L4OrderbookClient] = None

        # Synchronization
        self._lock = threading.RLock()
        self._height: int = 0
        self._synced = threading.Event()
        self._stopped = threading.Event()
        self._error: Optional[Exception] = None

    def start(self) -> "OrderbookStream":
        """Start the background streaming thread. Returns self for chaining."""
        if self._thread is not None:
            raise RuntimeError("Stream already started")

        self._stopped.clear()
        self._synced.clear()
        self._error = None

        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Stop the background streaming thread."""
        self._stopped.set()
        # Signal the async client to stop gracefully
        if self._client is not None:
            self._client.request_stop()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def wait_for_sync(self, timeout: float = 30) -> bool:
        """Wait for initial sync. Returns True if synced, False if timeout."""
        synced = self._synced.wait(timeout=timeout)
        if self._error is not None:
            raise self._error
        return synced

    @property
    def synced(self) -> bool:
        """True if initial sync is complete."""
        return self._synced.is_set()

    @property
    def height(self) -> int:
        """Current block height."""
        with self._lock:
            return self._height

    def get_coins(self, sort_by_orders: bool = False) -> list[str]:
        """
        Get list of available coins.

        Args:
            sort_by_orders: If True, sort by total order count (descending).
                           Most liquid markets first.
        """
        with self._lock:
            if self._client is None:
                return []
            coins = list(self._client.books.keys())
            if sort_by_orders:
                coins.sort(
                    key=lambda c: sum(self._client.books[c].order_count()),
                    reverse=True
                )
            return coins

    # =========================================================================
    # Fast-path methods (no full book copy)
    # =========================================================================

    def best_bid(self, coin: str) -> str:
        """Get best bid price as string. O(1)."""
        with self._lock:
            if self._client is None:
                return "N/A"
            book = self._client.books.get(coin)
            return book.best_bid_str() if book else "N/A"

    def best_ask(self, coin: str) -> str:
        """Get best ask price as string. O(1)."""
        with self._lock:
            if self._client is None:
                return "N/A"
            book = self._client.books.get(coin)
            return book.best_ask_str() if book else "N/A"

    def mid_price(self, coin: str) -> str:
        """Get mid price as string. O(1)."""
        with self._lock:
            if self._client is None:
                return "N/A"
            book = self._client.books.get(coin)
            return book.mid_price_str() if book else "N/A"

    def spread(self, coin: str) -> str:
        """Get spread as string. O(1)."""
        with self._lock:
            if self._client is None:
                return "N/A"
            book = self._client.books.get(coin)
            return book.spread_str() if book else "N/A"

    def best_bid_size(self, coin: str) -> str:
        """Get total size at best bid as string. O(1)."""
        with self._lock:
            if self._client is None:
                return "N/A"
            book = self._client.books.get(coin)
            return book.best_bid_size_str() if book else "N/A"

    def best_ask_size(self, coin: str) -> str:
        """Get total size at best ask as string. O(1)."""
        with self._lock:
            if self._client is None:
                return "N/A"
            book = self._client.books.get(coin)
            return book.best_ask_size_str() if book else "N/A"

    def best_bid_users(self, coin: str) -> list[str]:
        """Get list of user addresses with orders at best bid. O(k)."""
        with self._lock:
            if self._client is None:
                return []
            book = self._client.books.get(coin)
            if book is None or not book.bids:
                return []
            # First key is best bid (most negative = highest price)
            level = book.bids.peekitem(0)[1]
            return list(set(o.user for o in level.iter_orders()))

    def best_ask_users(self, coin: str) -> list[str]:
        """Get list of user addresses with orders at best ask. O(k)."""
        with self._lock:
            if self._client is None:
                return []
            book = self._client.books.get(coin)
            if book is None or not book.asks:
                return []
            # First key is best ask (lowest price)
            level = book.asks.peekitem(0)[1]
            return list(set(o.user for o in level.iter_orders()))

    def get_depth(self, coin: str, notional: float, side: str) -> Optional[float]:
        """
        Get depth in bps to consume a given notional. O(levels).

        Args:
            coin: Coin symbol
            notional: Target notional value (e.g., 10000 for $10k)
            side: "B" for bids, "A" for asks

        Returns:
            Basis points from mid (rounded to 2 decimals), or None if insufficient liquidity
        """
        with self._lock:
            if self._client is None:
                return None
            book = self._client.books.get(coin)
            if book is None:
                return None
            bps = book.get_depth(notional, side)
            return round(bps, 2) if bps is not None else None

    def get_notional_depth(self, coin: str, bps: float, side: str) -> int:
        """
        Get total notional within bps from mid. O(levels).

        Args:
            coin: Coin symbol
            bps: Basis points from mid price
            side: "B" for bids, "A" for asks

        Returns:
            Total notional (px * sz) within range (rounded to int)
        """
        with self._lock:
            if self._client is None:
                return 0
            book = self._client.books.get(coin)
            if book is None:
                return 0
            return round(book.get_notional_depth(bps, side))

    def orders_by_user(self, coin: str, user: str) -> list[Order]:
        """
        Get all orders from a user. O(k) where k = user's order count.

        Args:
            coin: Coin symbol
            user: User address (will be lowercased)

        Returns copies of Order objects (safe to use after lock released).
        """
        user = user.lower()
        with self._lock:
            if self._client is None:
                return []
            book = self._client.books.get(coin)
            if book is None:
                return []
            orders = book.orders_by_user(user)
            return [copy.copy(o) for o in orders]

    def user_liquidity(self, coin: str, user: str, side: Optional[str] = None) -> str:
        """
        Get total size for a user's orders as string. O(k) where k = user's order count.

        Args:
            coin: Coin symbol
            user: User address (will be lowercased)
            side: Optional filter - "B" for bids, "A" for asks, None for both
        """
        user = user.lower()
        with self._lock:
            if self._client is None:
                return "0"
            book = self._client.books.get(coin)
            if book is None:
                return "0"
            orders = book.orders_by_user(user, side)
            from .book import format_size
            return format_size(sum(o.sz for o in orders))

    def user_order_count(self, coin: str, user: str) -> int:
        """
        Get count of user's orders. O(k) where k = user's order count.

        Args:
            coin: Coin symbol
            user: User address (will be lowercased)
        """
        user = user.lower()
        with self._lock:
            if self._client is None:
                return 0
            book = self._client.books.get(coin)
            if book is None:
                return 0
            return len(book.orders_by_user(user))

    def users_liquidity(
        self, coin: str, users: list[str], side: Optional[str] = None
    ) -> dict[str, str]:
        """
        Get liquidity for multiple users in one call (single lock acquisition).

        Args:
            coin: Coin symbol
            users: List of user addresses (will be lowercased)
            side: Optional filter - "B" for bids, "A" for asks, None for both

        Returns:
            Dict mapping user address -> liquidity as string
        """
        from .book import format_size
        users = [u.lower() for u in users]
        with self._lock:
            if self._client is None:
                return {u: "0" for u in users}
            book = self._client.books.get(coin)
            if book is None:
                return {u: "0" for u in users}
            result = {}
            for user in users:
                orders = book.orders_by_user(user, side)
                result[user] = format_size(sum(o.sz for o in orders))
            return result

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict:
        """Get current latency and throughput statistics."""
        with self._lock:
            if self._client is None:
                return {
                    "blocks_received": 0,
                    "network_latency": "n=0",
                    "apply_latency": "n=0",
                    "network_latency_avg_ms": 0.0,
                    "apply_latency_avg_ms": 0.0,
                    "avg_diffs_per_block": 0,
                    "raw_bytes": 0,
                    "msg_count": 0,
                }
            return self._client.get_stats()

    def reset_stats(self):
        """Reset statistics counters."""
        with self._lock:
            if self._client is not None:
                self._client.reset_stats()

    # =========================================================================
    # Context manager
    # =========================================================================

    def __enter__(self) -> "OrderbookStream":
        self.start()
        if not self.wait_for_sync(timeout=30):
            self.stop()
            raise TimeoutError("Failed to sync orderbook within 30 seconds")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    # =========================================================================
    # Internal
    # =========================================================================

    def _on_update(self, client: L4OrderbookClient, height: int, diff_count: int):
        """Called by async client after each update."""
        with self._lock:
            self._height = height

        if not self._synced.is_set():
            self._synced.set()

        if self._user_callback is not None:
            try:
                self._user_callback(self, height)
            except Exception:
                pass

    def _run_thread(self):
        """Background thread entry point."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._client = L4OrderbookClient(
                coins=self._coins,
                api_key=self._api_key,
                on_update=self._on_update,
            )
            self._loop.run_until_complete(self._run_until_stopped())
        except (PermissionError, SubscriptionError, RateLimitError) as e:
            self._error = e
            self._synced.set()
        except Exception as e:
            self._error = HydromancerError(f"Stream error: {e}")
            self._synced.set()
        finally:
            self._loop.close()
            self._loop = None
            self._client = None

    async def _run_until_stopped(self):
        """Run the client until stop is requested."""
        task = asyncio.create_task(self._client.run(reconnect=True))

        try:
            # Wait for either the task to complete or stop to be requested
            while not self._stopped.is_set():
                if task.done():
                    # Re-raise any exception from the task
                    task.result()
                    return
                await asyncio.sleep(0.1)
        finally:
            # Ensure task is cancelled and awaited
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
