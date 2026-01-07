# Hydromancer SDK

Enhanced Hyperliquid data access via the Hydromancer API.

## Installation

```bash
git clone https://github.com/hydromancerxyz/hydromancer-sdk.git
cd hydromancer-sdk
pip install -e .
```

Or install directly from GitHub:
```bash
pip install git+https://github.com/hydromancerxyz/hydromancer-sdk.git
```

## Quick Start

### Synchronous API (Recommended)

The simplest way to stream orderbook data using a context manager:

```python
from hydromancer_sdk import OrderbookStream

with OrderbookStream(coins=["ETH"]) as stream:
    print(f"Best bid: {stream.best_bid('ETH')}")
    print(f"Best ask: {stream.best_ask('ETH')}")
    print(f"Spread: {stream.spread('ETH')}")
```

### Async API

For async applications:

```python
import asyncio
from hydromancer_sdk import L4OrderbookClient

async def main():
    client = L4OrderbookClient(coins=["ETH"])
    await client.run()

asyncio.run(main())
```

## API Key

Get your API key at [hydromancer.xyz](https://hydromancer.xyz).

```bash
export HYDROMANCER_API_KEY="your-api-key"
```

Or pass directly:
```python
stream = OrderbookStream(coins=["ETH"], api_key="your-api-key")
```

---

## OrderbookStream Reference

`OrderbookStream` is a synchronous wrapper that runs the WebSocket client in a background thread, providing thread-safe access to live orderbook data.

### Constructor

```python
OrderbookStream(
    coins: list[str] | None = None,  # Coins to subscribe, None = all markets
    api_key: str | None = None,       # API key (or use env var)
    on_update: Callable | None = None # Optional callback on each update
)
```

### Context Manager

```python
with OrderbookStream(coins=["ETH", "BTC"]) as stream:
    # stream is synced and ready
    while True:
        print(stream.best_bid("ETH"))
        time.sleep(1)
```

### Manual Lifecycle

```python
stream = OrderbookStream(coins=["ETH"])
stream.start()
stream.wait_for_sync(timeout=30)  # Wait for initial snapshot
# ... use stream ...
stream.stop()
```

---

## Available Methods

### Price Data (O(1))

| Method | Returns | Description |
|--------|---------|-------------|
| `best_bid(coin)` | `str` | Best bid price (e.g., `"3245.50"`) |
| `best_ask(coin)` | `str` | Best ask price |
| `mid_price(coin)` | `str` | Mid price `(bid + ask) / 2` |
| `spread(coin)` | `str` | Spread `ask - bid` |
| `best_bid_size(coin)` | `str` | Total size at best bid |
| `best_ask_size(coin)` | `str` | Total size at best ask |

### Users at BBO (O(k) where k = users at level)

| Method | Returns | Description |
|--------|---------|-------------|
| `best_bid_users(coin)` | `list[str]` | Addresses with orders at best bid |
| `best_ask_users(coin)` | `list[str]` | Addresses with orders at best ask |

### Depth Metrics (O(levels))

| Method | Returns | Description |
|--------|---------|-------------|
| `get_depth(coin, notional, side)` | `float` | Basis points from mid to consume `notional` USD |
| `get_notional_depth(coin, bps, side)` | `int` | Total USD liquidity within `bps` from mid |

**Example:**
```python
# How many bps to consume $10,000 on the bid side?
depth_bps = stream.get_depth("ETH", 10000, "B")  # e.g., 2.5

# How much liquidity within 10 bps of mid on ask side?
liquidity = stream.get_notional_depth("ETH", 10, "A")  # e.g., 250000
```

### User Order Queries (O(k) where k = user's orders)

| Method | Returns | Description |
|--------|---------|-------------|
| `orders_by_user(coin, user)` | `list[Order]` | All orders from a user address |
| `user_liquidity(coin, user, side=None)` | `str` | Total size of user's orders |
| `user_order_count(coin, user)` | `int` | Number of user's orders |
| `users_liquidity(coin, users, side=None)` | `dict[str, str]` | Batch query for multiple users |

**Example:**
```python
# Get HLP's bid liquidity
hlp = "0x010461c14e146ac35fe42271bdc1134ee31c703a"
bid_size = stream.user_liquidity("ETH", hlp, "B")

# Batch query multiple addresses (single lock acquisition)
addresses = ["0xabc...", "0xdef...", "0x123..."]
liquidity = stream.users_liquidity("ETH", addresses, "B")
for addr, size in liquidity.items():
    print(f"{addr}: {size}")
```

### Stream State

| Property/Method | Returns | Description |
|-----------------|---------|-------------|
| `height` | `int` | Current block height |
| `synced` | `bool` | True if initial sync complete |
| `get_coins(sort_by_orders=False)` | `list[str]` | Available coins, optionally sorted by order count |
| `get_stats()` | `dict` | Latency and throughput statistics |
| `reset_stats()` | None | Clear statistics (usually not needed) |

---

## Latency Metrics

The SDK tracks two latency components:

### Network Latency (`network_latency_avg_ms`)

**What it measures:** Time from block creation on Hyperliquid to when the WebSocket message is received by your client.

```
Block created (timestamp in message) → Message received by SDK
```

**Includes:**
- Hyperliquid block propagation
- Hydromancer processing and forwarding
- Network transit to your client
- WebSocket receive overhead

**Typical values:** 100-300ms depending on your location relative to servers.

### Apply Latency (`apply_latency_avg_ms`)

**What it measures:** Time to apply the update to the local orderbook data structure.

```
Message received → Update applied to OrderBook
```

**Includes:**
- JSON parsing (done before this measurement)
- Order insertions/modifications/removals
- Index updates (oid_to_loc, user_to_oids)

**Typical values:** 0.01-0.5ms (very fast, mostly CPU-bound).

### Reading Stats

```python
stats = stream.get_stats()

print(f"Network latency: {stats['network_latency_avg_ms']:.1f}ms")
print(f"Apply latency: {stats['apply_latency_avg_ms']:.2f}ms")

# Also available:
# stats['network_latency']  - Full string: "n=100 avg=215.00ms min=98.00ms max=350.00ms"
# stats['apply_latency']    - Full string for apply latency
# stats['blocks_received']  - Total blocks processed
# stats['raw_bytes']        - Total bytes received
# stats['msg_count']        - Total messages received
```

**Note:** Stats are computed over a **rolling window of the last 100 blocks**, so they represent recent performance, not cumulative averages.

---

## Subscribing to All Markets

```python
# Subscribe to all markets (requires appropriate API permissions)
with OrderbookStream(coins=None) as stream:
    # Get top 5 markets by order count
    top_coins = stream.get_coins(sort_by_orders=True)[:5]

    for coin in top_coins:
        print(f"{coin}: {stream.best_bid(coin)} / {stream.best_ask(coin)}")
```

---

## Update Callback

React to each orderbook update:

```python
def on_update(stream: OrderbookStream, height: int):
    """Called after each block is applied."""
    print(f"Height {height}: ETH mid = {stream.mid_price('ETH')}")

with OrderbookStream(coins=["ETH"], on_update=on_update) as stream:
    while True:
        time.sleep(10)
```

---

## Async Client Reference

For async applications, use `L4OrderbookClient` directly:

```python
import asyncio
from hydromancer_sdk import L4OrderbookClient

def on_update(client: L4OrderbookClient, height: int, diff_count: int):
    book = client.get_book("ETH")
    if book:
        print(f"ETH: {book.best_bid_str()} / {book.best_ask_str()}")

async def main():
    client = L4OrderbookClient(
        coins=["ETH", "BTC"],
        on_update=on_update
    )
    await client.run()

asyncio.run(main())
```

### Client Methods

| Method | Description |
|--------|-------------|
| `get_book(coin)` | Get `OrderBook` for a coin |
| `get_coins()` | List of available coins |
| `get_stats()` | Latency statistics |
| `reset_stats()` | Clear statistics |

### OrderBook Methods

| Method | Description |
|--------|-------------|
| `best_bid()` / `best_ask()` | Best prices (scaled int) |
| `best_bid_str()` / `best_ask_str()` | Best prices (formatted string) |
| `best_bid_size_str()` / `best_ask_size_str()` | Size at BBO |
| `spread()` / `spread_str()` | Spread |
| `mid_price()` / `mid_price_str()` | Mid price |
| `order_count()` | Returns `(bid_count, ask_count)` |
| `orders_by_user(user, side=None)` | Get user's orders |
| `get_depth(notional, side)` | Depth in bps for notional |
| `get_notional_depth(bps, side)` | Liquidity within bps |

---

## Error Handling

```python
from hydromancer_sdk import (
    OrderbookStream,
    HydromancerError,
    RateLimitError,
    PermissionError,
    SubscriptionError,
)

try:
    with OrderbookStream(coins=None) as stream:  # All markets
        pass
except PermissionError as e:
    print(f"Need higher tier API key: {e}")
except SubscriptionError as e:
    print(f"Too many coins or invalid subscription: {e}")
except RateLimitError as e:
    print(f"Rate limited: {e}")
except HydromancerError as e:
    print(f"General error: {e}")
```

---

## Examples

Run the included examples:

```bash
# Stream quotes with depth metrics
uv run examples/stream_quotes.py ETH BTC

# Stream all markets (top 5 shown)
uv run examples/stream_quotes.py ALL

# Track users at best bid/offer
uv run examples/stream_bbo_users.py ETH

# Track specific addresses' liquidity
uv run examples/stream_tracked_user_liquidity.py ETH

# Async client with top markets
uv run examples/stream_orderbook.py ETH BTC SOL

# Periodic validation against REST snapshots
uv run examples/validate_orderbook.py ETH --interval 60
```

---

## How It Works

### Snapshot + Delta Sync

1. **Subscribe** to WebSocket `l4BookUpdates` stream
2. **Buffer** incoming updates while waiting
3. **Fetch** REST snapshot with height
4. **Load** snapshot into local orderbooks
5. **Apply** buffered updates where `height > snapshot_height`
6. **Continue** processing live updates

This ensures no updates are missed during initial sync.

### Data Structures

- **SortedDict** for price levels (O(log n) insert, O(1) best price)
- **Doubly-linked list** at each price level (FIFO queue, O(1) operations)
- **Hash maps** for O(1) order lookup by `oid` and user index

### Reconnection

The client automatically reconnects on disconnect with exponential backoff (2s, 4s, 8s, ... max 30s). Permanent errors (permission denied, invalid subscription) are not retried.

---

## License

MIT
