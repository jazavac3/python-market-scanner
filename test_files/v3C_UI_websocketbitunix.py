import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple, Optional
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import threading

import aiohttp

# ============================================================================
# ========================== CONFIGURATION SECTION ===========================
# ============================================================================

class Config:
    """Centralized configuration management"""
    
    # API Configuration
    REST_TICKERS = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
    POLL_INTERVAL = 2.0
    HISTORY_SECONDS = 3600
    
    # Timeframes and thresholds
    TIMEFRAMES = {
        60:    (3.0,  25.0),
        300:   (6.0,  35.0),
        900:   (10.0, 45.0),
    }
    
    # Tier system
    TIER_MULTIPLIERS = {
        'MEGA':   0.15,
        'LARGE':  0.25,
        'HIGH':   0.35,
        'MID':    0.50,
        'LOW':    0.70,
        'MICRO':  1.00,
        'TINY':   1.30,
    }
    
    DYNAMIC_TIERS = [
        (100_000_000, 'MEGA',   'MEGA',      0.15),
        (50_000_000,  'LARGE',  'LARGE',     0.25),
        (20_000_000,  'HIGH',   'HIGH',      0.35),
        (5_000_000,   'MID',    'MID',       0.50),
        (1_000_000,   'LOW',    'LOW',       0.70),
        (100_000,     'MICRO',  'MICRO',     1.00),
        (0,           'TINY',   'TINY',      1.30),
    ]
    
    MIN_VOLUME_24H = 50000.0
    
    # Display
    MAX_TABLE_ROWS = 500
    SORT_BY = "strongest"
    AUTO_SCROLL = True
    
    @classmethod
    def get_tier(cls, avg_volume: float) -> Tuple[str, str, float]:
        for min_vol, tier_code, tier_display, multiplier in cls.DYNAMIC_TIERS:
            if avg_volume >= min_vol:
                return (tier_code, tier_display, multiplier)
        return ('TINY', 'TINY', 1.30)
    
    @classmethod
    def get_thresholds_for_tier(cls, multiplier: float, timeframe: int) -> Tuple[float, float]:
        if timeframe not in cls.TIMEFRAMES:
            return (100.0, 100.0)
        base_price, base_volume = cls.TIMEFRAMES[timeframe]
        return (base_price * multiplier, base_volume * multiplier)

# ============================================================================

histories: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(lambda: deque())
volume_cache: Dict[str, float] = {}


def to_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def prune_history(history: Deque[Tuple[float, float, float]], now_ts: float) -> None:
    cutoff = now_ts - Config.HISTORY_SECONDS
    while history and history[0][0] < cutoff:
        history.popleft()


def find_snapshot_at_or_before(history: Deque[Tuple[float, float, float]], cutoff_ts: float):
    for ts, price, vol in reversed(history):
        if ts <= cutoff_ts:
            return (ts, price, vol)
    return None


def pct_change(new: float, old: float) -> Optional[float]:
    if old == 0.0:
        return 0.0 if new == 0.0 else float("inf")
    return (new - old) / old * 100.0


def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    if x == float("inf"):
        return "INF"
    return f"{x:+.2f}%"


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
    if vol >= 1_000_000_000:
        return f"${vol/1_000_000_000:.2f}B"
    elif vol >= 1_000_000:
        return f"${vol/1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"${vol/1_000:.1f}K"
    else:
        return f"${vol:.0f}"


async def fetch_tickers(session: aiohttp.ClientSession):
    try:
        async with session.get(Config.REST_TICKERS, timeout=10) as resp:
            data = await resp.json()
    except Exception:
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


class SettingsDialog(tk.Toplevel):
    """Professional settings dialog for configuration management"""
    
    def __init__(self, parent, on_save_callback):
        super().__init__(parent)
        self.on_save_callback = on_save_callback
        
        self.title("Scanner Configuration")
        self.geometry("700x650")
        self.configure(bg="#1a1a1a")
        self.resizable(False, False)
        
        # Make modal
        self.transient(parent)
        self.grab_set()
        
        self.setup_ui()
        self.load_current_config()
        
    def setup_ui(self):
        # Main container with padding
        main_frame = tk.Frame(self, bg="#1a1a1a", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_label = tk.Label(
            main_frame,
            text="SCANNER CONFIGURATION",
            font=("Segoe UI", 14, "bold"),
            bg="#1a1a1a",
            fg="#ffffff"
        )
        title_label.pack(pady=(0, 20))
        
        # Notebook for tabs
        style = ttk.Style()
        style.configure('Settings.TNotebook', background='#1a1a1a', borderwidth=0)
        style.configure('Settings.TNotebook.Tab', background='#2d2d2d', foreground='#ffffff', 
                       padding=[20, 10], font=('Segoe UI', 10))
        style.map('Settings.TNotebook.Tab', background=[('selected', '#00ff88')], 
                 foreground=[('selected', '#000000')])
        
        notebook = ttk.Notebook(main_frame, style='Settings.TNotebook')
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        # Timeframes Tab
        timeframes_frame = tk.Frame(notebook, bg="#2d2d2d", padx=20, pady=20)
        notebook.add(timeframes_frame, text="Timeframes")
        
        self.setup_timeframes_tab(timeframes_frame)
        
        # Tiers Tab
        tiers_frame = tk.Frame(notebook, bg="#2d2d2d", padx=20, pady=20)
        notebook.add(tiers_frame, text="Tier Multipliers")
        
        self.setup_tiers_tab(tiers_frame)
        
        # General Tab
        general_frame = tk.Frame(notebook, bg="#2d2d2d", padx=20, pady=20)
        notebook.add(general_frame, text="General")
        
        self.setup_general_tab(general_frame)
        
        # Buttons
        button_frame = tk.Frame(main_frame, bg="#1a1a1a")
        button_frame.pack(fill=tk.X)
        
        save_btn = tk.Button(
            button_frame,
            text="SAVE & APPLY",
            command=self.save_config,
            bg="#00ff88",
            fg="#000000",
            font=("Segoe UI", 10, "bold"),
            padx=25,
            pady=10,
            relief=tk.FLAT,
            cursor="hand2"
        )
        save_btn.pack(side=tk.RIGHT, padx=5)
        
        cancel_btn = tk.Button(
            button_frame,
            text="CANCEL",
            command=self.destroy,
            bg="#444444",
            fg="#ffffff",
            font=("Segoe UI", 10),
            padx=25,
            pady=10,
            relief=tk.FLAT,
            cursor="hand2"
        )
        cancel_btn.pack(side=tk.RIGHT, padx=5)
        
    def setup_timeframes_tab(self, parent):
        tk.Label(
            parent,
            text="Configure timeframes and minimum thresholds",
            font=("Segoe UI", 9),
            bg="#2d2d2d",
            fg="#aaaaaa"
        ).pack(anchor='w', pady=(0, 15))
        
        # Create entry fields for common timeframes
        self.timeframe_entries = {}
        
        timeframe_options = [
            (60, "1 Minute"),
            (300, "5 Minutes"),
            (900, "15 Minutes"),
            (3600, "1 Hour"),
            (14400, "4 Hours"),
        ]
        
        for seconds, label in timeframe_options:
            frame = tk.Frame(parent, bg="#2d2d2d")
            frame.pack(fill=tk.X, pady=8)
            
            tk.Label(
                frame,
                text=label,
                font=("Segoe UI", 10),
                bg="#2d2d2d",
                fg="#ffffff",
                width=12,
                anchor='w'
            ).pack(side=tk.LEFT, padx=(0, 10))
            
            tk.Label(
                frame,
                text="Price %:",
                font=("Segoe UI", 9),
                bg="#2d2d2d",
                fg="#aaaaaa"
            ).pack(side=tk.LEFT, padx=(0, 5))
            
            price_entry = tk.Entry(
                frame,
                bg="#1a1a1a",
                fg="#ffffff",
                insertbackground="#ffffff",
                font=("Segoe UI", 9),
                width=8,
                relief=tk.FLAT
            )
            price_entry.pack(side=tk.LEFT, padx=(0, 15))
            
            tk.Label(
                frame,
                text="Volume %:",
                font=("Segoe UI", 9),
                bg="#2d2d2d",
                fg="#aaaaaa"
            ).pack(side=tk.LEFT, padx=(0, 5))
            
            vol_entry = tk.Entry(
                frame,
                bg="#1a1a1a",
                fg="#ffffff",
                insertbackground="#ffffff",
                font=("Segoe UI", 9),
                width=8,
                relief=tk.FLAT
            )
            vol_entry.pack(side=tk.LEFT)
            
            self.timeframe_entries[seconds] = (price_entry, vol_entry)
    
    def setup_tiers_tab(self, parent):
        tk.Label(
            parent,
            text="Adjust sensitivity multipliers for each market cap tier",
            font=("Segoe UI", 9),
            bg="#2d2d2d",
            fg="#aaaaaa"
        ).pack(anchor='w', pady=(0, 15))
        
        self.tier_entries = {}
        
        tiers = [
            ('MEGA', 'Mega Cap (>$100M/day)'),
            ('LARGE', 'Large Cap (>$50M/day)'),
            ('HIGH', 'High Cap (>$20M/day)'),
            ('MID', 'Mid Cap (>$5M/day)'),
            ('LOW', 'Low Cap (>$1M/day)'),
            ('MICRO', 'Micro Cap (>$100K/day)'),
            ('TINY', 'Tiny Cap (<$100K/day)'),
        ]
        
        for tier_code, tier_label in tiers:
            frame = tk.Frame(parent, bg="#2d2d2d")
            frame.pack(fill=tk.X, pady=8)
            
            tk.Label(
                frame,
                text=tier_label,
                font=("Segoe UI", 10),
                bg="#2d2d2d",
                fg="#ffffff",
                width=25,
                anchor='w'
            ).pack(side=tk.LEFT, padx=(0, 10))
            
            tk.Label(
                frame,
                text="Multiplier:",
                font=("Segoe UI", 9),
                bg="#2d2d2d",
                fg="#aaaaaa"
            ).pack(side=tk.LEFT, padx=(0, 5))
            
            entry = tk.Entry(
                frame,
                bg="#1a1a1a",
                fg="#ffffff",
                insertbackground="#ffffff",
                font=("Segoe UI", 9),
                width=8,
                relief=tk.FLAT
            )
            entry.pack(side=tk.LEFT)
            
            self.tier_entries[tier_code] = entry
    
    def setup_general_tab(self, parent):
        tk.Label(
            parent,
            text="General scanner settings",
            font=("Segoe UI", 9),
            bg="#2d2d2d",
            fg="#aaaaaa"
        ).pack(anchor='w', pady=(0, 15))
        
        # Min Volume
        frame = tk.Frame(parent, bg="#2d2d2d")
        frame.pack(fill=tk.X, pady=8)
        
        tk.Label(
            frame,
            text="Minimum 24h Volume ($):",
            font=("Segoe UI", 10),
            bg="#2d2d2d",
            fg="#ffffff",
            width=25,
            anchor='w'
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        self.min_vol_entry = tk.Entry(
            frame,
            bg="#1a1a1a",
            fg="#ffffff",
            insertbackground="#ffffff",
            font=("Segoe UI", 9),
            width=12,
            relief=tk.FLAT
        )
        self.min_vol_entry.pack(side=tk.LEFT)
        
        # Poll Interval
        frame = tk.Frame(parent, bg="#2d2d2d")
        frame.pack(fill=tk.X, pady=8)
        
        tk.Label(
            frame,
            text="Poll Interval (seconds):",
            font=("Segoe UI", 10),
            bg="#2d2d2d",
            fg="#ffffff",
            width=25,
            anchor='w'
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        self.poll_entry = tk.Entry(
            frame,
            bg="#1a1a1a",
            fg="#ffffff",
            insertbackground="#ffffff",
            font=("Segoe UI", 9),
            width=12,
            relief=tk.FLAT
        )
        self.poll_entry.pack(side=tk.LEFT)
        
        # Max Rows
        frame = tk.Frame(parent, bg="#2d2d2d")
        frame.pack(fill=tk.X, pady=8)
        
        tk.Label(
            frame,
            text="Max Table Rows:",
            font=("Segoe UI", 10),
            bg="#2d2d2d",
            fg="#ffffff",
            width=25,
            anchor='w'
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        self.max_rows_entry = tk.Entry(
            frame,
            bg="#1a1a1a",
            fg="#ffffff",
            insertbackground="#ffffff",
            font=("Segoe UI", 9),
            width=12,
            relief=tk.FLAT
        )
        self.max_rows_entry.pack(side=tk.LEFT)
        
    def load_current_config(self):
        # Load timeframes
        for seconds, (price_entry, vol_entry) in self.timeframe_entries.items():
            if seconds in Config.TIMEFRAMES:
                price, vol = Config.TIMEFRAMES[seconds]
                price_entry.insert(0, str(price))
                vol_entry.insert(0, str(vol))
        
        # Load tiers
        for tier_code, entry in self.tier_entries.items():
            if tier_code in Config.TIER_MULTIPLIERS:
                entry.insert(0, str(Config.TIER_MULTIPLIERS[tier_code]))
        
        # Load general
        self.min_vol_entry.insert(0, str(Config.MIN_VOLUME_24H))
        self.poll_entry.insert(0, str(Config.POLL_INTERVAL))
        self.max_rows_entry.insert(0, str(Config.MAX_TABLE_ROWS))
    
    def save_config(self):
        try:
            # Save timeframes
            new_timeframes = {}
            for seconds, (price_entry, vol_entry) in self.timeframe_entries.items():
                price_val = price_entry.get().strip()
                vol_val = vol_entry.get().strip()
                if price_val and vol_val:
                    new_timeframes[seconds] = (float(price_val), float(vol_val))
            
            if not new_timeframes:
                messagebox.showerror("Error", "At least one timeframe must be configured")
                return
            
            Config.TIMEFRAMES = new_timeframes
            
            # Save tiers
            for tier_code, entry in self.tier_entries.items():
                val = entry.get().strip()
                if val:
                    Config.TIER_MULTIPLIERS[tier_code] = float(val)
            
            # Update dynamic tiers with new multipliers
            Config.DYNAMIC_TIERS = [
                (100_000_000, 'MEGA',   'MEGA',      Config.TIER_MULTIPLIERS['MEGA']),
                (50_000_000,  'LARGE',  'LARGE',     Config.TIER_MULTIPLIERS['LARGE']),
                (20_000_000,  'HIGH',   'HIGH',      Config.TIER_MULTIPLIERS['HIGH']),
                (5_000_000,   'MID',    'MID',       Config.TIER_MULTIPLIERS['MID']),
                (1_000_000,   'LOW',    'LOW',       Config.TIER_MULTIPLIERS['LOW']),
                (100_000,     'MICRO',  'MICRO',     Config.TIER_MULTIPLIERS['MICRO']),
                (0,           'TINY',   'TINY',      Config.TIER_MULTIPLIERS['TINY']),
            ]
            
            # Save general
            Config.MIN_VOLUME_24H = float(self.min_vol_entry.get())
            Config.POLL_INTERVAL = float(self.poll_entry.get())
            Config.MAX_TABLE_ROWS = int(self.max_rows_entry.get())
            
            messagebox.showinfo("Success", "Configuration saved successfully")
            self.on_save_callback()
            self.destroy()
            
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid input: {str(e)}")


class CryptoScannerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Momentum Scanner - Professional Edition")
        self.root.geometry("1600x900")
        self.root.configure(bg="#0a0a0a")
        
        self.running = False
        self.scan_count = 0
        self.mover_count = 0
        self.sort_column = None
        self.sort_reverse = False
        
        self.setup_ui()
        
    def setup_ui(self):
        # ===== MENU BAR =====
        menubar = tk.Menu(self.root, bg="#1a1a1a", fg="#ffffff")
        self.root.config(menu=menubar)
        
        file_menu = tk.Menu(menubar, tearoff=0, bg="#1a1a1a", fg="#ffffff")
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Settings", command=self.open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        view_menu = tk.Menu(menubar, tearoff=0, bg="#1a1a1a", fg="#ffffff")
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Clear Table", command=self.clear_table)
        view_menu.add_command(label="Refresh Columns", command=self.rebuild_table)
        
        # ===== HEADER =====
        header_frame = tk.Frame(self.root, bg="#1a1a1a", height=100)
        header_frame.pack(fill=tk.X, padx=0, pady=0)
        header_frame.pack_propagate(False)
        
        # Title section
        title_container = tk.Frame(header_frame, bg="#1a1a1a")
        title_container.pack(side=tk.LEFT, padx=20, pady=20)
        
        title_label = tk.Label(
            title_container, 
            text="MOMENTUM SCANNER", 
            font=("Segoe UI", 16, "bold"),
            bg="#1a1a1a", 
            fg="#ffffff"
        )
        title_label.pack(anchor='w')
        
        subtitle_label = tk.Label(
            title_container,
            text="Real-time cryptocurrency momentum detection",
            font=("Segoe UI", 9),
            bg="#1a1a1a",
            fg="#888888"
        )
        subtitle_label.pack(anchor='w')
        
        # Status section
        status_container = tk.Frame(header_frame, bg="#1a1a1a")
        status_container.pack(side=tk.RIGHT, padx=20, pady=20)
        
        stats_frame = tk.Frame(status_container, bg="#1a1a1a")
        stats_frame.pack()
        
        self.status_label = tk.Label(
            stats_frame,
            text="STOPPED",
            font=("Segoe UI", 11, "bold"),
            bg="#1a1a1a",
            fg="#ff4444",
            width=12
        )
        self.status_label.grid(row=0, column=0, padx=10, sticky='w')
        
        self.scan_label = tk.Label(
            stats_frame,
            text="Scans: 0",
            font=("Segoe UI", 10),
            bg="#1a1a1a",
            fg="#ffffff"
        )
        self.scan_label.grid(row=0, column=1, padx=10)
        
        self.mover_label = tk.Label(
            stats_frame,
            text="Alerts: 0",
            font=("Segoe UI", 10),
            bg="#1a1a1a",
            fg="#ffffff"
        )
        self.mover_label.grid(row=0, column=2, padx=10)
        
        self.time_label = tk.Label(
            stats_frame,
            text="",
            font=("Segoe UI", 9),
            bg="#1a1a1a",
            fg="#888888"
        )
        self.time_label.grid(row=1, column=0, columnspan=3, pady=(5, 0))
        
        # ===== SEPARATOR =====
        separator = tk.Frame(self.root, bg="#333333", height=1)
        separator.pack(fill=tk.X)
        
        # ===== CONTROL PANEL =====
        control_frame = tk.Frame(self.root, bg="#0f0f0f", height=60)
        control_frame.pack(fill=tk.X, padx=0, pady=0)
        control_frame.pack_propagate(False)
        
        button_container = tk.Frame(control_frame, bg="#0f0f0f")
        button_container.pack(side=tk.LEFT, padx=20, pady=10)
        
        self.start_button = tk.Button(
            button_container,
            text="START",
            command=self.start_scanning,
            bg="#00cc66",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            padx=30,
            pady=8,
            relief=tk.FLAT,
            cursor="hand2",
            borderwidth=0
        )
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = tk.Button(
            button_container,
            text="STOP",
            command=self.stop_scanning,
            bg="#cc0000",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            padx=30,
            pady=8,
            relief=tk.FLAT,
            cursor="hand2",
            state=tk.DISABLED,
            borderwidth=0
        )
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        clear_button = tk.Button(
            button_container,
            text="CLEAR",
            command=self.clear_table,
            bg="#333333",
            fg="#ffffff",
            font=("Segoe UI", 10),
            padx=25,
            pady=8,
            relief=tk.FLAT,
            cursor="hand2",
            borderwidth=0
        )
        clear_button.pack(side=tk.LEFT, padx=5)
        
        settings_button = tk.Button(
            button_container,
            text="SETTINGS",
            command=self.open_settings,
            bg="#0066cc",
            fg="#ffffff",
            font=("Segoe UI", 10),
            padx=25,
            pady=8,
            relief=tk.FLAT,
            cursor="hand2",
            borderwidth=0
        )
        settings_button.pack(side=tk.LEFT, padx=5)
        
        # Info label
        info_label = tk.Label(
            control_frame,
            text="Click column headers to sort | Double-click rows for details",
            font=("Segoe UI", 9),
            bg="#0f0f0f",
            fg="#666666"
        )
        info_label.pack(side=tk.RIGHT, padx=20)
        
        # ===== TABLE =====
        table_frame = tk.Frame(self.root, bg="#0a0a0a")
        table_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        
        # Scrollbars
        vsb = ttk.Scrollbar(table_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        
        hsb = ttk.Scrollbar(table_frame, orient="horizontal")
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Professional.Treeview",
            background="#1a1a1a",
            foreground="#ffffff",
            fieldbackground="#1a1a1a",
            borderwidth=0,
            font=("Consolas", 9),
            rowheight=28
        )
        style.configure(
            "Professional.Treeview.Heading", 
            background="#0f0f0f", 
            foreground="#00cc66", 
            font=("Segoe UI", 10, "bold"),
            borderwidth=1,
            relief="flat"
        )
        style.map("Professional.Treeview", 
                 background=[("selected", "#2d4a2d")],
                 foreground=[("selected", "#ffffff")])
        style.map("Professional.Treeview.Heading",
                 background=[("active", "#1a1a1a")])
        
        # Build initial columns
        self.build_table_structure(table_frame, vsb, hsb)
        
    def build_table_structure(self, parent, vsb, hsb):
        """Build or rebuild table structure based on current config"""
        if hasattr(self, 'tree'):
            self.tree.destroy()
        
        columns = ["time", "symbol", "tier", "volume"]
        for tf in sorted(Config.TIMEFRAMES.keys()):
            columns.append(f"price_{tf}")
        columns.append("triggers")
        
        self.tree = ttk.Treeview(
            parent,
            columns=columns,
            show="headings",
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set,
            style="Professional.Treeview",
            selectmode="browse"
        )
        
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)
        
        # Configure columns
        self.tree.heading("time", text="TIME", command=lambda: self.sort_by_column("time"))
        self.tree.heading("symbol", text="SYMBOL", command=lambda: self.sort_by_column("symbol"))
        self.tree.heading("tier", text="TIER", command=lambda: self.sort_by_column("tier"))
        self.tree.heading("volume", text="24H VOLUME", command=lambda: self.sort_by_column("volume"))
        
        for tf in sorted(Config.TIMEFRAMES.keys()):
            tf_label = format_timeframe(tf).upper()
            self.tree.heading(f"price_{tf}", text=f"PRICE {tf_label}", 
                            command=lambda t=tf: self.sort_by_column(f"price_{t}"))
        
        self.tree.heading("triggers", text="TRIGGERS", command=lambda: self.sort_by_column("triggers"))
        
        # Column widths
        self.tree.column("time", width=80, anchor="center")
        self.tree.column("symbol", width=120, anchor="w")
        self.tree.column("tier", width=80, anchor="center")
        self.tree.column("volume", width=120, anchor="e")
        
        for tf in sorted(Config.TIMEFRAMES.keys()):
            self.tree.column(f"price_{tf}", width=110, anchor="e")
        
        self.tree.column("triggers", width=140, anchor="center")
        
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        # Tags
        self.tree.tag_configure("positive", foreground="#00ff88", font=("Consolas", 10, "bold"))
        self.tree.tag_configure("negative", foreground="#ff4444", font=("Consolas", 10, "bold"))
        self.tree.tag_configure("neutral", foreground="#ffcc00")
        
        # Bind double-click
        self.tree.bind("<Double-1>", self.on_row_double_click)
    
    def sort_by_column(self, col):
        """Sort table by column"""
        items = [(self.tree.set(item, col), item) for item in self.tree.get_children('')]
        
        # Determine if numeric
        try:
            items = [(float(val.replace('%', '').replace('$', '').replace('M', '').replace('K', '').replace(',', '')), item) 
                    for val, item in items if val not in ['N/A', 'INF', '']]
            numeric = True
        except:
            numeric = False
            
        if self.sort_column == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_reverse = False
            self.sort_column = col
        
        items.sort(reverse=self.sort_reverse)
        
        # Rearrange items
        for index, (val, item) in enumerate(items):
            self.tree.move(item, '', index)
    
    def on_row_double_click(self, event):
        """Show details on double-click"""
        item = self.tree.selection()[0]
        values = self.tree.item(item, 'values')
        if values:
            details = f"Symbol: {values[1]}\nTier: {values[2]}\nVolume: {values[3]}"
            messagebox.showinfo("Alert Details", details)
    
    def rebuild_table(self):
        """Rebuild table structure (called after config changes)"""
        # Save current data
        current_data = []
        for item in self.tree.get_children():
            current_data.append(self.tree.item(item))
        
        # Rebuild structure
        table_frame = self.tree.master
        vsb = None
        hsb = None
        for widget in table_frame.winfo_children():
            if isinstance(widget, ttk.Scrollbar):
                if widget.cget('orient') == 'vertical':
                    vsb = widget
                else:
                    hsb = widget
        
        self.build_table_structure(table_frame, vsb, hsb)
    
    def open_settings(self):
        """Open settings dialog"""
        SettingsDialog(self.root, self.on_settings_saved)
    
    def on_settings_saved(self):
        """Callback after settings are saved"""
        self.rebuild_table()
        messagebox.showinfo("Configuration", "Settings applied. Restart scanner for changes to take effect.")
    
    def update_time(self):
        if self.running:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.time_label.config(text=f"Last update: {current_time}")
            self.root.after(1000, self.update_time)
    
    def start_scanning(self):
        self.running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_label.config(text="RUNNING", fg="#00cc66")
        
        self.update_time()
        
        thread = threading.Thread(target=self.run_async_loop, daemon=True)
        thread.start()
    
    def stop_scanning(self):
        self.running = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_label.config(text="STOPPED", fg="#ff4444")
    
    def clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.mover_count = 0
        self.mover_label.config(text="Alerts: 0")
    
    def add_mover(self, data):
        time_str, symbol, tier, tf_data, triggers, avg_vol = data
        
        # Determine color
        tag = "neutral"
        strongest_pchg = 0
        for pchg, _ in tf_data.values():
            if pchg is not None and abs(pchg) > abs(strongest_pchg):
                strongest_pchg = pchg
        
        if strongest_pchg > 0:
            tag = "positive"
        elif strongest_pchg < 0:
            tag = "negative"
        
        # Build row
        values = [time_str, symbol, tier, format_volume(avg_vol)]
        
        for tf in sorted(Config.TIMEFRAMES.keys()):
            pchg, vchg = tf_data.get(tf, (None, None))
            values.append(fmt_pct(pchg))
        
        values.append(", ".join(triggers))
        
        self.tree.insert("", 0, values=values, tags=(tag,))
        
        # Limit rows
        children = self.tree.get_children()
        if len(children) > Config.MAX_TABLE_ROWS:
            self.tree.delete(children[-1])
    
    def run_async_loop(self):
        asyncio.run(self.scan_loop())
    
    async def scan_loop(self):
        async with aiohttp.ClientSession() as session:
            # Warm-up
            tick0 = await fetch_tickers(session)
            now = time.time()
            for sym, (price, vol) in tick0.items():
                histories[sym].append((now, price, vol))
                volume_cache[sym] = vol
            
            while self.running:
                start = time.time()
                tickers = await fetch_tickers(session)
                ts = time.time()
                
                if not tickers:
                    await asyncio.sleep(max(1.0, Config.POLL_INTERVAL))
                    continue
                
                self.scan_count += 1
                self.root.after(0, lambda: self.scan_label.config(text=f"Scans: {self.scan_count}"))
                
                movers = []
                
                for sym, (price, vol) in tickers.items():
                    h = histories[sym]
                    h.append((ts, price, vol))
                    prune_history(h, ts)
                
                for sym, (price, vol) in tickers.items():
                    h = histories.get(sym)
                    if not h or len(h) < 2:
                        continue
                    
                    if sym in volume_cache:
                        volume_cache[sym] = 0.9 * volume_cache[sym] + 0.1 * vol
                    else:
                        volume_cache[sym] = vol
                    
                    if volume_cache[sym] < Config.MIN_VOLUME_24H:
                        continue
                    
                    tier_code, tier_display, multiplier = Config.get_tier(volume_cache[sym])
                    
                    timeframe_data = {}
                    is_mover = False
                    reasons = []
                    
                    for timeframe_sec in Config.TIMEFRAMES.keys():
                        snap = find_snapshot_at_or_before(h, ts - timeframe_sec)
                        if not snap:
                            timeframe_data[timeframe_sec] = (None, None)
                            continue
                        
                        _, old_price, old_vol = snap
                        pchg = pct_change(price, old_price)
                        vchg = pct_change(vol, old_vol)
                        
                        timeframe_data[timeframe_sec] = (pchg, vchg)
                        
                        p_thresh, v_thresh = Config.get_thresholds_for_tier(multiplier, timeframe_sec)
                        
                        if pchg is not None and abs(pchg) >= p_thresh:
                            is_mover = True
                            tf_label = format_timeframe(timeframe_sec)
                            reasons.append(f"P{tf_label}")
                        
                        if vchg is not None and abs(vchg) >= v_thresh:
                            is_mover = True
                            tf_label = format_timeframe(timeframe_sec)
                            reasons.append(f"V{tf_label}")
                    
                    if is_mover:
                        movers.append((sym, tier_display, timeframe_data, reasons, volume_cache[sym]))
                
                if movers:
                    # Sort by strongest price change
                    def get_strongest(m):
                        return max([abs(p) for p, _ in m[2].values() if p is not None], default=0)
                    movers.sort(key=get_strongest, reverse=True)
                    
                    for sym, tier, tf_data, reasons, avg_vol in movers:
                        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                        data = (time_str, sym, tier, tf_data, reasons, avg_vol)
                        self.root.after(0, lambda d=data: self.add_mover(d))
                        self.mover_count += 1
                    
                    self.root.after(0, lambda: self.mover_label.config(text=f"Alerts: {self.mover_count}"))
                
                elapsed = time.time() - start
                wait = Config.POLL_INTERVAL - elapsed
                if wait > 0:
                    await asyncio.sleep(wait)


def main():
    root = tk.Tk()
    app = CryptoScannerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()