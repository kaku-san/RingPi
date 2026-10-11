"""Local visit-window clock formatting. A window is a schedule, not an identity."""
from datetime import datetime, time, date, timedelta
import re
from zoneinfo import ZoneInfo


_TIME = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def format_window(start, end):
    def parts(value):
        hour = value.hour % 12 or 12
        suffix = 'AM' if value.hour < 12 else 'PM'
        return f'{hour}:{value.minute:02d}', suffix
    left, left_suffix = parts(start)
    right, right_suffix = parts(end)
    if left_suffix == right_suffix:
        return f'{left}-{right} {right_suffix}'
    return f'{left} {left_suffix}-{right} {right_suffix}'


def outside_window_text(label):
    return f'recognized guest, outside allowed window ({label})'


class GuestWindow(tuple):
    """(start, end, label). daily means the clock repeats every day."""

    def __new__(cls, start, end, label, daily=False, start_text='', end_text=''):
        window = super().__new__(cls, (start, end, label))
        window.daily = bool(daily)
        window.start_text = start_text
        window.end_text = end_text
        return window


def _on_day(day, start_text, end_text, zone):
    start = datetime.combine(day, time.fromisoformat(start_text), zone)
    end = datetime.combine(day, time.fromisoformat(end_text), zone)
    if end <= start:
        raise ValueError('Window end must be later than the start on that date')
    return start, end


def resolve_guest_window(now, timezone_name, start_text, end_text, date_text=None):
    """Resolve HH:MM bounds in the configured timezone.

    No date means the same hours every day there. A date applies to that date only.
    """
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError('Guest timezone is not configured')
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        raise ValueError('Guest timezone is not configured') from None
    if now.tzinfo is None:
        raise ValueError('Guest window needs a timezone-aware clock')
    if (not isinstance(start_text, str) or not isinstance(end_text, str)
            or not _TIME.fullmatch(start_text) or not _TIME.fullmatch(end_text)):
        raise ValueError('Use HH:MM in 24-hour time')
    local_now = now.astimezone(zone)
    daily = date_text is None
    if daily:
        day = local_now.date()
    else:
        if not isinstance(date_text, str) or not _DATE.fullmatch(date_text):
            raise ValueError('Use a YYYY-MM-DD date')
        try:
            day = date.fromisoformat(date_text)
        except ValueError:
            raise ValueError('Use a YYYY-MM-DD date') from None
    start, end = _on_day(day, start_text, end_text, zone)
    if daily and end <= local_now:
        start, end = _on_day(day + timedelta(days=1), start_text, end_text, zone)
    return GuestWindow(start, end, format_window(start, end), daily, start_text, end_text)
