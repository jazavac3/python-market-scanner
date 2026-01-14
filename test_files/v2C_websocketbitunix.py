import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple, Optional
import os

import aiohttp

# ============================================================================
# ========================== CONFIGURATION SECTION ===========================
# ============================================================================

# API Configuration
REST_TICKERS = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
POLL_INTERVAL = 2.0          # seconds between scans
HISTORY_SECONDS = 3600       # keep 1 hour of history

# ============================================================================
# 🎯 HUNTING MODE - SET YOUR TARGET % GAINS HERE
# ============================================================================
# These are the MINIMUM price changes you want to catch.
# The program will auto-adjust these based on market cap tier.
# 
# Example: If you set 1m to 4%, then:
#   - MEGA tier (BTC/ETH) will trigger at 0.6% (4% × 0.15)
#   - LARGE tier will trigger at 1.0% (4% × 0.25)  
#   - MID tier will trigger at 2.0% (4% × 0.50)
#   - TINY tier will trigger at 5.2% (4% × 1.30)
#
# Format: {timeframe_in_seconds: (min_price_change%, min_volume_change%)}

# 🔥 ACTIVE HUNTING MODE - Choose ONE preset or customize below:

# === PRESET 1: AGGRESSIVE SCALPING (catch quick pumps) ===
# TIMEFRAMES = {
#     60: (4.0, 30.0),      # 1 min: 4% moves minimum
# }

# === PRESET 2: SWING TRADING (medium-term moves) ===
# TIMEFRAMES = {
#     300:  (6.0, 40.0),    # 5 min: 6% moves
#     900:  (10.0, 50.0),   # 15 min: 10% moves
# }

# === PRESET 3: MOONSHOT HUNTING (big pumps only) ===
# TIMEFRAMES = {
#     900:   (15.0, 60.0),  # 15 min: 15% minimum
#     3600:  (30.0, 80.0),  # 1 hour: 30% minimum
#     14400: (70.0, 120.0), # 4 hours: 70% minimum (low caps need 91%!)
# }

# === PRESET 4: MULTI-STRATEGY (catch everything significant) ===
TIMEFRAMES = {
    60:    (3.0,  25.0),   # 1 min: 3% scalps
    300:   (6.0,  35.0),   # 5 min: 6% moves
    900:   (10.0, 45.0),   # 15 min: 10% swings
    # 3600:  (20.0, 70.0),   # 1 hour: 20% trends
}

# === CUSTOM: Set your own timeframes and thresholds ===
# Uncomment and modify as needed:
# TIMEFRAMES = {
#     60:    (5.0, 30.0),    # Your custom 1min threshold
#     14400: (50.0, 100.0),  # Your custom 4hr threshold
# }

# ============================================================================
# 🎚️ TIER MULTIPLIERS (How much to scale for different market caps)
# ============================================================================
# Lower multiplier = MORE SENSITIVE (easier to trigger for that tier)
# Higher multiplier = LESS SENSITIVE (harder to trigger)
#
# If you want BIG coins (BTC/ETH) to trigger more easily: LOWER the MEGA multiplier
# If you want to filter out more TINY cap noise: RAISE the TINY multiplier

TIER_MULTIPLIERS = {
    'MEGA':   0.15,   # BTC/ETH: Need only 15% of your target (VERY sensitive)
    'LARGE':  0.25,   # Major alts: 25% of target
    'HIGH':   0.35,   # Top alts: 35% of target
    'MID':    0.50,   # Mid caps: 50% of target
    'LOW':    0.70,   # Small caps: 70% of target
    'MICRO':  1.00,   # Micro caps: 100% of target (exactly your threshold)
    'TINY':   1.30,   # Ultra tiny: 130% of target (LESS sensitive, filters noise)
}

# ============================================================================
# 📊 TIER DEFINITIONS (Auto-assigned by 24h volume)
# ============================================================================
DYNAMIC_TIERS = [
    (100_000_000, 'MEGA',   'MEGA',      0.15),  # >$100M/day
    (50_000_000,  'LARGE',  'LARGE',     0.25),  # >$50M/day
    (20_000_000,  'HIGH',   'HIGH',      0.35),  # >$20M/day
    (5_000_000,   'MID',    'MID',       0.50),  # >$5M/day
    (1_000_000,   'LOW',    'LOW',       0.70),  # >$1M/day
    (100_000,     'MICRO',  'MICRO',     1.00),  # >$100K/day
    (0,           'TINY',   'TINY',      1.30),  # <$100K/day
]

# Minimum volume filter (ignore dead pairs)
MIN_VOLUME_24H = 50000.0  # Must have at least $50K in 24h volume

# ============================================================================
# 🎨 DISPLAY CONFIGURATION
# ============================================================================
SHOW_NO_MOVERS_HEARTBEAT = True    # Show "No movers" messages
CLEAR_SCREEN_ON_MOVERS = False     # Clear terminal when movers detected
AUTO_CLEAR_TERMINAL = True         # Auto-clear old output periodically
CLEAR_TERMINAL_AFTER = 600         # Clear every 10 minutes
MAX_MOVERS_DISPLAY = 15            # Show top N movers per scan
SORT_BY = "1m_price"               # "1m_price" | "strongest_timeframe" | "tier"

# Visual settings
USE_COLORS = True                  # ANSI color codes
SHOW_VOLUME_CHANGES = True         # Show volume % alongside price
COMPACT_MODE = False               # Single-line mode (set True for minimal output)

# Color codes
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_CYAN = "\033[96m"
COLOR_BLUE = "\033[94m"
COLOR_MAGENTA = "\033[95m"
COLOR_BOLD = "\033[1m"
COLOR_DIM = "\033[2m"

# ============================================================================
# ======================= END CONFIGURATION SECTION ==========================
# ============================================================================


histories: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(lambda: deque())
volume_cache: Dict[str, float] = {}
last_clear_time = time.time()


def get_color(text: str, color_code: str) -> str:
    if USE_COLORS:
        return f"{color_code}{text}{COLOR_RESET}"
    return text


def to_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def prune_history(history: Deque[Tuple[float, float, float]], now_ts: float) -> None:
    cutoff = now_ts - HISTORY_SECONDS
    while history and history[0][0] < cutoff:
        history.popleft()


def find_snapshot_at_or_before(history: Deque[Tuple[float, float, float]], cutoff_ts: float) -> Optional[Tuple[float, float, float]]:
    for ts, price, vol in reversed(history):
        if ts <= cutoff_ts:
            return (ts, price, vol)
    return None


def pct_change(new: float, old: float) -> Optional[float]:
    if old == 0.0:
        if new == 0.0:
            return 0.0
        return float("inf")
    return (new - old) / old * 100.0


def fmt_pct(x: Optional[float], colored: bool = False, bold: bool = False) -> str:
    if x is None:
        return "   N/A   "
    if x == float("inf"):
        return "   INF   "
    
    formatted = f"{x:+7.2f}%"
    
    if colored and USE_COLORS:
        color = COLOR_GREEN if x > 0 else COLOR_RED
        if bold:
            formatted = get_color(formatted, COLOR_BOLD + color)
        else:
            formatted = get_color(formatted, color)
    
    return formatted


def get_tier(avg_volume: float) -> Tuple[str, str, float]:
    for min_vol, tier_code, tier_display, multiplier in DYNAMIC_TIERS:
        if avg_volume >= min_vol:
            return (tier_code, tier_display, multiplier)
    return ('TINY', 'TINY', 1.30)


def get_thresholds_for_tier(multiplier: float, timeframe: int) -> Tuple[float, float]:
    if timeframe not in TIMEFRAMES:
        return (100.0, 100.0)
    
    base_price, base_volume = TIMEFRAMES[timeframe]
    return (base_price * multiplier, base_volume * multiplier)


async def fetch_tickers(session: aiohttp.ClientSession) -> Dict[str, Tuple[float, float]]:
    try:
        async with session.get(REST_TICKERS, timeout=10) as resp:
            data = await resp.json()
    except Exception as e:
        print(get_color(f"⚠ Fetch error: {e}", COLOR_RED))
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


def format_timeframe(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    else:
        return f"{seconds // 86400}d"


def format_volume(vol: float) -> str:
    if vol >= 1_000_000:
        return f"${vol/1_000_000:.1f}M"
    elif vol >= 1_000:
        return f"${vol/1_000:.1f}K"
    else:
        return f"${vol:.0f}"


def print_header():
    print(get_color("=" * 110, COLOR_BLUE))
    print(get_color("              🚀 ADAPTIVE CRYPTO MOMENTUM SCANNER v2.0 - TERMINAL 🚀", COLOR_BOLD))
    print(get_color("=" * 110, COLOR_BLUE))
    print(f"Poll: {POLL_INTERVAL}s | Sort: {SORT_BY} | Min Vol: ${MIN_VOLUME_24H:,.0f}")
    print()
    
    # Show hunting targets
    print(get_color("🎯 ACTIVE HUNTING TARGETS:", COLOR_YELLOW + COLOR_BOLD))
    for tf in sorted(TIMEFRAMES.keys()):
        p_thresh, v_thresh = TIMEFRAMES[tf]
        print(f"  {format_timeframe(tf):8} → Looking for {get_color(f'{p_thresh}%', COLOR_GREEN + COLOR_BOLD)} price moves (min {v_thresh}% volume spike)")
    print()
    
    # Show what each tier needs
    print(get_color("📊 Actual Trigger Thresholds by Tier:", COLOR_CYAN))
    print(get_color("   (What % each tier needs to trigger an alert)", COLOR_DIM))
    print()
    
    first_tf = sorted(TIMEFRAMES.keys())[0]
    base_price = TIMEFRAMES[first_tf][0]
    
    print(f"  {'TIER':<8} {'VOLUME RANGE':<20} {'MULTIPLIER':<12} {format_timeframe(first_tf).upper() + ' NEEDS':<15}")
    print(f"  {'-'*70}")
    
    for min_vol, tier_code, tier_display, mult in DYNAMIC_TIERS:
        vol_str = f">{format_volume(min_vol)}/day"
        actual = base_price * mult
        color = COLOR_GREEN if mult < 0.5 else COLOR_YELLOW if mult < 1.0 else COLOR_RED
        print(f"  {tier_display:<8} {vol_str:<20} {mult:>4.0%} ({mult:.2f}x)  → {get_color(f'{actual:>5.2f}%', color)}")
    
    print(get_color("\n💡 TIP: To see LESS noise, INCREASE the base % in TIMEFRAMES or RAISE tiny tier multipliers", COLOR_DIM))
    print(get_color("💡 TIP: To catch MORE big-cap moves, LOWER the MEGA/LARGE multipliers", COLOR_DIM))
    print(get_color("=" * 110, COLOR_BLUE))
    print()


def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')


async def main_loop():
    global last_clear_time
    
    print_header()
    
    async with aiohttp.ClientSession() as session:
        # Warm-up
        tick0 = await fetch_tickers(session)
        if not tick0:
            print(get_color("⚠ Initial fetch failed; retrying in 3s...", COLOR_RED))
            await asyncio.sleep(3)
            tick0 = await fetch_tickers(session)

        now = time.time()
        for sym, (price, vol) in tick0.items():
            histories[sym].append((now, price, vol))
            volume_cache[sym] = vol

        scan_count = 0

        while True:
            start = time.time()
            tickers = await fetch_tickers(session)
            ts = time.time()

            if not tickers:
                await asyncio.sleep(max(1.0, POLL_INTERVAL))
                continue

            scan_count += 1

            # Auto-clear terminal
            if AUTO_CLEAR_TERMINAL and (ts - last_clear_time) >= CLEAR_TERMINAL_AFTER:
                clear_terminal()
                print_header()
                last_clear_time = ts

            movers = []

            # Update histories
            for sym, (price, vol) in tickers.items():
                h = histories[sym]
                h.append((ts, price, vol))
                prune_history(h, ts)

            # Analyze
            for sym, (price, vol) in tickers.items():
                h = histories.get(sym)
                if not h or len(h) < 2:
                    continue

                # Update volume cache
                if sym in volume_cache:
                    volume_cache[sym] = 0.9 * volume_cache[sym] + 0.1 * vol
                else:
                    volume_cache[sym] = vol

                # Filter by min volume
                if volume_cache[sym] < MIN_VOLUME_24H:
                    continue

                # Get tier
                tier_code, tier_display, multiplier = get_tier(volume_cache[sym])

                # Analyze timeframes
                timeframe_data = {}
                is_mover = False
                reasons = []

                for timeframe_sec in TIMEFRAMES.keys():
                    snap = find_snapshot_at_or_before(h, ts - timeframe_sec)
                    if not snap:
                        timeframe_data[timeframe_sec] = (None, None)
                        continue

                    _, old_price, old_vol = snap
                    pchg = pct_change(price, old_price)
                    vchg = pct_change(vol, old_vol)
                    
                    timeframe_data[timeframe_sec] = (pchg, vchg)

                    # Get thresholds
                    p_thresh, v_thresh = get_thresholds_for_tier(multiplier, timeframe_sec)

                    # Check thresholds
                    if pchg is not None and abs(pchg) >= p_thresh:
                        is_mover = True
                        tf_label = format_timeframe(timeframe_sec)
                        reasons.append(f"P{tf_label}")
                    
                    if vchg is not None and abs(vchg) >= v_thresh:
                        is_mover = True
                        tf_label = format_timeframe(timeframe_sec)
                        reasons.append(f"V{tf_label}")

                if is_mover:
                    movers.append((sym, tier_display, multiplier, timeframe_data, reasons, volume_cache[sym]))

            # Display
            if movers:
                if CLEAR_SCREEN_ON_MOVERS:
                    clear_terminal()
                    print_header()
                
                # Sort
                if SORT_BY == "1m_price":
                    movers.sort(key=lambda x: abs(x[3].get(60, (None, None))[0]) if x[3].get(60, (None, None))[0] is not None else 0, reverse=True)
                elif SORT_BY == "strongest_timeframe":
                    def get_strongest_change(m):
                        max_change = 0
                        for tf_data in m[3].values():
                            if tf_data[0] is not None:
                                max_change = max(max_change, abs(tf_data[0]))
                        return max_change
                    movers.sort(key=get_strongest_change, reverse=True)
                elif SORT_BY == "tier":
                    tier_priority = {'MEGA': 0, 'LARGE': 1, 'HIGH': 2, 'MID': 3, 'LOW': 4, 'MICRO': 5, 'TINY': 6}
                    movers.sort(key=lambda x: tier_priority.get(x[1], 99))
                
                display_movers = movers[:MAX_MOVERS_DISPLAY]
                
                print(get_color(f"\n{'=' * 110}", COLOR_BLUE))
                timestamp = time.strftime('%H:%M:%S', time.localtime(ts))
                header = f"[{timestamp}] 🔥 {len(movers)} MOVERS DETECTED (Scan #{scan_count})"
                if len(movers) > MAX_MOVERS_DISPLAY:
                    header += f" (showing top {MAX_MOVERS_DISPLAY})"
                print(get_color(header, COLOR_MAGENTA + COLOR_BOLD))
                print(get_color("=" * 110, COLOR_BLUE))
                
                for sym, tier, mult, tf_data, reasons, avg_vol in display_movers:
                    if COMPACT_MODE:
                        tier_str = get_color(f"[{tier:6}]", COLOR_YELLOW)
                        changes_str = ""
                        for tf in sorted(TIMEFRAMES.keys()):
                            pchg, _ = tf_data.get(tf, (None, None))
                            if pchg is not None:
                                changes_str += f" {format_timeframe(tf)}:{fmt_pct(pchg, True, True)}"
                        vol_str = get_color(f"Vol:{format_volume(avg_vol)}", COLOR_DIM)
                        print(f"{sym:14} {tier_str} {changes_str}  {vol_str}")
                    else:
                        tier_str = get_color(f"[{tier}]", COLOR_YELLOW)
                        reason_str = get_color(",".join(reasons), COLOR_CYAN)
                        vol_str = get_color(f"Vol: {format_volume(avg_vol)}", COLOR_DIM)
                        print(f"\n{get_color(sym, COLOR_BOLD):14} {tier_str} {vol_str} Triggers: {reason_str}")
                        
                        # PRICE - BIG AND BOLD
                        print(f"  {get_color('PRICE:', COLOR_BOLD):15}", end="")
                        for tf in sorted(TIMEFRAMES.keys()):
                            pchg, vchg = tf_data.get(tf, (None, None))
                            tf_label = format_timeframe(tf)
                            print(f" {tf_label:>4}: {fmt_pct(pchg, True, True):>10}", end="")
                        print()
                        
                        # Volume - secondary
                        if SHOW_VOLUME_CHANGES:
                            print(get_color(f"  {'Volume:':15}", COLOR_DIM), end="")
                            for tf in sorted(TIMEFRAMES.keys()):
                                pchg, vchg = tf_data.get(tf, (None, None))
                                tf_label = format_timeframe(tf)
                                print(get_color(f" {tf_label:>4}: {fmt_pct(vchg):>10}", COLOR_DIM), end="")
                            print()
                
                print()
            else:
                if SHOW_NO_MOVERS_HEARTBEAT:
                    timestamp = time.strftime('%H:%M:%S', time.localtime(ts))
                    print(f"[{timestamp}] ⏳ Scan #{scan_count}: No movers detected", end='\r')

            # Sleep
            elapsed = time.time() - start
            wait = POLL_INTERVAL - elapsed
            if wait > 0:
                await asyncio.sleep(wait)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print(get_color("\n\n🛑 Scanner stopped by user.", COLOR_RED))