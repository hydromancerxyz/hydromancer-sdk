#!/usr/bin/env python3
"""
Example: Stream L4 orderbook updates.

Usage:
    export HYDROMANCER_API_KEY="your-api-key"
    uv run examples/stream_orderbook.py           # Default: ETH only
    uv run examples/stream_orderbook.py BTC ETH   # Specific coins
    uv run examples/stream_orderbook.py ALL       # All markets (requires permission)
"""

import asyncio
import sys

from hydromancer_sdk import (
    L4OrderbookClient,
    HydromancerError,
    RateLimitError,
    PermissionError,
    SubscriptionError,
)


def on_update(client: L4OrderbookClient, height: int, diff_count: int):
    """Called after each update is applied."""
    # Print stats every 100 heights
    if height % 100 == 0:
        print(f"\n=== Height {height} ===")
        stats = client.get_stats()
        print(f"  Latency (last 100): network={stats['network_latency_avg_ms']:.1f}ms  "
              f"apply={stats['apply_latency_avg_ms']:.2f}ms")

        # Top 5 markets by order count
        books = [(coin, client.get_book(coin)) for coin in client.get_coins()]
        books.sort(key=lambda x: sum(x[1].order_count()), reverse=True)

        for coin, book in books[:5]:
            bids, asks = book.order_count()
            print(
                f"  {coin}: bid={book.best_bid_str()}x{book.best_bid_size_str()} "
                f"ask={book.best_ask_str()}x{book.best_ask_size_str()} "
                f"spread={book.spread_str()} orders={bids}b/{asks}a"
            )


async def main():
    # Parse command line args
    args = sys.argv[1:]

    if not args:
        coins = ["ETH"]
    elif len(args) == 1 and args[0].upper() == "ALL":
        coins = None
    else:
        coins = [c.upper() for c in args]

    print(f"Coins: {coins if coins else 'ALL'}")

    try:
        client = L4OrderbookClient(coins=coins, on_update=on_update)
    except ValueError as e:
        print(f"Error: {e}")
        print("Set HYDROMANCER_API_KEY environment variable or pass api_key parameter")
        sys.exit(1)

    print(f"Connecting to {client.ws_url}...")

    try:
        await client.run()
    except PermissionError as e:
        print(f"\nPermission error: {e}")
        if coins is None:
            print("Hint: Subscribing to ALL markets requires the ws:l4BookUpdatesAll permission.")
            print("      Try specifying specific coins instead: uv run examples/stream_orderbook.py ETH BTC")
        sys.exit(1)
    except SubscriptionError as e:
        print(f"\nSubscription error: {e}")
        print("Hint: You may be requesting too many coins for your tier.")
        sys.exit(1)
    except RateLimitError as e:
        print(f"\nRate limit error: {e}")
        sys.exit(1)
    except HydromancerError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutdown")


if __name__ == "__main__":
    asyncio.run(main())
