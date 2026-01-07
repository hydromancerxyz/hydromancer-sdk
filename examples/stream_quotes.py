#!/usr/bin/env python3
"""
Stream live orderbook quotes and depth metrics.

Usage:
    uv run examples/stream_quotes.py
    uv run examples/stream_quotes.py BTC ETH SOL
    uv run examples/stream_quotes.py ALL          # All markets (top 5 shown)

Environment:
    HYDROMANCER_API_KEY: Your Hydromancer API key
"""

import sys
import time

from hydromancer_sdk import OrderbookStream

MAX_DISPLAY = 5  # Max coins to display for ALL markets


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
                # Best bid/ask with sizes
                print(f"{coin}: {stream.best_bid(coin)} ({stream.best_bid_size(coin)}) / "
                      f"{stream.best_ask(coin)} ({stream.best_ask_size(coin)})  "
                      f"spread: {stream.spread(coin)}")

                # Depth metrics
                depth_bid = stream.get_depth(coin, 10000, "B")
                depth_ask = stream.get_depth(coin, 10000, "A")
                liq_bid = stream.get_notional_depth(coin, 10, "B")
                liq_ask = stream.get_notional_depth(coin, 10, "A")

                print(f"  $10k depth: bid {depth_bid} bps / ask {depth_ask} bps")
                print(f"  10bps liq:  bid ${liq_bid:,} / ask ${liq_ask:,}")

            # Latency stats (rolling avg over last 100 blocks)
            stats = stream.get_stats()
            print(f"\nheight: {stream.height}  markets: {total_coins}")
            print(f"latency (last 100): network={stats['network_latency_avg_ms']:.1f}ms  "
                  f"apply={stats['apply_latency_avg_ms']:.2f}ms")
            print("-" * 50)
            time.sleep(1)


if __name__ == "__main__":
    main()
