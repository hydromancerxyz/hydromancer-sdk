#!/usr/bin/env python3
"""
Track multiple users' liquidity across markets.

Uses batch method (single lock) for efficiency when tracking many users.

Usage:
    uv run examples/stream_tracked_user_liquidity.py
    uv run examples/stream_tracked_user_liquidity.py BTC ETH SOL
    uv run examples/stream_tracked_user_liquidity.py ALL  # All markets (top 5 shown)

Environment:
    HYDROMANCER_API_KEY: Your Hydromancer API key
"""

import sys
import time

from hydromancer_sdk import OrderbookStream

MAX_DISPLAY = 5  # Max coins to display for ALL markets

# Addresses to track - add any addresses you want to monitor
TRACK_ADDRESSES = [
    "0x010461c14e146ac35fe42271bdc1134ee31c703a"  # HLP
]


def short_addr(addr: str) -> str:
    """Shorten address for display."""
    return f"{addr[:6]}...{addr[-4:]}"


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["ETH"]

    # Handle ALL keyword
    if len(args) == 1 and args[0].upper() == "ALL":
        coins = None
        print(f"Streaming: ALL markets (showing top {MAX_DISPLAY})")
    else:
        coins = [c.upper() for c in args]
        print(f"Streaming: {', '.join(coins)}")
    print(f"Tracking {len(TRACK_ADDRESSES)} addresses\n")

    with OrderbookStream(coins=coins) as stream:
        while True:
            # Get coins to display (all requested, or top N by order count for ALL)
            if coins is None:
                all_coins = stream.get_coins(sort_by_orders=True)
                display_coins = all_coins[:MAX_DISPLAY]
                total_coins = len(all_coins)
            else:
                display_coins = coins
                total_coins = len(coins)

            for coin in display_coins:
                print(f"{coin}:")

                # Batch fetch - single lock acquisition for all users
                bid_liq = stream.users_liquidity(coin, TRACK_ADDRESSES, "B")
                ask_liq = stream.users_liquidity(coin, TRACK_ADDRESSES, "A")

                for addr in TRACK_ADDRESSES:
                    addr_lower = addr.lower()
                    print(f"  {short_addr(addr)}: bid {bid_liq[addr_lower]} / ask {ask_liq[addr_lower]}")

            # Latency stats (rolling avg over last 100 blocks)
            stats = stream.get_stats()
            print(f"\nheight: {stream.height}  markets: {total_coins}")
            print(f"latency (last 100): network={stats['network_latency_avg_ms']:.1f}ms  "
                  f"apply={stats['apply_latency_avg_ms']:.2f}ms")
            print("-" * 40)
            time.sleep(1)


if __name__ == "__main__":
    main()
