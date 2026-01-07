"""Tests for OrderBook operations."""

import pytest

from hydromancer_sdk.orderbook import Order, OrderBook, SCALE, parse_scaled, format_value


def test_parse_scaled():
    """Test parsing decimal strings to scaled integers."""
    assert parse_scaled("42000.12345678") == 4200012345678
    assert parse_scaled("100.5") == 10050000000
    assert parse_scaled("100") == 10000000000
    assert parse_scaled("0.00000001") == 1


def test_format_value():
    """Test formatting scaled integers to strings."""
    assert format_value(4200012345678) == "42000.12345678"
    assert format_value(10050000000) == "100.5"
    assert format_value(10000000000) == "100"
    assert format_value(1) == "0.00000001"


def test_roundtrip():
    """Test parse -> format roundtrip."""
    values = ["42000.12345678", "100.5", "100", "0.00000001", "1.5"]
    for v in values:
        assert format_value(parse_scaled(v)) == v


def test_basic_operations():
    """Test basic add/remove operations."""
    book = OrderBook(coin="TEST")

    # Add bids at 4 and 5 (scaled)
    for i in range(3):
        book.add_order(Order(oid=i, user="user1", side="B", px=5 * SCALE, sz=100 * SCALE))
    for i in range(3, 7):
        book.add_order(Order(oid=i, user="user1", side="B", px=4 * SCALE, sz=200 * SCALE))

    # Add asks at 6 (no crossing)
    book.add_order(Order(oid=7, user="user2", side="A", px=6 * SCALE, sz=500 * SCALE))
    book.add_order(Order(oid=8, user="user2", side="A", px=6 * SCALE, sz=500 * SCALE))

    assert book.best_bid() == 5 * SCALE
    assert book.best_ask() == 6 * SCALE
    assert book.spread() == 1 * SCALE

    bids, asks = book.order_count()
    assert bids == 7
    assert asks == 2


def test_matching_ask_crosses_bids():
    """Test matching when ask crosses bids."""
    book = OrderBook(coin="TEST2")
    book.add_order(Order(oid=1, user="user1", side="B", px=100 * SCALE, sz=50 * SCALE))
    book.add_order(Order(oid=2, user="user1", side="B", px=99 * SCALE, sz=100 * SCALE))

    # Add ask at 99 - should match bid at 100 (50) and partial bid at 99
    book.add_order(Order(oid=3, user="user2", side="A", px=99 * SCALE, sz=80 * SCALE))

    # After matching: bid@100 fully filled (removed), bid@99 has 70 left, ask@99 fully filled
    assert book.best_bid() == 99 * SCALE
    assert book.best_ask() is None
    bids, asks = book.order_count()
    assert bids == 1
    assert asks == 0


def test_matching_bid_crosses_asks():
    """Test matching when bid crosses asks."""
    book = OrderBook(coin="TEST3")
    book.add_order(Order(oid=1, user="user1", side="A", px=100 * SCALE, sz=50 * SCALE))
    book.add_order(Order(oid=2, user="user1", side="A", px=101 * SCALE, sz=100 * SCALE))

    # Add bid at 101 - should match ask at 100 (50) and partial ask at 101
    book.add_order(Order(oid=3, user="user2", side="B", px=101 * SCALE, sz=80 * SCALE))

    # After matching: ask@100 fully filled (removed), ask@101 has 70 left, bid@101 fully filled
    assert book.best_ask() == 101 * SCALE
    assert book.best_bid() is None
    bids, asks = book.order_count()
    assert bids == 0
    assert asks == 1


def test_never_crossed():
    """Test that book is never crossed after any add."""
    book = OrderBook(coin="TEST4")
    for i in range(100):
        # Randomly add bids and asks at crossing prices
        book.add_order(Order(oid=i * 2, user="u", side="B", px=(100 + i % 10) * SCALE, sz=10 * SCALE))
        book.add_order(Order(oid=i * 2 + 1, user="u", side="A", px=(95 + i % 10) * SCALE, sz=10 * SCALE))
        # Book should never be crossed
        bid = book.best_bid()
        ask = book.best_ask()
        if bid and ask:
            assert bid < ask, f"Book crossed at i={i}: bid={bid} >= ask={ask}"


def test_remove_order():
    """Test removing orders."""
    book = OrderBook(coin="TEST5")
    book.add_order(Order(oid=1, user="user1", side="B", px=100 * SCALE, sz=50 * SCALE))
    book.add_order(Order(oid=2, user="user1", side="B", px=100 * SCALE, sz=50 * SCALE))

    assert book.remove_order(1)
    bids, _ = book.order_count()
    assert bids == 1

    assert book.remove_order(2)
    bids, _ = book.order_count()
    assert bids == 0

    # Removing non-existent order returns False
    assert not book.remove_order(999)


def test_modify_size():
    """Test modifying order size."""
    book = OrderBook(coin="TEST6")
    book.add_order(Order(oid=1, user="user1", side="B", px=100 * SCALE, sz=50 * SCALE))

    assert book.modify_size(1, 75 * SCALE)
    assert book.best_bid_size() == 75 * SCALE

    # Modifying non-existent order returns False
    assert not book.modify_size(999, 100 * SCALE)


def test_str_methods():
    """Test string formatting methods."""
    book = OrderBook(coin="TEST7")
    book.add_order(Order(oid=1, user="user1", side="B", px=parse_scaled("100.5"), sz=parse_scaled("1.25")))
    book.add_order(Order(oid=2, user="user1", side="A", px=parse_scaled("101.75"), sz=parse_scaled("2.5")))

    assert book.best_bid_str() == "100.5"
    assert book.best_ask_str() == "101.75"
    assert book.best_bid_size_str() == "1.25"
    assert book.best_ask_size_str() == "2.5"
    assert book.spread_str() == "1.25"
