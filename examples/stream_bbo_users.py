#!/usr/bin/env python3
"""
Stream users at best bid/offer (BBO) with sizes.

Shows which addresses have orders at the top of book.

Usage:
    uv run examples/stream_bbo_users.py
    uv run examples/stream_bbo_users.py BTC ETH
    uv run examples/stream_bbo_users.py ALL       # All markets (top 5 shown)

Environment:
    HYDROMANCER_API_KEY: Your Hydromancer API key
"""

import sys
import time

from hydromancer_sdk import OrderbookStream

MAX_DISPLAY = 5  # Max coins to display for ALL markets


def short_addr(addr: str) -> str:
    """Shorten address for display."""
    return f"{addr[:6]}...{addr[-4:]}"


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["ETH"]

    # Handle ALL keyword
    if len(args) == 1 and args[0].upper() == "ALL":
        coins = None
        print(f"Streaming: ALL markets (showing top {MAX_DISPLAY})\n")
    else:
        coins = [c.upper() for c in args]
        print(f"Streaming: {', '.join(coins)}\n")

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
                bid = stream.best_bid(coin)
                ask = stream.best_ask(coin)
                bid_sz = stream.best_bid_size(coin)
                ask_sz = stream.best_ask_size(coin)

                print(f"{coin}: {bid} ({bid_sz}) / {ask} ({ask_sz})")

                # Users at best bid
                bid_users = stream.best_bid_users(coin)
                print(f"  Best bid ({len(bid_users)} users): ", end="")
                print(", ".join(short_addr(u) for u in bid_users[:5]), end="")
                if len(bid_users) > 5:
                    print(f" +{len(bid_users) - 5} more", end="")
                print()

                # Users at best ask
                ask_users = stream.best_ask_users(coin)
                print(f"  Best ask ({len(ask_users)} users): ", end="")
                print(", ".join(short_addr(u) for u in ask_users[:5]), end="")
                if len(ask_users) > 5:
                    print(f" +{len(ask_users) - 5} more", end="")
                print()

            # Latency stats (rolling avg over last 100 blocks)
            stats = stream.get_stats()
            print(f"\nheight: {stream.height}  markets: {total_coins}")
            print(f"latency (last 100): network={stats['network_latency_avg_ms']:.1f}ms  "
                  f"apply={stats['apply_latency_avg_ms']:.2f}ms")
            print("-" * 50)
            time.sleep(1)


if __name__ == "__main__":
    main()
