# bitunix_rest_scanner_clean.py
import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple, Optional
import os
import dotenv

dotenv.load_dotenv(".env")

import aiohttp

# ---------- CONFIG ----------
REST_TICKERS = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
POLL_INTERVAL = 2.0          # seconds (2s)
PRICE_THRESHOLD = 2.0        # percent for price change
VOLUME_THRESHOLD = 20.0      # percent for volume change
HISTORY_SECONDS = 300        # keep 5 minutes of history for 1m snapshot
TREND_VOLUME_LOOKBACK_SEC = 60.0    # local baseline window for spike detection
TREND_SPIKE_MULTIPLIER = 1.5       # volume must be this many times baseline
TREND_MIN_PRICE_MOVE_1M = 0.5       # minimum 1m price move (abs %) to confirm trend
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_NOTIFY_COOLDOWN_SEC = 120.0  # avoid spam
# ----------------------------

# per-symbol history: deque of (ts_sec, price(float), volume(float))
histories: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(lambda: deque())

def clear():            # clears terminal 
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

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


def avg_volume_before(
    history: Deque[Tuple[float, float, float]],
    now_ts: float,
    lookback_sec: float,
) -> Optional[float]:
    values = [v for ts, _, v in history if (now_ts - lookback_sec) <= ts < now_ts]
    if not values:
        return None
    return sum(values) / len(values)


async def send_telegram_message(session: aiohttp.ClientSession, text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        async with session.post(url, json=payload, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False

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
        vol = to_float(item.get("quoteVol") or item.get("q") or item.get("baseVol") or 0.0)
        out[sym] = (price, vol)
    return out

# main scanning loop
async def main_loop():
    print("Starting live scanner. Poll interval:", POLL_INTERVAL, "s")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("Telegram notifications enabled (volume changes only).")
    else:
        print("Telegram disabled. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable notifications.")
    async with aiohttp.ClientSession() as session:
        last_telegram_sent_at: Dict[str, float] = {}
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
            trend_starts = []

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

                # decide mover
                is_mover = False
                if pchg1 is not None and (abs(pchg1) >= PRICE_THRESHOLD or pchg1 == float("inf")):
                    is_mover = True
                if vchg1 is not None and (abs(vchg1) >= VOLUME_THRESHOLD or vchg1 == float("inf")):
                    is_mover = True

                if is_mover:
                    movers.append((sym, pchg1, vchg1))

                # Telegram notifications only for significant volume changes
                vol_signal = vchg1 is not None and (abs(vchg1) >= VOLUME_THRESHOLD or vchg1 == float("inf"))
                if vol_signal and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                    last_sent = last_telegram_sent_at.get(sym, 0.0)
                    if (ts - last_sent) >= TELEGRAM_NOTIFY_COOLDOWN_SEC:
                        msg = (
                            f"Volume alert: {sym}\n"
                            f"1m volume change: {fmt_pct(vchg1)}\n"
                            f"1m price change: {fmt_pct(pchg1)}\n"
                            f"Time: {time.strftime('%H:%M:%S', time.localtime(ts))}"
                        )
                        sent = await send_telegram_message(session, msg)
                        if sent:
                            last_telegram_sent_at[sym] = ts

                # trend-start candidate: current volume spikes vs recent baseline + price confirms direction
                baseline_vol = avg_volume_before(h, ts, TREND_VOLUME_LOOKBACK_SEC)
                if baseline_vol is not None and baseline_vol > 0.0:
                    vol_ratio = vol / baseline_vol
                else:
                    vol_ratio = None

                if (
                    vol_ratio is not None
                    and vol_ratio >= TREND_SPIKE_MULTIPLIER
                    and pchg1 is not None
                    and pchg1 != float("inf")
                    and abs(pchg1) >= TREND_MIN_PRICE_MOVE_1M
                ):
                    direction = "UP" if pchg1 > 0 else "DOWN"
                    trend_starts.append((sym, direction, vol_ratio, pchg1, vchg1))

            # print movers
            if movers:
                print(f"\n[{time.strftime('%H:%M:%S', time.localtime(ts))}] Movers: {len(movers)}")
                for sym, p1, v1 in movers:
                    print(f"{sym:12}  1mP:{fmt_pct(p1):>8}  1mV:{fmt_pct(v1):>8}")
            else:
                print(f"[{time.strftime('%H:%M:%S', time.localtime(ts))}] No movers")

            if trend_starts:
                print(f"[{time.strftime('%H:%M:%S', time.localtime(ts))}] Trend-start volume spikes: {len(trend_starts)}")
                for sym, direction, ratio, p1, v1 in trend_starts:
                    print(
                        f"{sym:12}  Dir:{direction:>4}  "
                        f"VolX:{ratio:>5.2f}  1mP:{fmt_pct(p1):>8}  1mV:{fmt_pct(v1):>8}"
                    )

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
