"""API call tracking and persistence matching ApiCallTracker.cs."""
import os, threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple, Dict

@dataclass
class ApiCallRecord:
    time_utc: datetime
    endpoint: str
    detail: str

    def to_dict(self):
        return {
            "time": self.time_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "endpoint": self.endpoint,
            "detail": self.detail,
        }

class ApiCallTracker:
    RETAIN_DAYS = 35

    def __init__(self, log_path: str = None):
        self._lock = threading.Lock()
        self._calls: List[ApiCallRecord] = []

        if log_path:
            self._log_path = Path(log_path)
        else:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                self._log_path = Path(local_appdata) / "BackTester" / "api_calls.log"
                # If legacy AlpacaWpf log exists and BackTester log doesn't, migrate it
                legacy_log = Path(local_appdata) / "AlpacaWpf" / "api_calls.log"
                if legacy_log.exists() and not self._log_path.exists():
                    try:
                        self._log_path.parent.mkdir(parents=True, exist_ok=True)
                        import shutil
                        shutil.copy2(legacy_log, self._log_path)
                    except Exception:
                        pass
            else:
                self._log_path = Path(__file__).resolve().parent.parent / "logs" / "api_calls.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def _load_existing(self):
        if not self._log_path.exists():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.RETAIN_DAYS)
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split("\t", 2)
                    if len(parts) < 2:
                        continue
                    try:
                        t = datetime.fromisoformat(parts[0])
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        if t < cutoff:
                            continue
                        endpoint = parts[1]
                        detail = parts[2] if len(parts) > 2 else ""
                        self._calls.append(ApiCallRecord(t, endpoint, detail))
                    except Exception:
                        continue
        except Exception:
            pass

    def record(self, endpoint: str, detail: str = ""):
        now = datetime.now(timezone.utc)
        rec = ApiCallRecord(now, endpoint, detail)
        with self._lock:
            self._calls.append(rec)
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(f"{now.isoformat()}\t{endpoint}\t{detail}\n")
            except Exception:
                pass

    def count_since(self, since_utc: datetime) -> int:
        with self._lock:
            return sum(1 for c in self._calls if c.time_utc >= since_utc)

    def count_last_minute(self) -> int:
        now = datetime.now(timezone.utc)
        return self.count_since(now - timedelta(minutes=1))

    def count_last_hour(self) -> int:
        now = datetime.now(timezone.utc)
        return self.count_since(now - timedelta(hours=1))

    def count_today(self) -> int:
        now = datetime.now(timezone.utc)
        start_of_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        return self.count_since(start_of_today)

    def counts_by_day(self, last_n_days: int = 30) -> List[Tuple[str, int]]:
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=last_n_days - 1)).date()
        with self._lock:
            counts: Dict[str, int] = {}
            for i in range(last_n_days):
                d = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
                counts[d] = 0

            for c in self._calls:
                c_date = c.time_utc.date()
                if c_date >= start_date:
                    d_str = c_date.strftime("%Y-%m-%d")
                    counts[d_str] = counts.get(d_str, 0) + 1

            return sorted(counts.items(), key=lambda x: x[0])

    def counts_by_hour_today(self) -> List[Tuple[int, int]]:
        now = datetime.now(timezone.utc)
        today = now.date()
        with self._lock:
            by_hour = {h: 0 for h in range(24)}
            for c in self._calls:
                if c.time_utc.date() == today:
                    by_hour[c.time_utc.hour] += 1
            return [(h, by_hour[h]) for h in range(24)]

    def recent_calls(self, count: int = 100) -> List[dict]:
        with self._lock:
            recent = sorted(self._calls, key=lambda c: c.time_utc, reverse=True)[:count]
            return [c.to_dict() for c in recent]
ApiTracker = ApiCallTracker
