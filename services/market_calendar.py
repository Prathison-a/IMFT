"""NYSE trading calendar implementation matching MarketCalendar.cs."""
from datetime import date, time, timedelta
from typing import Set, Dict

REGULAR_OPEN_ET = time(9, 30, 0)
REGULAR_CLOSE_ET = time(16, 0, 0)
EARLY_CLOSE_ET = time(13, 0, 0)

_holiday_cache: Dict[int, Set[date]] = {}

def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Monday is 0, Sunday is 6."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday() + 7) % 7
    return first + timedelta(days=offset + 7 * (n - 1))

def last_weekday(year: int, month: int, weekday: int) -> date:
    # Determine last day of month
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday + 7) % 7
    return last - timedelta(days=offset)

def observed(d: date) -> date:
    """Saturday -> preceding Friday, Sunday -> following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d

def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)

def build_holidays(year: int) -> Set[date]:
    holidays = set()

    # New Year's Day
    new_year = observed(date(year, 1, 1))
    if new_year.year == year:
        holidays.add(new_year)

    holidays.add(nth_weekday(year, 1, 0, 3))   # MLK Day (3rd Monday of Jan)
    holidays.add(nth_weekday(year, 2, 0, 3))   # Washington's Birthday (3rd Monday of Feb)
    holidays.add(easter_sunday(year) - timedelta(days=2))  # Good Friday
    holidays.add(last_weekday(year, 5, 0))     # Memorial Day (last Monday of May)

    if year >= 2022:
        holidays.add(observed(date(year, 6, 19)))  # Juneteenth

    holidays.add(observed(date(year, 7, 4)))   # Independence Day
    holidays.add(nth_weekday(year, 9, 0, 1))   # Labor Day (1st Monday of Sep)
    holidays.add(nth_weekday(year, 11, 3, 4))  # Thanksgiving (4th Thursday of Nov)
    holidays.add(observed(date(year, 12, 25))) # Christmas Day

    return holidays

def holidays_for(year: int) -> Set[date]:
    if year not in _holiday_cache:
        _holiday_cache[year] = build_holidays(year)
    return _holiday_cache[year]

def is_holiday(d: date) -> bool:
    return d in holidays_for(d.year)

def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    return not is_holiday(d)

def is_early_close(d: date) -> bool:
    if not is_trading_day(d):
        return False
    # Day after Thanksgiving (Black Friday)
    black_friday = nth_weekday(d.year, 11, 3, 4) + timedelta(days=1)
    if d == black_friday:
        return True
    # Christmas Eve Dec 24
    if d.month == 12 and d.day == 24:
        return True
    # July 3rd
    if d.month == 7 and d.day == 3:
        return True
    return False

def get_session_close_et(d: date) -> time:
    return EARLY_CLOSE_ET if is_early_close(d) else REGULAR_CLOSE_ET

class MarketCalendar:
    @staticmethod
    def is_trading_day(d):
        return is_trading_day(d)

    @staticmethod
    def is_holiday(d):
        return is_holiday(d)

    @staticmethod
    def is_early_close(d):
        return is_early_close(d)

    @staticmethod
    def get_session_close_et(d):
        return get_session_close_et(d)

    @staticmethod
    def is_market_open(dt_val):
        import pytz
        et_tz = pytz.timezone('America/New_York')
        if dt_val.tzinfo is None:
            dt_et = et_tz.localize(dt_val)
        else:
            dt_et = dt_val.astimezone(et_tz)
        d = dt_et.date()
        if not is_trading_day(d):
            return False
        close_t = get_session_close_et(d)
        return REGULAR_OPEN_ET <= dt_et.time() <= close_t
