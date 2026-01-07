"""
Orderbook data structures.

Provides efficient L4 orderbook representation with:
- SortedDict for price-sorted levels (like BTreeMap)
- Doubly-linked lists for FIFO queue at each price level
- O(1) order lookup by order ID

All prices and sizes are stored as integers scaled by 10^8 for precision.
Use format_price() and format_size() for display.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Optional

from sortedcontainers import SortedDict

if TYPE_CHECKING:
    import pandas as pd


# Fixed-point multiplier (10^8) for price/size representation.
# All px and sz values are stored as raw_value * SCALE.
SCALE = 10**8


def parse_scaled(value: str) -> int:
    """
    Parse decimal string to scaled integer.

    Args:
        value: Decimal string (e.g., "42000.12345678")

    Returns:
        Scaled integer (value * 10^8)
    """
    f = float(value)
    if f < 0:
        raise ValueError("Value cannot be negative")
    return int(round(f * SCALE))


def format_value(scaled: int) -> str:
    """
    Format scaled integer to decimal string.

    Trims trailing zeros and decimal point if not needed.

    Args:
        scaled: Integer scaled by 10^8

    Returns:
        Decimal string (e.g., "42000.5", "100")
    """
    s = f"{scaled / SCALE:.8f}"
    s = s.rstrip("0")
    s = s.rstrip(".")
    return s


# Aliases for clarity
format_price = format_value
format_size = format_value


@dataclass
class Order:
    """Single order in the book. Prices/sizes are scaled by 10^8."""

    oid: int
    user: str
    side: str  # "B" or "A"
    px: int  # price * 10^8
    sz: int  # size * 10^8
    prev_oid: Optional[int] = None
    next_oid: Optional[int] = None


class PriceLevel:
    """
    Doubly-linked list of orders at a price level.
    Maintains FIFO order (time priority) with O(1) operations.
    """

    def __init__(self):
        self.orders: dict[int, Order] = {}  # oid -> Order
        self.head_oid: Optional[int] = None
        self.tail_oid: Optional[int] = None

    def __len__(self) -> int:
        return len(self.orders)

    def is_empty(self) -> bool:
        return self.head_oid is None

    def push_back(self, order: Order):
        """Add order to end of queue (newest)."""
        if order.oid in self.orders:
            return

        order.prev_oid = self.tail_oid
        order.next_oid = None
        self.orders[order.oid] = order

        if self.tail_oid is not None:
            self.orders[self.tail_oid].next_oid = order.oid

        self.tail_oid = order.oid

        if self.head_oid is None:
            self.head_oid = order.oid

    def remove(self, oid: int) -> bool:
        """Remove order by oid. Returns True if found."""
        if oid not in self.orders:
            return False

        order = self.orders.pop(oid)

        # Update prev's next pointer
        if order.prev_oid is not None:
            self.orders[order.prev_oid].next_oid = order.next_oid
        else:
            self.head_oid = order.next_oid

        # Update next's prev pointer
        if order.next_oid is not None:
            self.orders[order.next_oid].prev_oid = order.prev_oid
        else:
            self.tail_oid = order.prev_oid

        return True

    def get(self, oid: int) -> Optional[Order]:
        """Get order by oid."""
        return self.orders.get(oid)

    def total_size(self) -> int:
        """Sum of all order sizes at this level (scaled)."""
        return sum(o.sz for o in self.orders.values())

    def iter_orders(self) -> Iterator[Order]:
        """Iterate orders in FIFO order (oldest first)."""
        oid = self.head_oid
        while oid is not None:
            order = self.orders[oid]
            yield order
            oid = order.next_oid


class OrderBook:
    """
    Single market orderbook.

    Uses:
    - SortedDict for price-sorted levels (like BTreeMap)
    - PriceLevel (linked list) for FIFO queue at each price
    - dict for O(1) order lookup by oid

    All prices and sizes are scaled integers (multiply by 10^8).
    Use format_price() and format_size() for display.
    """

    def __init__(self, coin: str):
        self.coin = coin
        # Bids: highest price first (negative keys for reverse sort)
        self.bids: SortedDict[int, PriceLevel] = SortedDict()
        # Asks: lowest price first
        self.asks: SortedDict[int, PriceLevel] = SortedDict()
        # O(1) lookup: oid -> (side, px)
        self.oid_to_loc: dict[int, tuple[str, int]] = {}
        # O(1) lookup: user -> set of oids (for orders_by_user queries)
        self.user_to_oids: dict[str, set[int]] = {}
        # Cached order counts for O(1) access
        self._bid_count: int = 0
        self._ask_count: int = 0

    def _get_book(self, side: str) -> SortedDict[int, PriceLevel]:
        return self.bids if side == "B" else self.asks

    def _match_order(self, order: Order, maker_book: SortedDict, is_bid: bool):
        """Match incoming order against maker book, consuming size from both sides."""
        while maker_book and order.sz > 0:
            best_key, level = maker_book.peekitem(0)
            # Bids match asks (positive keys), asks match bids (negative keys)
            best_px = best_key if is_bid else -best_key
            crosses = (best_px <= order.px) if is_bid else (best_px >= order.px)
            if not crosses:
                break

            orders_to_remove: list[Order] = []
            for maker in level.iter_orders():
                if order.sz <= 0:
                    break
                match_sz = min(order.sz, maker.sz)
                order.sz -= match_sz
                maker.sz -= match_sz
                if maker.sz <= 0:
                    orders_to_remove.append(maker)

            for maker in orders_to_remove:
                level.remove(maker.oid)
                del self.oid_to_loc[maker.oid]
                # Maintain user index
                if maker.user in self.user_to_oids:
                    self.user_to_oids[maker.user].discard(maker.oid)
                    if not self.user_to_oids[maker.user]:
                        del self.user_to_oids[maker.user]
                # Maintain order count (maker side is opposite of taker)
                if is_bid:
                    self._ask_count -= 1
                else:
                    self._bid_count -= 1

            if level.is_empty():
                del maker_book[best_key]

    def add_order(self, order: Order):
        """Add order to book with matching."""
        if order.oid in self.oid_to_loc:
            return  # Skip duplicate

        # Match against opposite side
        if order.side == "B":
            self._match_order(order, self.asks, is_bid=True)
        else:
            self._match_order(order, self.bids, is_bid=False)

        # Add remaining size to book
        if order.sz > 0:
            book = self._get_book(order.side)
            key = -order.px if order.side == "B" else order.px
            if key not in book:
                book[key] = PriceLevel()
            book[key].push_back(order)
            self.oid_to_loc[order.oid] = (order.side, order.px)
            # Maintain user index
            if order.user not in self.user_to_oids:
                self.user_to_oids[order.user] = set()
            self.user_to_oids[order.user].add(order.oid)
            # Maintain order count
            if order.side == "B":
                self._bid_count += 1
            else:
                self._ask_count += 1

    def remove_order(self, oid: int) -> bool:
        """Remove order from book."""
        if oid not in self.oid_to_loc:
            return False

        side, px = self.oid_to_loc.pop(oid)
        book = self._get_book(side)
        key = -px if side == "B" else px

        if key in book:
            level = book[key]
            # Get user before removing for index maintenance
            order = level.get(oid)
            if order:
                # Maintain user index
                if order.user in self.user_to_oids:
                    self.user_to_oids[order.user].discard(oid)
                    if not self.user_to_oids[order.user]:
                        del self.user_to_oids[order.user]
            level.remove(oid)
            if level.is_empty():
                del book[key]
            # Maintain order count
            if side == "B":
                self._bid_count -= 1
            else:
                self._ask_count -= 1
            return True
        return False

    def modify_size(self, oid: int, new_sz: int) -> bool:
        """Modify order size (new_sz is scaled)."""
        if oid not in self.oid_to_loc:
            return False

        side, px = self.oid_to_loc[oid]
        book = self._get_book(side)
        key = -px if side == "B" else px

        if key in book:
            order = book[key].get(oid)
            if order:
                order.sz = new_sz
                return True
        return False

    def best_bid(self) -> Optional[int]:
        """Get best bid price (highest), scaled."""
        if not self.bids:
            return None
        # First key is most negative = highest price
        return -self.bids.peekitem(0)[0]

    def best_ask(self) -> Optional[int]:
        """Get best ask price (lowest), scaled."""
        if not self.asks:
            return None
        return self.asks.peekitem(0)[0]

    def best_bid_size(self) -> Optional[int]:
        """Get total size at best bid, scaled."""
        if not self.bids:
            return None
        level = self.bids.peekitem(0)[1]
        return level.total_size()

    def best_ask_size(self) -> Optional[int]:
        """Get total size at best ask, scaled."""
        if not self.asks:
            return None
        level = self.asks.peekitem(0)[1]
        return level.total_size()

    def spread(self) -> Optional[int]:
        """Get spread, scaled."""
        bid, ask = self.best_bid(), self.best_ask()
        if bid is not None and ask is not None:
            return ask - bid
        return None

    def mid_price(self) -> Optional[int]:
        """Get mid price, scaled."""
        bid, ask = self.best_bid(), self.best_ask()
        if bid is not None and ask is not None:
            return (bid + ask) // 2
        return None

    def order_count(self) -> tuple[int, int]:
        """Return (bid_count, ask_count). O(1)."""
        return self._bid_count, self._ask_count

    # Convenience methods for formatted output
    def best_bid_str(self) -> str:
        """Get best bid price as formatted string."""
        bid = self.best_bid()
        return format_price(bid) if bid is not None else "N/A"

    def best_ask_str(self) -> str:
        """Get best ask price as formatted string."""
        ask = self.best_ask()
        return format_price(ask) if ask is not None else "N/A"

    def best_bid_size_str(self) -> str:
        """Get best bid size as formatted string."""
        sz = self.best_bid_size()
        return format_size(sz) if sz is not None else "N/A"

    def best_ask_size_str(self) -> str:
        """Get best ask size as formatted string."""
        sz = self.best_ask_size()
        return format_size(sz) if sz is not None else "N/A"

    def spread_str(self) -> str:
        """Get spread as formatted string."""
        s = self.spread()
        return format_price(s) if s is not None else "N/A"

    def mid_price_str(self) -> str:
        """Get mid price as formatted string."""
        m = self.mid_price()
        return format_price(m) if m is not None else "N/A"

    # =========================================================================
    # Query Helpers
    # =========================================================================

    def get_order(self, oid: int) -> Optional[Order]:
        """
        Get an order by its ID. O(1) lookup.

        Args:
            oid: Order ID

        Returns:
            Order object or None if not found
        """
        if oid not in self.oid_to_loc:
            return None
        side, px = self.oid_to_loc[oid]
        book = self._get_book(side)
        key = -px if side == "B" else px
        if key in book:
            return book[key].get(oid)
        return None

    def orders_by_user(self, user: str, side: Optional[str] = None) -> list[Order]:
        """
        Get all orders from a specific user address. O(k) where k = user's order count.

        Args:
            user: User address to filter by
            side: Optional filter - "B" for bids, "A" for asks, None for both

        Returns:
            List of Order objects from the specified user

        Example:
            >>> whale_orders = book.orders_by_user("0xabc123...")
            >>> total_size = sum(o.sz for o in whale_orders) / SCALE
        """
        if user not in self.user_to_oids:
            return []

        orders = []
        for oid in self.user_to_oids[user]:
            order = self.get_order(oid)
            if order and (side is None or order.side == side):
                orders.append(order)
        return orders

    def get_depth(self, notional: float, side: str) -> Optional[float]:
        """
        Get depth in bps required to consume a given notional amount.

        Args:
            notional: Target notional value (px * sz) to consume
            side: "B" for bids, "A" for asks

        Returns:
            Basis points from mid price to consume the notional, or None if
            mid price unavailable or insufficient liquidity

        Example:
            >>> bps = book.get_depth(5000, "A")  # How deep to buy $5000?
            >>> print(f"Depth: {bps:.1f} bps")
        """
        mid = self.mid_price()
        if mid is None:
            return None

        book = self.bids if side == "B" else self.asks
        target_notional = int(notional * SCALE * SCALE)  # Scale for px * sz
        consumed_notional = 0
        worst_px = None

        for key, level in book.items():
            px = -key if side == "B" else key
            for order in level.iter_orders():
                order_notional = px * order.sz  # Both scaled, so SCALE^2
                consumed_notional += order_notional
                worst_px = px
                if consumed_notional >= target_notional:
                    break
            if consumed_notional >= target_notional:
                break

        if worst_px is None:
            return None

        # Calculate bps from mid
        bps = abs(worst_px - mid) / mid * 10000
        return bps

    def get_notional_depth(self, bps: float, side: str) -> float:
        """
        Get total notional liquidity within a given bps range from mid.

        Args:
            bps: Basis points from mid price
            side: "B" for bids, "A" for asks

        Returns:
            Total notional value (px * sz summed) within the bps range

        Example:
            >>> notional = book.get_notional_depth(10, "B")  # Liquidity within 10 bps on bid side
            >>> print(f"Bid liquidity: ${notional:,.0f}")
        """
        mid = self.mid_price()
        if mid is None:
            return 0.0

        book = self.bids if side == "B" else self.asks
        bps_fraction = bps / 10000

        if side == "B":
            # Bids: include prices from mid down to mid * (1 - bps_fraction)
            min_px = int(mid * (1 - bps_fraction))
            max_px = mid
        else:
            # Asks: include prices from mid up to mid * (1 + bps_fraction)
            min_px = mid
            max_px = int(mid * (1 + bps_fraction))

        total_notional = 0
        for key, level in book.items():
            px = -key if side == "B" else key
            if min_px <= px <= max_px:
                for order in level.iter_orders():
                    total_notional += px * order.sz  # SCALE^2

        # Descale: px * sz = (px_real * SCALE) * (sz_real * SCALE) = notional_real * SCALE^2
        return total_notional / (SCALE * SCALE)

    def get_all_orders(self, side: str) -> dict[int, "Order"]:
        """
        Get all orders on a side as a dict keyed by oid.

        Args:
            side: "B" for bids, "A" for asks

        Returns:
            Dict mapping oid -> Order for all orders on the specified side
        """
        book = self.bids if side == "B" else self.asks
        result: dict[int, Order] = {}
        for level in book.values():
            for order in level.iter_orders():
                result[order.oid] = order
        return result

    def clone(self) -> "OrderBook":
        """
        Create a deep copy of this orderbook.

        Returns:
            New OrderBook with copied state (not linked to original)
        """
        new_book = OrderBook(self.coin)

        # Clone bids
        for key, level in self.bids.items():
            new_level = PriceLevel()
            for order in level.iter_orders():
                new_order = Order(
                    oid=order.oid,
                    user=order.user,
                    side=order.side,
                    px=order.px,
                    sz=order.sz,
                )
                new_level.push_back(new_order)
                new_book.oid_to_loc[order.oid] = (order.side, order.px)
                if order.user not in new_book.user_to_oids:
                    new_book.user_to_oids[order.user] = set()
                new_book.user_to_oids[order.user].add(order.oid)
            new_book.bids[key] = new_level

        # Clone asks
        for key, level in self.asks.items():
            new_level = PriceLevel()
            for order in level.iter_orders():
                new_order = Order(
                    oid=order.oid,
                    user=order.user,
                    side=order.side,
                    px=order.px,
                    sz=order.sz,
                )
                new_level.push_back(new_order)
                new_book.oid_to_loc[order.oid] = (order.side, order.px)
                if order.user not in new_book.user_to_oids:
                    new_book.user_to_oids[order.user] = set()
                new_book.user_to_oids[order.user].add(order.oid)
            new_book.asks[key] = new_level

        # Copy cached counts
        new_book._bid_count = self._bid_count
        new_book._ask_count = self._ask_count

        return new_book
