import requests, time, colorama

# --- CONFIG ---
API_URL = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
PRICE_THRESHOLD = 2      # % price change
VOLUME_THRESHOLD = 3    # % volume change
CHECK_INTERVAL = 60      # seconds (1m)
# ----------------

def get_tickers():
    r = requests.get(API_URL).json()
    tickers = {}
    for item in r.get("data", []):
        symbol = item["symbol"]
        price = float(item["lastPrice"])
        volume = float(item["quoteVol"])  # quoteVol is the traded volume in quote currency
        tickers[symbol] = {"price": price, "volume": volume}
    return tickers

print("Fetching initial snapshot...")
baseline = get_tickers()
time.sleep(CHECK_INTERVAL)

while True:
    print("\nChecking for movers...")
    now = get_tickers()
    for symbol, new_data in now.items():
        if symbol not in baseline: 
            continue
        old_data = baseline[symbol]

        # Avoid division by zero
        if old_data["price"] == 0 or old_data["volume"] == 0:
            continue

        price_change = ((new_data["price"] - old_data["price"]) / old_data["price"]) * 100
        vol_change = ((new_data["volume"] - old_data["volume"]) / old_data["volume"]) * 100

        # Filter only significant changes
        if abs(price_change) >= PRICE_THRESHOLD or abs(vol_change) >= VOLUME_THRESHOLD:
            print(f"{symbol}: Price change {price_change:.2f}%, Volume chagne {vol_change:.2f}%")

    baseline = now
    time.sleep(CHECK_INTERVAL)
