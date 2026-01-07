#!/usr/bin/env python3
"""
Periodically validate orderbook synchronization.

Compares local orderbook state against API snapshots to ensure
the streaming client maintains correct state. Mostly useful for debugging.

Usage:
    uv run examples/validate_orderbook.py
    uv run examples/validate_orderbook.py BTC ETH --interval 30
    uv run examples/validate_orderbook.py ALL --interval 120 --duration 600

Environment:
    HYDROMANCER_API_KEY: Your Hydromancer API key
"""

import argparse
import asyncio
import sys

from hydromancer_sdk import (
    L4OrderbookClient,
    HydromancerError,
    PermissionError,
    SubscriptionError,
)


async def run_validation(
    coins: list[str] | None,
    interval_seconds: int,
    duration_seconds: int | None,
):
    """Run client with periodic validation."""
    validation_count = 0
    validation_passed = 0
    validation_failed = 0
    validation_skipped = 0

    def on_update(client: L4OrderbookClient, height: int, diff_count: int):
        if height % 100 == 0:
            stats = client.get_stats()
            print(f"  Height {height}: latency (last 100): "
                  f"network={stats['network_latency_avg_ms']:.1f}ms apply={stats['apply_latency_avg_ms']:.2f}ms")

    try:
        client = L4OrderbookClient(coins=coins, on_update=on_update)
    except ValueError as e:
        print(f"Error: {e}")
        print("Set HYDROMANCER_API_KEY environment variable")
        sys.exit(1)

    print("Starting orderbook validation")
    print(f"  Coins: {coins if coins else 'ALL'}")
    print(f"  Validation interval: {interval_seconds}s")
    if duration_seconds:
        print(f"  Duration: {duration_seconds}s")
    print()

    async def validation_loop():
        nonlocal validation_count, validation_passed, validation_failed, validation_skipped

        # Wait for initial sync
        while not client.synced:
            await asyncio.sleep(0.1)

        print(f"Synced at height {client.current_height}")
        print()

        start_time = asyncio.get_event_loop().time()

        while True:
            await asyncio.sleep(interval_seconds)

            # Check duration limit
            if duration_seconds:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= duration_seconds:
                    print(f"\nDuration limit reached ({duration_seconds}s)")
                    break

            validation_count += 1
            print(f"\n=== Validation #{validation_count} ===")

            # Start validation (clones books, starts buffering)
            ctx = client.start_validation()
            print(f"  Started at height {ctx.start_height}")

            # Complete validation (fetches snapshot, replays, compares)
            try:
                result = await client.complete_validation(ctx)
            except HydromancerError as e:
                print(f"  Validation failed: {e}")
                validation_failed += 1
                continue

            if result.skipped:
                validation_skipped += 1
                print(f"  SKIPPED - {result.skip_reason}")
            elif result.valid:
                validation_passed += 1
                print(
                    f"  PASSED - height: {result.api_height}, "
                    f"replayed: {result.updates_replayed}, "
                    f"coins: {result.coins_checked}"
                )
            else:
                validation_failed += 1
                print(
                    f"  FAILED - {len(result.mismatches)} mismatches "
                    f"(height: {result.api_height}, replayed: {result.updates_replayed})"
                )

                # Show first few mismatches
                for m in result.mismatches[:5]:
                    print(f"    [{m.mismatch_type}] {m.coin} {m.side} oid={m.oid}")
                if len(result.mismatches) > 5:
                    print(f"    ... and {len(result.mismatches) - 5} more")

    async def client_task():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    # Run both tasks
    client_runner = asyncio.create_task(client_task())
    validator = asyncio.create_task(validation_loop())

    try:
        await validator
    except asyncio.CancelledError:
        pass
    finally:
        client_runner.cancel()
        try:
            await client_runner
        except asyncio.CancelledError:
            pass

    # Print summary
    print()
    print("=" * 40)
    print("Validation Summary")
    print("=" * 40)
    print(f"  Total: {validation_count}")
    print(f"  Passed: {validation_passed}")
    print(f"  Failed: {validation_failed}")
    print(f"  Skipped: {validation_skipped}")

    if validation_count > 0:
        actual = validation_passed + validation_failed
        if actual > 0:
            rate = validation_passed / actual * 100
            print(f"  Success rate: {rate:.1f}%")

    return validation_failed == 0


def main():
    parser = argparse.ArgumentParser(description="Validate orderbook synchronization")
    parser.add_argument(
        "coins",
        nargs="*",
        default=["ETH"],
        help="Coins to validate (default: ETH). Use ALL for all markets.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Validation interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Total run duration in seconds (default: unlimited)",
    )

    args = parser.parse_args()

    # Handle ALL keyword
    if args.coins and len(args.coins) == 1 and args.coins[0].upper() == "ALL":
        coins = None
    else:
        coins = [c.upper() for c in args.coins] if args.coins else ["ETH"]

    try:
        success = asyncio.run(run_validation(coins, args.interval, args.duration))
        sys.exit(0 if success else 1)
    except PermissionError as e:
        print(f"\nPermission error: {e}")
        if coins is None:
            print("Hint: ALL markets requires ws:l4BookUpdatesAll permission.")
        sys.exit(1)
    except SubscriptionError as e:
        print(f"\nSubscription error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
