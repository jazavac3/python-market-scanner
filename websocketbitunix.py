# bitunix_rest_scanner.py
import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple, Optional

import aiohttp

# ---------- CONFIG ----------
REST_TICKERS = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
POLL_INTERVAL = 2.0          # seconds (2s)
PRICE_THRESHOLD = 2.0        # percent for price change
VOLUME_THRESHOLD = 20.0      # percent for volume change
HISTORY_SECONDS = 300        # keep 5 minutes of history
# ----------------------------

# per-symbol history: deque of (ts_sec, price(float), volume(float))
histories: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(lambda: deque())

# helper: safe float parse
def to_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0

# prune history older than HISTORY_SECONDS
def prune_history(history: Deque[Tuple[float, float, float]], now_ts: float) -> None:
    cutoff = now_ts - HISTORY_SECONDS
    while history and history[0][0] < cutoff:
        history.popleft()

# find last snapshot at or before cutoff_ts
def find_snapshot_at_or_before(history: Deque[Tuple[float, float, float]], cutoff_ts: float) -> Optional[Tuple[float, float, float]]:
    for ts, price, vol in reversed(history):
        if ts <= cutoff_ts:
            return (ts, price, vol)
    return None

# percent change with zero handling
def pct_change(new: float, old: float) -> Optional[float]:
    if old == 0.0:
        if new == 0.0:
            return 0.0
        return float("inf")
    return (new - old) / old * 100.0

# format percent for printing
def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    if x == float("inf"):
        return "INF"
    return f"{x:+.2f}%"

# fetch tickers via REST (returns dict symbol -> (price, quoteVol))
async def fetch_tickers(session: aiohttp.ClientSession) -> Dict[str, Tuple[float, float]]:
    try:
        async with session.get(REST_TICKERS, timeout=10) as resp:
            data = await resp.json()
    except Exception as e:
        print("Fetch error:", e)
        return {}

    out = {}
    for item in data.get("data", []):
        sym = item.get("symbol")
        if not sym:
            continue
        price = to_float(item.get("lastPrice") or item.get("la") or 0.0)
        # try quoteVol then baseVol
        vol = to_float(item.get("quoteVol") or item.get("q") or item.get("baseVol") or 0.0)
        out[sym] = (price, vol)
    return out

# main scanning loop
async def main_loop():
    print("Starting REST scanner. Poll interval:", POLL_INTERVAL, "s")
    async with aiohttp.ClientSession() as session:
        # warm first snapshot
        tick0 = await fetch_tickers(session)
        if not tick0:
            print("Initial fetch failed; retrying in 3s...")
            await asyncio.sleep(3)
            tick0 = await fetch_tickers(session)

        now = time.time()
        for sym, (price, vol) in tick0.items():
            histories[sym].append((now, price, vol))

        while True:
            start = time.time()
            tickers = await fetch_tickers(session)
            ts = time.time()

            if not tickers:
                # on fetch failure, wait a bit and retry
                await asyncio.sleep(max(1.0, POLL_INTERVAL))
                continue

            movers = []

            # update histories
            for sym, (price, vol) in tickers.items():
                h = histories[sym]
                h.append((ts, price, vol))
                prune_history(h, ts)

            # analyze each symbol (iterate current tickers)
            for sym, (price, vol) in tickers.items():
                h = histories.get(sym)
                if not h or len(h) < 2:
                    continue

                # 1-minute snapshot
                snap1 = find_snapshot_at_or_before(h, ts - 60.0)
                if snap1:
                    _, old_price_1, old_vol_1 = snap1
                    pchg1 = pct_change(price, old_price_1)
                    vchg1 = pct_change(vol, old_vol_1)
                else:
                    pchg1 = None
                    vchg1 = None

                # 5-minute snapshot
                snap5 = find_snapshot_at_or_before(h, ts - 300.0)
                if snap5:
                    _, old_price_5, old_vol_5 = snap5
                    pchg5 = pct_change(price, old_price_5)
                    vchg5 = pct_change(vol, old_vol_5)
                else:
                    pchg5 = None
                    vchg5 = None

                # decide mover
                is_mover = False
                if pchg1 is not None and (abs(pchg1) >= PRICE_THRESHOLD or pchg1 == float("inf")):
                    is_mover = True
                if vchg1 is not None and (abs(vchg1) >= VOLUME_THRESHOLD or vchg1 == float("inf")):
                    is_mover = True
                if pchg5 is not None and (abs(pchg5) >= PRICE_THRESHOLD or pchg5 == float("inf")):
                    is_mover = True
                if vchg5 is not None and (abs(vchg5) >= VOLUME_THRESHOLD or vchg5 == float("inf")):
                    is_mover = True

                if is_mover:
                    movers.append((sym, pchg1, vchg1, pchg5, vchg5))

            # print movers
            if movers:
                print(f"\n[{time.strftime('%H:%M:%S', time.localtime(ts))}] Movers: {len(movers)}")
                for sym, p1, v1, p5, v5 in movers:
                    print(f"{sym:12}  1mP:{fmt_pct(p1):>8}  1mV:{fmt_pct(v1):>8}   5mP:{fmt_pct(p5):>8}  5mV:{fmt_pct(v5):>8}")
            else:
                # lightweight heartbeat to show it's alive
                print(f"[{time.strftime('%H:%M:%S', time.localtime(ts))}] No movers")

            # sleep until next poll, accounting for time spent
            elapsed = time.time() - start
            wait = POLL_INTERVAL - elapsed
            if wait > 0:
                await asyncio.sleep(wait)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nScanner stopped by user.")
