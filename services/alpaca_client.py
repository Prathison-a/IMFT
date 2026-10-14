import os
from pathlib import Path

# Load .env file from project root if present
def _load_env_file():
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env"
    ]
    env_path = next((p for p in candidates if p.exists()), None)
    if not env_path:
        return

    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)
    except Exception:
        pass

    # Native parser fallback / clean-up (strip surrounding quotes & whitespace)
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("\"'").strip()
                    if k:
                        os.environ[k] = v
    except Exception:
        pass

_load_env_file()

"""Alpaca historical data fetching service matching AlpacaDataService.cs."""
import os, time, requests
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Callable
from strategy.models import PriceBar
from services.bar_cache import BarCache
from services.api_tracker import ApiCallTracker

class AlpacaRateLimitException(Exception):
    def __init__(self, message: str, reset_time_utc: Optional[datetime] = None):
        super().__init__(message)
        self.reset_time_utc = reset_time_utc

class AlpacaDataService:
    DEFAULT_KEY = ""
    DEFAULT_SECRET = ""
    # Paper Trading Base URL & Market Data URL
    PAPER_BASE_URL = os.environ.get("ALPACA_API_URL") or os.environ.get("ALPACA_BASE_URL") or os.environ.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets/v2"
    DATA_BASE_URL = os.environ.get("ALPACA_DATA_URL") or os.environ.get("APCA_API_DATA_URL") or "https://data.alpaca.markets/v2/stocks/bars"

    def __init__(self, tracker: Optional[ApiCallTracker] = None, cache: Optional[BarCache] = None):
        self.base_url = self.DATA_BASE_URL
        self.paper_url = self.PAPER_BASE_URL
        self.tracker = tracker or ApiCallTracker()
        self.cache = cache or BarCache()
        self.target_requests_per_minute = 150
        self._last_call_at_utc = 0.0

    @property
    def api_key(self) -> str:
        _load_env_file()
        val = (
            os.environ.get("ALPACA_API_KEY")
            or os.environ.get("ALPACA_API_KEY_ID")
            or os.environ.get("APCA_API_KEY_ID")
            or os.environ.get("ALPACA_KEY")
            or os.environ.get("API_KEY")
            or self.DEFAULT_KEY
        )
        return val.strip().strip("\"'").strip() if val else ""

    @property
    def api_secret(self) -> str:
        _load_env_file()
        val = (
            os.environ.get("ALPACA_SECRET_KEY")
            or os.environ.get("ALPACA_API_SECRET_KEY")
            or os.environ.get("APCA_API_SECRET_KEY")
            or os.environ.get("ALPACA_SECRET")
            or os.environ.get("SECRET_KEY")
            or self.DEFAULT_SECRET
        )
        return val.strip().strip("\"'").strip() if val else ""

    def _throttle(self, progress_callback: Optional[Callable[[str], None]] = None):
        min_interval = 60.0 / max(1, self.target_requests_per_minute)
        elapsed = time.time() - self._last_call_at_utc
        if elapsed < min_interval:
            wait = min_interval - elapsed
            if progress_callback:
                progress_callback(f"Pacing to stay under free-tier limit ({self.target_requests_per_minute}/min) -- waiting {wait*1000:.0f}ms.")
            time.sleep(wait)

    @property
    def is_configured(self) -> bool:
        _load_env_file()
        key = (self.api_key or "").strip()
        secret = (self.api_secret or "").strip()
        if not key or not secret:
            return False
        if "YOUR_ALPACA" in key.upper() or "YOUR_KEY" in key.upper() or "YOUR_SECRET" in secret.upper():
            return False
        return True

    def get_bars(
        self,
        symbol: str,
        start_utc: datetime,
        end_utc: datetime,
        timeframe_val: int = 15,
        timeframe_unit: str = "Min",
        progress_callback: Optional[Callable[[str], None]] = None,
        force_refresh: bool = False,
        feed: str = "sip"
    ) -> List[PriceBar]:
        symbol = symbol.strip().upper()
        tf_key = BarCache.timeframe_key(timeframe_val, timeframe_unit)

        if not force_refresh:
            cached = self.cache.try_get(symbol, tf_key, start_utc, end_utc)
            if cached is not None:
                if progress_callback:
                    progress_callback(f"{symbol} {tf_key}: already downloaded today -- using {len(cached)} cached bars, no API calls made.")
                return cached

        if not self.is_configured:
            raise Exception(
                "Alpaca API credentials not configured. "
                "Please open your .env file and paste your real Alpaca Paper Trading API Key and Secret Key."
            )

        # Format timeframe string for Alpaca API, e.g., 15Min, 1Hour, 1Day
        alpaca_tf = f"{timeframe_val}{timeframe_unit}"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

        start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "symbols": symbol,
            "timeframe": alpaca_tf,
            "start": start_str,
            "end": end_str,
            "limit": 10000,
            "adjustment": "all",
            "feed": feed,
        }

        all_bars: List[PriceBar] = []
        page_token = None
        page = 0

        while True:
            page += 1
            self._throttle(progress_callback)

            if page_token:
                params["page_token"] = page_token
            elif "page_token" in params:
                del params["page_token"]

            try:
                resp = requests.get(self.base_url, headers=headers, params=params, timeout=60)
            finally:
                self._last_call_at_utc = time.time()

            if resp.status_code == 401:
                self.tracker.record("GetHistoricalBars", f"{symbol} HTTP_401_UNAUTHORIZED")
                raise Exception(
                    "Alpaca API authentication failed (HTTP 401: Unauthorized). "
                    "Please open your .env file and set your actual ALPACA_API_KEY and ALPACA_SECRET_KEY credentials."
                )

            if resp.status_code == 403:
                self.tracker.record("GetHistoricalBars", f"{symbol} HTTP_403_FORBIDDEN")
                raise Exception(
                    "Alpaca API access forbidden (HTTP 403). Please verify your account permissions and subscription tier."
                )

            if resp.status_code == 429:
                self.tracker.record("GetHistoricalBars", f"{symbol} RATE_LIMITED")
                reset_val = resp.headers.get("X-RateLimit-Reset")
                reset_time = datetime.fromtimestamp(int(reset_val), tz=timezone.utc) if reset_val else None
                msg = f"Alpaca rate limit hit while fetching {symbol} (page {page}). "
                msg += f"Resets at {reset_time.strftime('%H:%M:%S')} UTC." if reset_time else "Wait a bit and try again."
                raise AlpacaRateLimitException(msg, reset_time)

            if resp.status_code != 200:
                self.tracker.record("GetHistoricalBars", f"{symbol} HTTP_{resp.status_code}")
                raise Exception(f"Alpaca HTTP {resp.status_code}: {resp.text[:300]}")

            self.tracker.record("GetHistoricalBars", f"{symbol} page {page}")
            data = resp.json()
            bars_dict = data.get("bars", {})
            raw_bars = bars_dict.get(symbol, []) or []

            bars_this_page = 0
            for b in raw_bars:
                # b['t'] is ISO-8601 string, e.g. "2026-09-28T13:30:00Z"
                t_str = b["t"]
                t = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                all_bars.append(PriceBar(
                    time_utc=t,
                    open=float(b["o"]),
                    high=float(b["h"]),
                    low=float(b["l"]),
                    close=float(b["c"]),
                    volume=float(b["v"]),
                ))
                bars_this_page += 1

            if progress_callback:
                progress_callback(f"{symbol}: page {page}, {bars_this_page} bars (total {len(all_bars)})")

            # Check rate limit headroom
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining is not None and int(remaining) <= 3:
                reset_val = resp.headers.get("X-RateLimit-Reset")
                if reset_val:
                    wait_sec = max(1.0, int(reset_val) - time.time())
                    if progress_callback:
                        progress_callback(f"Close to Alpaca rate limit ({remaining} left) -- pausing {wait_sec:.0f}s until reset.")
                    time.sleep(wait_sec)

            page_token = data.get("next_page_token")
            if not page_token:
                break

        # Deduplicate & sort
        seen = set()
        deduped: List[PriceBar] = []
        for b in sorted(all_bars, key=lambda x: x.time_utc):
            if b.time_utc not in seen:
                seen.add(b.time_utc)
                deduped.append(b)

        self.cache.store(symbol, tf_key, start_utc, end_utc, deduped)
        return deduped
AlpacaClient = AlpacaDataService
