import asyncio, requests

# --- CONFIG ---
API_URL = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
PRICE_THRESHOLD = 2      # % price change
VOLUME_THRESHOLD = 20    # % volume change
CHECK_INTERVAL = 60      # seconds (1m)
# ----------------