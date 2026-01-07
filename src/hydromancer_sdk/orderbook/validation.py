"""
Orderbook validation utilities.

Provides functionality to validate local orderbook state against API snapshots.
Used for testing, debugging, and ensuring synchronization correctness.

Example:
    >>> ctx = client.start_validation()
    >>> await asyncio.sleep(30)  # Wait for some updates
    >>> result = await client.complete_validation(ctx)
    >>> if not result.valid:
    ...     print(f"Found {len(result.mismatches)} mismatches")
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .book import Order, OrderBook

from .book import Order, OrderBook, parse_scaled, format_price, format_size


@dataclass
class ValidationContext:
    """
    Context for in-progress validation.

    Created by start_validation(), passed to complete_validation().
    Holds the cloned orderbook state and buffered updates.
    """

    start_height: int
    cloned_books: dict[str, "OrderBook"]
    buffered_updates: list[dict] = field(default_factory=list)


@dataclass
class OrderMismatch:
    """
    Details of a single order mismatch between local and API state.

    Attributes:
        coin: Market symbol (e.g., "ETH", "BTC")
        mismatch_type: One of "missing_in_local", "extra_in_local",
                       "size_mismatch", "price_mismatch"
        side: "bids" or "asks"
        oid: Order ID
        details: Human-readable description of the mismatch
        expected: Order data from API snapshot (None if extra_in_local)
        actual: Order data from local state (None if missing_in_local)
    """

    coin: str
    mismatch_type: str
    side: str
    oid: int
    details: str
    expected: Optional[dict] = None
    actual: Optional[dict] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "coin": self.coin,
            "type": self.mismatch_type,
            "side": self.side,
            "oid": self.oid,
            "details": self.details,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class ValidationResult:
    """
    Result of orderbook validation.

    Attributes:
        valid: True if validation passed (or was skipped)
        api_height: Height of the API snapshot
        local_height: Height of local state after replay
        updates_replayed: Number of buffered updates applied
        coins_checked: Number of markets compared
        mismatches: List of order mismatches found
        skipped: True if validation was skipped
        skip_reason: Reason for skip if skipped
    """

    valid: bool
    api_height: int
    local_height: int
    updates_replayed: int
    coins_checked: int
    mismatches: list[OrderMismatch]
    skipped: bool = False
    skip_reason: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "valid": self.valid,
            "api_height": self.api_height,
            "local_height": self.local_height,
            "updates_replayed": self.updates_replayed,
            "coins_checked": self.coins_checked,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


class ValidationMixin:
    """
    Mixin class providing orderbook validation functionality.

    Mixed into L4OrderbookClient to provide start_validation() and
    complete_validation() methods for comparing local state against API.
    """

    # These attributes must be provided by the main class
    _validation_ctx: Optional[ValidationContext]
    books: dict[str, "OrderBook"]
    current_height: int
    coins: Optional[list[str]]

    async def fetch_snapshot(self) -> dict:
        """Must be implemented by main class."""
        raise NotImplementedError

    def start_validation(self) -> ValidationContext:
        """
        Start validation by cloning current state and buffering updates.

        Order of operations matters to avoid race conditions:
        1. Start buffering FIRST (set _validation_ctx)
        2. THEN clone books and record height
        This ensures any updates during cloning are captured.

        Returns:
            ValidationContext with start_height and cloned books

        Example:
            >>> ctx = client.start_validation()
            >>> await asyncio.sleep(30)
            >>> result = await client.complete_validation(ctx)
        """
        if self._validation_ctx is not None:
            raise RuntimeError("Validation already in progress")

        # Step 1: Start buffering FIRST with empty context
        ctx = ValidationContext(
            start_height=0,
            cloned_books={},
            buffered_updates=[],
        )
        self._validation_ctx = ctx

        # Step 2: Clone books and record height (buffering is now active)
        ctx.cloned_books = {coin: book.clone() for coin, book in self.books.items()}
        ctx.start_height = self.current_height

        return ctx

    async def complete_validation(
        self, ctx: ValidationContext, timeout_seconds: float = 5.0
    ) -> ValidationResult:
        """
        Complete validation by fetching API snapshot and comparing.

        This method:
        1. Waits briefly for snapshot publisher to catch up
        2. Fetches a fresh snapshot from the API
        3. Replays buffered updates on cloned books to reach API height
        4. Compares local state against API snapshot

        Args:
            ctx: ValidationContext from start_validation()
            timeout_seconds: Max time to wait for stream to catch up

        Returns:
            ValidationResult with comparison details
        """
        try:
            # Wait for snapshot publisher to catch up
            await asyncio.sleep(1.0)

            # Fetch fresh snapshot from API
            snapshot = await self.fetch_snapshot()
            api_height = snapshot["height"]

            # If API snapshot is older than when we started, skip validation
            if api_height < ctx.start_height:
                return ValidationResult(
                    valid=True,
                    api_height=api_height,
                    local_height=ctx.start_height,
                    updates_replayed=0,
                    coins_checked=0,
                    mismatches=[],
                    skipped=True,
                    skip_reason=f"API snapshot lagging (api={api_height}, start={ctx.start_height})",
                )

            # Wait for live stream to catch up to API height
            start_wait = time.time()
            while self.current_height < api_height:
                if time.time() - start_wait > timeout_seconds:
                    break
                await asyncio.sleep(0.05)

            # Replay buffered updates on cloned books until API height
            updates_replayed = 0
            local_height = ctx.start_height

            # Check for gap in buffered updates
            relevant_updates = [
                u
                for u in ctx.buffered_updates
                if ctx.start_height < u["height"] <= api_height
            ]
            if relevant_updates:
                first_buffered = min(u["height"] for u in relevant_updates)
                if first_buffered > ctx.start_height + 1:
                    gap = first_buffered - ctx.start_height - 1
                    return ValidationResult(
                        valid=False,
                        api_height=api_height,
                        local_height=ctx.start_height,
                        updates_replayed=0,
                        coins_checked=0,
                        mismatches=[],
                        skipped=True,
                        skip_reason=f"Validation gap: start={ctx.start_height}, first_buffered={first_buffered} (gap={gap} blocks)",
                    )

            for update in ctx.buffered_updates:
                if update["height"] <= ctx.start_height:
                    continue
                if update["height"] > api_height:
                    break
                self._apply_update_to_books(ctx.cloned_books, update)
                local_height = update["height"]
                updates_replayed += 1

            # Only compare if we reached the exact API height
            if local_height != api_height:
                return ValidationResult(
                    valid=True,
                    api_height=api_height,
                    local_height=local_height,
                    updates_replayed=updates_replayed,
                    coins_checked=0,
                    mismatches=[],
                    skipped=True,
                    skip_reason=f"Height mismatch after replay (api={api_height}, local={local_height})",
                )

            # Compare against API snapshot
            mismatches = self._compare_with_snapshot(ctx.cloned_books, snapshot)

            return ValidationResult(
                valid=len(mismatches) == 0,
                api_height=api_height,
                local_height=local_height,
                updates_replayed=updates_replayed,
                coins_checked=len(snapshot["markets"]),
                mismatches=mismatches,
            )
        finally:
            self._validation_ctx = None

    def _apply_update_to_books(self, books: dict[str, "OrderBook"], update: dict):
        """Apply a single update to a books dict (used for replay)."""
        diffs = update.get("diffs", [])
        for diff in diffs:
            diff_type = diff.get("type")
            oid = diff["oid"]
            coin = diff["coin"]

            if coin not in books:
                books[coin] = OrderBook(coin=coin)
            book = books[coin]

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

    def _compare_with_snapshot(
        self, local_books: dict[str, "OrderBook"], snapshot: dict
    ) -> list[OrderMismatch]:
        """Compare local orderbooks against API snapshot."""
        mismatches: list[OrderMismatch] = []

        for market in snapshot["markets"]:
            coin = market["coin"]

            local_book = local_books.get(coin)
            if local_book is None:
                if self.coins is not None and coin not in self.coins:
                    continue
                local_book = OrderBook(coin=coin)

            # Compare bids
            mismatches.extend(
                self._compare_side(
                    coin=coin,
                    side="bids",
                    local_orders=local_book.get_all_orders("B"),
                    api_orders=market.get("bids", []),
                )
            )

            # Compare asks
            mismatches.extend(
                self._compare_side(
                    coin=coin,
                    side="asks",
                    local_orders=local_book.get_all_orders("A"),
                    api_orders=market.get("asks", []),
                )
            )

        return mismatches

    def _compare_side(
        self,
        coin: str,
        side: str,
        local_orders: dict[int, "Order"],
        api_orders: list[dict],
    ) -> list[OrderMismatch]:
        """Compare one side of a single market."""
        mismatches: list[OrderMismatch] = []
        api_by_oid: dict[int, dict] = {o["oid"]: o for o in api_orders}

        for oid, api_order in api_by_oid.items():
            if oid in local_orders:
                local_order = local_orders[oid]
                api_px = parse_scaled(api_order["px"])
                api_sz = parse_scaled(api_order["sz"])

                if local_order.px != api_px:
                    mismatches.append(
                        OrderMismatch(
                            coin=coin,
                            mismatch_type="price_mismatch",
                            side=side,
                            oid=oid,
                            details=f"price: local={format_price(local_order.px)} api={api_order['px']}",
                            expected=api_order,
                            actual=self._order_to_dict(local_order),
                        )
                    )
                elif local_order.sz != api_sz:
                    mismatches.append(
                        OrderMismatch(
                            coin=coin,
                            mismatch_type="size_mismatch",
                            side=side,
                            oid=oid,
                            details=f"size: local={format_size(local_order.sz)} api={api_order['sz']}",
                            expected=api_order,
                            actual=self._order_to_dict(local_order),
                        )
                    )
            else:
                mismatches.append(
                    OrderMismatch(
                        coin=coin,
                        mismatch_type="missing_in_local",
                        side=side,
                        oid=oid,
                        details=f"missing: oid={oid} px={api_order['px']} sz={api_order['sz']}",
                        expected=api_order,
                        actual=None,
                    )
                )

        for oid, local_order in local_orders.items():
            if oid not in api_by_oid:
                mismatches.append(
                    OrderMismatch(
                        coin=coin,
                        mismatch_type="extra_in_local",
                        side=side,
                        oid=oid,
                        details=f"extra: oid={oid} px={format_price(local_order.px)} sz={format_size(local_order.sz)}",
                        expected=None,
                        actual=self._order_to_dict(local_order),
                    )
                )

        return mismatches

    def _order_to_dict(self, order: "Order") -> dict:
        """Convert Order to dict for mismatch reporting."""
        return {
            "oid": order.oid,
            "user": order.user,
            "side": order.side,
            "px": format_price(order.px),
            "sz": format_size(order.sz),
        }
