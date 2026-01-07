"""L4 orderbook snapshot wire format parsing."""

import struct

import msgpack
import zstandard as zstd


def unpack_l4_book_snapshot(data: bytes) -> dict:
    """
    Unpack L4 orderbook snapshot from binary format.

    Wire format: [count:u32][height:u64][timestamp_ms:u64][blob_len:u32][blob]...
    Each blob is zstd-compressed L4PerpMarketSnapshot msgpack: [coin, bids, asks]
    Order tuple: [user, px, sz, oid]

    Returns dict with height, timestamp_ms, market_count, markets.
    """
    offset = 0
    count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    height = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    timestamp_ms = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    dctx = zstd.ZstdDecompressor()
    markets = []

    for _ in range(count):
        blob_len = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        blob = data[offset : offset + blob_len]
        offset += blob_len

        # Decompress L4PerpMarketSnapshot: [coin, bids, asks]
        # max_output_size needed when zstd frame lacks content size
        # 20x is plenty - typical msgpack compression ratio is 5-10x
        decompressed = dctx.decompress(blob, max_output_size=blob_len * 20)
        m = msgpack.unpackb(decompressed, raw=False)

        # order = [user, px, sz, oid]
        bids = [{"oid": o[3], "user": o[0], "px": o[1], "sz": o[2]} for o in m[1]]
        asks = [{"oid": o[3], "user": o[0], "px": o[1], "sz": o[2]} for o in m[2]]
        markets.append({"coin": m[0], "bids": bids, "asks": asks})

    return {
        "height": height,
        "timestamp_ms": timestamp_ms,
        "market_count": len(markets),
        "markets": markets,
    }
