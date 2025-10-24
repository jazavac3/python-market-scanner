import tkinter as tk
from tkinter import scrolledtext, ttk
import requests
import time
import logging
from collections import deque
import keyboard  # pip install keyboard
import threading
import traceback

# --- CONFIG ---
API_URL = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
# Price thresholds for 1m and 5m changes
PRICE_THRESHOLD_1M_PERCENT = 2.0
PRICE_THRESHOLD_5M_PERCENT = 3.0  # Might want this slightly higher
# Volume thresholds (factor over average of previous intervals)
VOLUME_SPIKE_FACTOR_1M = 3.0  # Volume must be 3x the average of previous 1m intervals
VOLUME_SPIKE_FACTOR_5M = 5.0  # Volume must be 5x the average of previous 5m intervals (e.g., last 5 1m intervals)
CHECK_INTERVAL_SECONDS = 60  # How often we fetch data (1 minute)
AVERAGE_WINDOW_SIZE = 5      # Number of previous intervals to average volume over (e.g., last 5 minutes)
ALERT_COOLDOWN_MINUTES = 10  # Minimum time before alerting on the same symbol again
EXIT_KEY = 'q'
# ----------------

# Setup logging to redirect to the UI textbox
class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        # Ensure updates happen in the main thread
        self.text_widget.after(0, self.append_text, msg)

    def append_text(self, msg):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, msg + '\n')
        self.text_widget.configure(state='disabled')
        # Auto-scroll to the end
        self.text_widget.see(tk.END)

class MarketSpikeDetector:
    def __init__(self, api_url, price_thresh_1m, price_thresh_5m, vol_factor_1m, vol_factor_5m, check_interval, avg_window, cooldown_minutes, exit_key):
        self.api_url = api_url
        self.price_thresh_1m = price_thresh_1m
        self.price_thresh_5m = price_thresh_5m
        self.vol_factor_1m = vol_factor_1m
        self.vol_factor_5m = vol_factor_5m
        self.check_interval = check_interval
        self.avg_window = avg_window
        self.cooldown_seconds = cooldown_minutes * 60
        self.exit_key = exit_key
        self.last_alert_times = {}
        self.price_history = {} # Store price snapshots for each symbol
        self.volume_history = {} # Store volume snapshots for each symbol
        self.running = False

    def get_tickers(self):
        try:
            logging.info("Fetching tickers...")
            response = requests.get(self.api_url, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                logging.error(f"API Error: {data.get('msg', 'Unknown error')}")
                return None
            tickers = {}
            for item in data.get("data", []):
                symbol = item["symbol"]
                price = float(item["lastPrice"])
                volume = float(item["quoteVol"])
                tickers[symbol] = {"price": price, "volume": volume, "timestamp": time.time()}
            logging.info(f"Fetched {len(tickers)} tickers successfully.")
            return tickers
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error fetching tickers: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logging.error(f"Error parsing ticker  {e}. Response: {response.text[:200]}...") # Log first 200 chars of response if parsing fails
            return None

    def calculate_interval_volume(self, symbol, current_data):
        if symbol in self.volume_history and self.volume_history[symbol]:
            previous_snapshot = self.volume_history[symbol][-1]
            interval_vol = current_data["volume"] - previous_snapshot["volume"]
            if interval_vol < 0:
                logging.warning(f"Negative interval volume for {symbol}: {current_data['volume']} - {previous_snapshot['volume']} = {interval_vol}. Using 0.")
                interval_vol = 0
            return interval_vol
        return 0

    def update_price_history(self, current_tickers):
        for symbol, data in current_tickers.items():
            if symbol not in self.price_history:
                self.price_history[symbol] = deque(maxlen=self.avg_window)
            self.price_history[symbol].append(data)

    def update_volume_history(self, current_tickers):
         for symbol, data in current_tickers.items():
            if symbol not in self.volume_history:
                self.volume_history[symbol] = deque(maxlen=self.avg_window)
            self.volume_history[symbol].append(data)

    def calculate_price_change(self, symbol, current_price, history_key):
        """Calculates price change over the specified history window."""
        history = getattr(self, history_key) # Get the correct history deque (price_history)
        if symbol in history and len(history[symbol]) > 1:
            # Get the price from the start of the desired window
            # For 1m, we want the previous snapshot (index -2 if len=2, -2 if len>2)
            # For 5m, we want the snapshot 5 intervals ago (index -5 if len=5, -5 if len>5, or first if len < 5)
            # Simplified: Calculate change from the *oldest* price in the current history window
            if len(history[symbol]) >= 2:
                 start_price = history[symbol][0]["price"] # Oldest in the window
                 if start_price != 0:
                     change_pct = ((current_price - start_price) / start_price) * 100
                     return change_pct
        return 0.0

    def calculate_average_interval_volume(self, symbol):
        if symbol not in self.volume_history or len(self.volume_history[symbol]) < 2:
            return 0

        intervals = []
        hist = self.volume_history[symbol]
        for i in range(1, len(hist)):
            vol_diff = hist[i]["volume"] - hist[i-1]["volume"]
            vol_diff = max(0, vol_diff)
            intervals.append(vol_diff)

        if intervals:
            avg_vol = sum(intervals) / len(intervals)
            # logging.debug(f"Symbol {symbol}: Avg Interval Vol = {avg_vol}, Last Intervals = {intervals[-3:]}")
            return avg_vol
        return 0

    def check_and_alert(self, current_tickers):
        if not current_tickers:
            logging.warning("No tickers to check for alerts.")
            return

        for symbol, data in current_tickers.items():
            current_price = data["price"]
            current_interval_vol = self.calculate_interval_volume(symbol, data)
            avg_interval_vol = self.calculate_average_interval_volume(symbol)

            # Calculate price changes for 1m and 5m based on history length
            price_change_1m = self.calculate_price_change(symbol, current_price, 'price_history')
            # For 5m, we need at least 5 snapshots in history to be meaningful
            price_change_5m = 0.0
            if symbol in self.price_history and len(self.price_history[symbol]) >= 5:
                 price_change_5m = self.calculate_price_change(symbol, current_price, 'price_history')

            # Check for alerts
            should_alert = False
            alert_reasons = []

            # Check price thresholds
            if abs(price_change_1m) >= self.price_thresh_1m:
                should_alert = True
                alert_reasons.append(f"Price 1m: {price_change_1m:+.2f}%")
            if abs(price_change_5m) >= self.price_thresh_5m:
                should_alert = True
                alert_reasons.append(f"Price 5m: {price_change_5m:+.2f}%")

            # Check volume spikes (relative to average)
            if avg_interval_vol > 0:
                if current_interval_vol > (self.vol_factor_1m * avg_interval_vol):
                    should_alert = True
                    alert_reasons.append(f"Volume 1m Spike: {current_interval_vol:.2f} (x{current_interval_vol/avg_interval_vol:.2f} avg)")
                # Note: The 5m volume spike uses the *average* over the last 5 intervals, not a single 5m volume.
                # This is consistent with the previous logic and how cumulative volume works.
                # If you want a spike relative to the *sum* of the last 5 intervals, that's a different calculation.
                # For now, using the average volume for comparison.
                # You could add a check for a large *cumulative* volume over the last 5 intervals if needed.

            # Check cooldown
            current_time = time.time()
            last_alert_time = self.last_alert_times.get(symbol, 0)
            if should_alert and (current_time - last_alert_time) >= self.cooldown_seconds:
                logging.info(f"🚨 SPIKE ALERT: {symbol} - {', '.join(alert_reasons)} (Last {current_price:.6f})")
                self.last_alert_times[symbol] = current_time

        # Update histories after checking all symbols
        self.update_price_history(current_tickers)
        self.update_volume_history(current_tickers)


    def run(self):
        """Main loop to continuously check for spikes."""
        try:
            logging.info(f"Starting Market Spike Detector... Press '{self.exit_key.upper()}' to exit or click Stop.")
            self.running = True
            baseline = self.get_tickers()
            if baseline:
                self.update_price_history(baseline)
                self.update_volume_history(baseline)
                logging.info("Initial snapshot taken.")
            else:
                logging.error("Failed to get initial snapshot. Detector cannot start.")
                self.running = False
                return

            counter = 0
            while self.running:
                counter += 1
                logging.info(f"[Loop {counter}] Waiting {self.check_interval}s...")

                slept = 0
                while slept < self.check_interval and self.running:
                    time.sleep(0.5)
                    slept += 0.5
                    if keyboard.is_pressed(self.exit_key):
                         logging.info(f"Exit key '{self.exit_key}' detected in loop.")
                         self.running = False
                         break

                if not self.running:
                    logging.info("Detector stopped by key press or stop button during sleep.")
                    break

                logging.info(f"[Loop {counter}] Fetching tickers...")
                current_tickers = self.get_tickers()
                if current_tickers and self.running:
                    logging.info(f"[Loop {counter}] Checking {len(current_tickers)} tickers for spikes...")
                    self.check_and_alert(current_tickers)
                    logging.info(f"[Loop {counter}] Check complete.")
                elif not self.running:
                    logging.info("Detector stopped by key press or stop button during processing.")
                    break
                else:
                    logging.warning(f"[Loop {counter}] Failed to fetch tickers in this cycle.")
                    time.sleep(5) # Brief pause on failure

        except Exception as e:
            logging.error(f"An unexpected error occurred in the detector loop: {e}")
            logging.error(traceback.format_exc())
        finally:
            logging.info("Market Spike Detector stopped.")
            self.running = False


class SpikeDetectorUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Market Spike Detector (1m/5m Focus)")

        # Create and pack the text area for logs
        self.log_text = scrolledtext.ScrolledText(root, wrap=tk.WORD, state='disabled', height=20, width=100)
        self.log_text.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        # Create a frame for the buttons
        button_frame = ttk.Frame(root)
        button_frame.pack(pady=5)

        # Create and pack the Start button
        self.start_button = ttk.Button(button_frame, text="Start Detector", command=self.start_detector)
        self.start_button.pack(side=tk.LEFT, padx=5)

        # Create and pack the Stop button
        self.stop_button = ttk.Button(button_frame, text="Stop Detector", command=self.stop_detector, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        # Initialize the detector object with new parameters
        self.detector = MarketSpikeDetector(
            API_URL,
            PRICE_THRESHOLD_1M_PERCENT,
            PRICE_THRESHOLD_5M_PERCENT,
            VOLUME_SPIKE_FACTOR_1M,
            VOLUME_SPIKE_FACTOR_5M,
            CHECK_INTERVAL_SECONDS,
            AVERAGE_WINDOW_SIZE,
            ALERT_COOLDOWN_MINUTES,
            EXIT_KEY
        )

        # Setup logging to redirect to the text widget
        text_handler = TextHandler(self.log_text)
        logging.getLogger().addHandler(text_handler)
        logging.getLogger().setLevel(logging.INFO)

        # Store the thread object
        self.detector_thread = None

    def start_detector(self):
        if not self.detector.running:
            logging.info("Start button pressed.")
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.detector_thread = threading.Thread(target=self.detector.run, daemon=True)
            self.detector_thread.start()
            logging.info("Detector thread started.")

    def stop_detector(self):
        if self.detector.running:
            logging.info("Stop button pressed. Sending stop signal...")
            self.detector.running = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            logging.info("Stop signal sent.")


# Main execution
if __name__ == "__main__":
    root = tk.Tk()
    app = SpikeDetectorUI(root)
    root.mainloop()