"""Disk-backed cache for price bars matching BarCache.cs."""
import os, json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from strategy.models import PriceBar

class BarCache:
    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir:
            self._dir = Path(cache_dir)
        else:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                self._dir = Path(local_appdata) / "BackTester" / "bar_cache"
                # If legacy AlpacaWpf cache exists and BackTester cache doesn't, migrate files
                legacy_dir = Path(local_appdata) / "AlpacaWpf" / "bar_cache"
                if legacy_dir.exists() and not self._dir.exists():
                    try:
                        import shutil
                        self._dir.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(legacy_dir, self._dir)
                    except Exception:
                        pass
            else:
                self._dir = Path(__file__).resolve().parent.parent / "cache"
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def timeframe_key(timeframe_val: int, unit: str) -> str:
        # e.g., 15Min, 1Hour, 1Day
        unit_clean = unit.capitalize()
        if unit_clean.startswith("Min"):
            return f"{timeframe_val}Min"
        elif unit_clean.startswith("Hour"):
            return f"{timeframe_val}Hour"
        elif unit_clean.startswith("Day"):
            return f"{timeframe_val}Day"
        return f"{timeframe_val}{unit}"

    def _path_for(self, symbol: str, tf_key: str) -> Path:
        return self._dir / f"{symbol.upper()}_{tf_key}.json"

    def try_get(self, symbol: str, tf_key: str, request_start: datetime, request_end: datetime) -> Optional[List[PriceBar]]:
        path = self._path_for(symbol, tf_key)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            fetched_at = datetime.fromisoformat(data["fetched_at_utc"])
            now_utc = datetime.now(timezone.utc)
            if fetched_at.date() != now_utc.date():
                return None
            range_start = datetime.fromisoformat(data["range_start_utc"])
            if range_start > request_start:
                return None  # Doesn't cover requested start

            bars: List[PriceBar] = []
            for b in data.get("bars", []):
                t = datetime.fromisoformat(b["time_utc"])
                if request_start <= t <= request_end:
                    bars.append(PriceBar(
                        time_utc=t,
                        open=float(b["open"]),
                        high=float(b["high"]),
                        low=float(b["low"]),
                        close=float(b["close"]),
                        volume=float(b["volume"]),
                    ))
            return bars
        except Exception:
            return None

    def store(self, symbol: str, tf_key: str, range_start: datetime, range_end: datetime, bars: List[PriceBar]):
        path = self._path_for(symbol, tf_key)
        now_utc = datetime.now(timezone.utc)
        payload = {
            "fetched_at_utc": now_utc.isoformat(),
            "range_start_utc": range_start.isoformat(),
            "range_end_utc": range_end.isoformat(),
            "bars": [
                {
                    "time_utc": b.time_utc.isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ]
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            pass