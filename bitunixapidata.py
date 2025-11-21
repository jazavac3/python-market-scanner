import requests, time

# --- CONFIG ---
API_URL = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
PRICE_THRESHOLD = 2      # % price change
VOLUME_THRESHOLD = 20    # % volume change
CHECK_INTERVAL = 60      # seconds (1m)
# ----------------



# Gathers tickers from the API_URL and puts them into a list so they can be iterated on
def get_tickers():
    response = requests.get(API_URL).json()
    tickers = {}
    for item in response.get("data", []):
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
            print(f"{symbol}: Price change {price_change:.2f}%, Volume change: {vol_change:.2f}%")

    baseline = now
    time.sleep(CHECK_INTERVAL)
