"""
L4 Orderbook streaming client.

Implements snapshot + delta orderbook sync:
1. Subscribe to l4BookUpdates WebSocket stream (buffers updates)
2. Fetch REST snapshot with height
3. Apply buffered updates where height > snapshot_height
4. Continue applying live updates
"""

import asyncio
import json
import os
import time
from typing import Callable, Optional

import aiohttp

from .book import Order, OrderBook, parse_scaled
from .validation import ValidationMixin, ValidationContext, ValidationResult
from .._internal.l4_formats import unpack_l4_book_snapshot
from .._internal.stats import TimingStats


# Default configuration
DEFAULT_REST_URL = "https://api.hydromancer.xyz"
DEFAULT_WS_URL = "wss://api.hydromancer.xyz/ws"


class HydromancerError(Exception):
    """Base exception for Hydromancer SDK errors."""
    pass


class RateLimitError(HydromancerError):
    """Raised when API rate limit is exceeded."""
    pass


class PermissionError(HydromancerError):
    """Raised when API key lacks required permissions."""
    pass


class SubscriptionError(HydromancerError):
    """Raised when subscription fails (too many coins, invalid coins, etc.)."""
    pass


class L4OrderbookClient(ValidationMixin):
    """
    L4 Orderbook streaming client with snapshot + delta sync.

    Args:
        coins: List of coins to subscribe to, or None for all markets.
        api_key: Hydromancer API key. Reads from HYDROMANCER_API_KEY env var if not provided.
        rest_url: REST API URL. Reads from HYDROMANCER_API_URL env var, defaults to api.hydromancer.xyz.
        ws_url: WebSocket URL. Reads from HYDROMANCER_WS_URL env var, defaults to api.hydromancer.xyz/ws.
        on_update: Optional callback called after each update is applied.
                   Signature: (client: L4OrderbookClient, height: int, diff_count: int) -> None

    Example:
        >>> client = L4OrderbookClient(coins=["ETH", "BTC"])
        >>> await client.run()
    """

    MAX_QUEUE_SIZE = 1000

    def __init__(
        self,
        coins: list[str] | None = None,
        api_key: str | None = None,
        rest_url: str | None = None,
        ws_url: str | None = None,
        on_update: Optional[Callable[["L4OrderbookClient", int, int], None]] = None,
    ):
        self.api_key = api_key or os.getenv("HYDROMANCER_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "API key required. Pass api_key parameter or set HYDROMANCER_API_KEY environment variable."
            )

        self.rest_url = rest_url or os.getenv("HYDROMANCER_API_URL", DEFAULT_REST_URL)
        self.ws_url = ws_url or os.getenv("HYDROMANCER_WS_URL", DEFAULT_WS_URL)
        self.on_update = on_update

        # None = all markets, list = specific coins
        self.coins = coins
        self.books: dict[str, OrderBook] = {}
        self.snapshot_height: int = 0
        self.current_height: int = 0
        self.synced = False

        # Queue for updates (producer/consumer pattern)
        self._update_queue: asyncio.Queue | None = None

        # Timing stats (internal)
        self._network_latency_stats = TimingStats()  # block_time -> receive
        self._apply_latency_stats = TimingStats()  # receive -> apply
        self._diff_count_stats = TimingStats()

        # Message size stats
        self._raw_bytes = 0
        self._msg_count = 0
        self._blocks_received = 0

        # Validation state (from ValidationMixin)
        self._validation_ctx: Optional[ValidationContext] = None

        # Shutdown flag
        self._stop_requested = False

    def request_stop(self):
        """Request graceful shutdown of the client."""
        self._stop_requested = True
        # Put a sentinel to wake up the update processor
        if self._update_queue is not None:
            try:
                self._update_queue.put_nowait(None)
            except Exception:
                pass

    def get_book(self, coin: str) -> OrderBook | None:
        """Get orderbook for a specific coin."""
        return self.books.get(coin)

    def get_coins(self) -> list[str]:
        """Get list of coins with orderbooks."""
        return list(self.books.keys())

    async def fetch_snapshot(self) -> dict:
        """Fetch L4 snapshot from REST API (multi-zstd format)."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        if self.coins:
            request = {"type": "l4Book", "coins": self.coins}
        else:
            request = {"type": "l4Book"}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.rest_url}/info",
                json=request,
                headers=headers,
            ) as resp:
                if resp.status == 429:
                    text = await resp.text()
                    raise RateLimitError(f"Rate limit exceeded: {text}")
                elif resp.status == 403:
                    text = await resp.text()
                    raise PermissionError(f"Permission denied: {text}")
                elif resp.status != 200:
                    text = await resp.text()
                    raise HydromancerError(f"Snapshot failed: {resp.status} - {text}")

                data = await resp.read()
                return unpack_l4_book_snapshot(data)

    def load_snapshot(self, snapshot: dict):
        """Load snapshot into orderbooks."""
        self.snapshot_height = snapshot["height"]
        self.books.clear()

        for market in snapshot["markets"]:
            coin = market["coin"]
            book = OrderBook(coin=coin)

            for order_data in market["bids"]:
                order = Order(
                    oid=order_data["oid"],
                    user=order_data["user"],
                    side="B",
                    px=parse_scaled(order_data["px"]),
                    sz=parse_scaled(order_data["sz"]),
                )
                book.add_order(order)

            for order_data in market["asks"]:
                order = Order(
                    oid=order_data["oid"],
                    user=order_data["user"],
                    side="A",
                    px=parse_scaled(order_data["px"]),
                    sz=parse_scaled(order_data["sz"]),
                )
                book.add_order(order)

            self.books[coin] = book

    def apply_update(self, update: dict) -> int:
        """
        Apply a single L4PerpBookUpdate message.
        Returns diff_count (number of order changes applied).
        """
        height = update["height"]

        if height <= self.snapshot_height:
            return 0

        # Buffer update if validation is in progress
        if self._validation_ctx is not None:
            self._validation_ctx.buffered_updates.append(update)

        # Detect gaps in height sequence - crash to force resync
        if self.current_height > 0 and height != self.current_height + 1:
            raise HydromancerError(f"Height gap detected: {self.current_height} -> {height}")

        self.current_height = height

        diffs = update.get("diffs", [])
        for diff in diffs:
            diff_type = diff.get("type")
            oid = diff["oid"]
            coin = diff["coin"]

            if coin not in self.books:
                self.books[coin] = OrderBook(coin=coin)
            book = self.books[coin]

            if diff_type == "new":
                order = Order(
                    oid=oid,
                    user=diff["user"],
                    side=diff["side"],
                    px=parse_scaled(diff["px"]),
                    sz=parse_scaled(diff["sz"]),
                )
                book.add_order(order)
            elif diff_type == "update":
                book.modify_size(oid, parse_scaled(diff["sz"]))
            elif diff_type == "remove":
                book.remove_order(oid)

        return len(diffs)

    def _handle_ws_error(self, message: str) -> None:
        """
        Handle WebSocket error messages and raise appropriate exceptions.

        Error format from server: {"type": "error", "message": "<message>"}
        """
        msg_lower = message.lower()

        # Rate limit errors
        if any(x in msg_lower for x in [
            "rate limit exceeded",
            "message rate limit exceeded",
            "connection limit reached",
            "total subscription limit reached",
            "subscription limit reached for",
        ]):
            raise RateLimitError(message)

        # Permission errors
        if any(x in msg_lower for x in [
            "requires permission",
            "permission limit reached",
            "subscribing to all markets for",  # "...requires permission: ws:l4BookUpdatesAll"
        ]):
            raise PermissionError(message)

        # Subscription/coin limit errors
        if any(x in msg_lower for x in [
            "too many coins",
            "entity limit reached",
            "is not available for tier",
            "unknown subscription type",
        ]):
            raise SubscriptionError(message)

        # Authentication errors
        if any(x in msg_lower for x in [
            "authentication failed",
            "authentication timeout",
        ]):
            raise PermissionError(message)

        # Generic error - raise but don't crash
        raise HydromancerError(message)

    async def _ws_reader(self, ws):
        """Read WS messages and put updates in queue."""
        async for msg in ws:
            if self._stop_requested:
                break

            if msg.type == aiohttp.WSMsgType.TEXT:
                receive_time_ms = time.time() * 1000
                raw_size = len(msg.data)
                data = json.loads(msg.data)

                if data.get("channel") == "l4BookUpdates":
                    update = data["data"]
                    self._raw_bytes += raw_size
                    self._msg_count += 1
                    await self._update_queue.put((update, receive_time_ms))

                elif "height" in data and "diffs" in data:
                    self._raw_bytes += raw_size
                    self._msg_count += 1
                    await self._update_queue.put((data, receive_time_ms))

                elif data.get("type") == "ping":
                    await ws.send_json({"method": "pong"})

                elif data.get("type") == "error":
                    self._handle_ws_error(data.get("message", "Unknown error"))

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _update_processor(self):
        """Process updates from queue."""
        # Drain buffered updates first (no latency stats for buffered)
        while not self._update_queue.empty():
            item = self._update_queue.get_nowait()
            if item is None:  # Shutdown sentinel
                return
            update, _ = item  # Ignore receive_time for buffered updates
            if update["height"] > self.snapshot_height:
                self.apply_update(update)

        while not self._stop_requested:
            qsize = self._update_queue.qsize()
            if qsize > self.MAX_QUEUE_SIZE:
                print(f"Queue overflow ({qsize} > {self.MAX_QUEUE_SIZE}), stopping")
                break

            item = await self._update_queue.get()

            if item is None:  # Shutdown sentinel
                return

            update, receive_time_ms = item

            if update["height"] <= self.snapshot_height:
                continue

            # Calculate network latency (block_time -> receive)
            block_timestamp_ms = update["timestamp"]
            network_latency_ms = receive_time_ms - block_timestamp_ms

            # Apply update and calculate apply latency (receive -> apply)
            apply_start_ms = time.time() * 1000
            diff_count = self.apply_update(update)
            apply_latency_ms = (time.time() * 1000) - apply_start_ms

            self._blocks_received += 1

            if diff_count > 0:
                self._network_latency_stats.record(network_latency_ms)
                self._apply_latency_stats.record(apply_latency_ms)
                self._diff_count_stats.record(diff_count)

            # Call user callback if provided
            if self.on_update:
                self.on_update(self, update["height"], diff_count)

    async def _keepalive_pinger(self, ws):
        """Send periodic pings to keep connection alive."""
        while not self._stop_requested:
            await asyncio.sleep(30)
            if self._stop_requested:
                break
            try:
                await ws.send_json({"method": "ping"})
            except Exception:
                break

    async def _connect_and_stream(self, session: aiohttp.ClientSession):
        """Single connection attempt - connect, subscribe, snapshot, stream."""
        async with session.ws_connect(f"{self.ws_url}?token={self.api_key}") as ws:
            msg = await ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "connected":
                    pass  # Connected successfully

            # Subscribe
            subscription = {"type": "l4BookUpdates"}
            if self.coins:
                subscription["coins"] = self.coins
            await ws.send_json({"method": "subscribe", "subscription": subscription})

            # Start WS reader
            reader_task = asyncio.create_task(self._ws_reader(ws))
            ping_task = asyncio.create_task(self._keepalive_pinger(ws))

            # Wait for updates to buffer before fetching snapshot
            # This gives the snapshot publisher time to write a fresh snapshot
            await asyncio.sleep(2.0)

            # Fetch snapshot
            snapshot = await self.fetch_snapshot()
            self.load_snapshot(snapshot)
            self.synced = True

            # Start processor
            processor_task = asyncio.create_task(self._update_processor())

            try:
                await asyncio.gather(reader_task, processor_task)
            finally:
                # Cancel all tasks and wait for them to complete
                for task in [ping_task, reader_task, processor_task]:
                    if not task.done():
                        task.cancel()
                # Wait for cancellation to complete, suppressing CancelledError
                await asyncio.gather(
                    ping_task, reader_task, processor_task,
                    return_exceptions=True
                )

    async def run(self, reconnect: bool = True, max_retries: int = 0):
        """
        Main run loop. Connects to WebSocket, fetches snapshot, and processes updates.

        Args:
            reconnect: If True, automatically reconnect on disconnect (default: True)
            max_retries: Max reconnection attempts, 0 = unlimited (default: 0)

        Raises:
            PermissionError: If API key lacks required permissions (no retry)
            SubscriptionError: If subscription is invalid or exceeds limits (no retry)
            RateLimitError: If rate limit exceeded (will retry with backoff)
            HydromancerError: For other API errors
        """
        retries = 0

        async with aiohttp.ClientSession() as session:
            while not self._stop_requested:
                try:
                    self._update_queue = asyncio.Queue()
                    self.synced = False
                    await self._connect_and_stream(session)
                except asyncio.CancelledError:
                    return  # Clean exit on cancellation
                except (PermissionError, SubscriptionError):
                    # Permanent failures - don't retry
                    raise
                except Exception as e:
                    if self._stop_requested:
                        return  # Clean exit if stop was requested

                    if not reconnect:
                        raise

                    retries += 1
                    if max_retries > 0 and retries > max_retries:
                        print(f"Max retries ({max_retries}) exceeded, stopping")
                        raise

                    wait_time = min(2 ** retries, 30)  # exponential backoff, max 30s
                    print(f"Disconnected: {e}. Reconnecting in {wait_time}s (attempt {retries})...")
                    await asyncio.sleep(wait_time)

    def get_stats(self) -> dict:
        """Get current statistics."""
        return {
            "blocks_received": self._blocks_received,
            "network_latency": str(self._network_latency_stats),
            "apply_latency": str(self._apply_latency_stats),
            "network_latency_avg_ms": self._network_latency_stats.avg_ms(),
            "apply_latency_avg_ms": self._apply_latency_stats.avg_ms(),
            "avg_diffs_per_block": self._diff_count_stats.avg_ms() if self._diff_count_stats.count > 0 else 0,
            "raw_bytes": self._raw_bytes,
            "msg_count": self._msg_count,
        }

    def reset_stats(self):
        """Reset statistics counters."""
        self._blocks_received = 0
        self._network_latency_stats.reset()
        self._apply_latency_stats.reset()
        self._diff_count_stats.reset()
        self._raw_bytes = 0
        self._msg_count = 0
