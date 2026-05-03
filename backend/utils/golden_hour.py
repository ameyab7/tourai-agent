"""utils/golden_hour.py — Golden/blue hour windows from sunrise/sunset ISO strings."""

from datetime import datetime, timedelta, timezone


def _parse(iso: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def get_light_windows(sunrise_iso: str, sunset_iso: str) -> dict:
    """
    Returns minutes until the next golden/blue hour window and its label.

    Golden hour: 30 min before sunrise/sunset → 30 min after.
    Blue hour:   30–60 min before sunrise; 30–60 min after sunset.
    """
    now     = datetime.now(timezone.utc)
    sunrise = _parse(sunrise_iso)
    sunset  = _parse(sunset_iso)
    if not sunrise or not sunset:
        return {"active": False, "label": None, "minutes_away": None}

    windows = [
        ("Morning blue hour",   sunrise - timedelta(minutes=60), sunrise - timedelta(minutes=30)),
        ("Morning golden hour", sunrise - timedelta(minutes=30), sunrise + timedelta(minutes=30)),
        ("Evening golden hour", sunset  - timedelta(minutes=30), sunset  + timedelta(minutes=30)),
        ("Evening blue hour",   sunset  + timedelta(minutes=30), sunset  + timedelta(minutes=60)),
    ]

    for label, start, end in windows:
        if start <= now <= end:
            return {"active": True,  "label": label, "minutes_away": 0}
        if now < start:
            minutes_away = int((start - now).total_seconds() / 60)
            return {"active": False, "label": label, "minutes_away": minutes_away}

    return {"active": False, "label": None, "minutes_away": None}
