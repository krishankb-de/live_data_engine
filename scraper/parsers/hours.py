"""Multilingual opening hours parser. DE + EN + FR → DE 2-letter day keys."""

import re
from typing import Optional

DAY_KEYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Maps every spelling to 2-letter DE key
_DAY_MAP = {
    # DE
    "mo": "Mo", "mon": "Mo", "montag": "Mo",
    "di": "Di", "die": "Di", "dienstag": "Di",
    "mi": "Mi", "mit": "Mi", "mittwoch": "Mi",
    "do": "Do", "don": "Do", "donnerstag": "Do",
    "fr": "Fr", "fre": "Fr", "freitag": "Fr",
    "sa": "Sa", "sam": "Sa", "samstag": "Sa", "sonnabend": "Sa",
    "so": "So", "son": "So", "sonntag": "So",
    # EN
    "monday": "Mo", "tuesday": "Di", "wednesday": "Mi",
    "thursday": "Do", "friday": "Fr", "saturday": "Sa", "sunday": "So",
    "tue": "Di", "wed": "Mi", "thu": "Do", "fri": "Fr", "sat": "Sa", "sun": "So",
    # FR
    "lundi": "Mo", "mardi": "Di", "mercredi": "Mi",
    "jeudi": "Do", "vendredi": "Fr", "samedi": "Sa", "dimanche": "So",
    "lun": "Mo", "mar": "Di", "mer": "Mi", "jeu": "Do",
    "ven": "Fr", "sam": "Sa", "dim": "So",

}

_DAY_PATTERN = (
    r'(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonnabend|sonntag|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
    r'lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|'
    r'mon|tue|wed|thu|fri|sat|sun|'
    r'lun|mar|mer|jeu|ven|sam|dim|'
    r'mo|di|mi|do|fr|sa|so)'
)

# DE/EN range:  "Mo-Fr 10:00-18:00", "Mon-Sat 10am-7pm",
#               "Monday to Saturday, 10am-7pm"
_RANGE_RE = re.compile(
    r'\b(?P<day_from>' + _DAY_PATTERN + r')\b'
    r'(?:\s*[\-–—]\s*|\s+(?:to|bis|au|–|—)\s+)'
    r'\b(?P<day_to>' + _DAY_PATTERN + r')\b'
    r'[\s,:.\-–—_]*(?:from|von|de|de\s+)?\s*'
    r'(?P<open>\d{1,2}(?:[:.]\d{2})?)\s*(?P<open_ap>am|pm|h|Uhr|heures?)?\s*'
    r'(?:[\-–—]|to|bis|\s+à\s+)\s*'
    r'(?P<close>\d{1,2}(?:[:.]\d{2})?)\s*(?P<close_ap>am|pm|h|Uhr|heures?)?',
    re.IGNORECASE,
)

# Single day:  "Sa 10:00-14:00", "Le lundi de 14 à 19 heures"
_SINGLE_RE = re.compile(
    r'(?:le\s+)?'
    r'\b(?P<day>' + _DAY_PATTERN + r')\b'
    r'[\s,:.\-–—_]*(?:from|von|de\s+)?\s*'
    r'(?P<open>\d{1,2}(?:[:.]\d{2})?)\s*(?P<open_ap>am|pm|h|Uhr|heures?)?\s*'
    r'(?:[\-–—]|to|bis|\s+à\s+)\s*'
    r'(?P<close>\d{1,2}(?:[:.]\d{2})?)\s*(?P<close_ap>am|pm|h|Uhr|heures?)?',
    re.IGNORECASE,
)


def _to_24h(value: str, ap: Optional[str]) -> Optional[str]:
    """'10' / '10:30' / '10.30' + 'am'|'pm'|None → 'HH:MM'."""
    value = value.replace(".", ":")
    if ":" in value:
        h, m = value.split(":", 1)
    else:
        h, m = value, "00"
    try:
        hi = int(h)
        mi = int(m)
    except ValueError:
        return None
    if ap:
        ap_l = ap.lower()
        if ap_l == "pm" and hi < 12:
            hi += 12
        elif ap_l == "am" and hi == 12:
            hi = 0
    if not (0 <= hi <= 24 and 0 <= mi < 60):
        return None
    return f"{hi:02d}:{mi:02d}"


def _norm_day(raw: str) -> Optional[str]:
    return _DAY_MAP.get(raw.lower())


def _expand_range(d_from: str, d_to: str) -> list[str]:
    """Mo-Fr → ['Mo','Di','Mi','Do','Fr']."""
    if d_from not in DAY_KEYS or d_to not in DAY_KEYS:
        return [d_from] if d_from in DAY_KEYS else []
    i, j = DAY_KEYS.index(d_from), DAY_KEYS.index(d_to)
    if j < i:
        return [d_from, d_to]
    return list(DAY_KEYS[i : j + 1])


def parse_opening_hours(text: str) -> Optional[dict]:
    """
    Return {"Mo": "10:00-18:00", ...} with one entry per day covered.
    Compacted output groups consecutive days with same hours into Mo-Fr.
    """
    by_day: dict[str, str] = {}

    for m in _RANGE_RE.finditer(text):
        d_from = _norm_day(m.group("day_from"))
        d_to = _norm_day(m.group("day_to"))
        if not d_from or not d_to:
            continue
        open_ap = m.group("open_ap")
        close_ap = m.group("close_ap")
        # If only close has am/pm, propagate to open
        if close_ap and close_ap.lower() in ("am", "pm") and not open_ap:
            open_ap = close_ap
        t_open = _to_24h(m.group("open"), open_ap)
        t_close = _to_24h(m.group("close"), close_ap)
        if not t_open or not t_close:
            continue
        for d in _expand_range(d_from, d_to):
            by_day.setdefault(d, f"{t_open}-{t_close}")

    for m in _SINGLE_RE.finditer(text):
        d = _norm_day(m.group("day"))
        if not d:
            continue
        open_ap = m.group("open_ap")
        close_ap = m.group("close_ap")
        if close_ap and close_ap.lower() in ("am", "pm") and not open_ap:
            open_ap = close_ap
        t_open = _to_24h(m.group("open"), open_ap)
        t_close = _to_24h(m.group("close"), close_ap)
        if not t_open or not t_close:
            continue
        by_day.setdefault(d, f"{t_open}-{t_close}")

    if not by_day:
        return None
    return _compact(by_day)


def _compact(by_day: dict[str, str]) -> dict[str, str]:
    """Mo:X Di:X Mi:X ... → 'Mo-Mi': X. Keeps individual days when hours differ."""
    out: dict[str, str] = {}
    run_start = None
    run_prev = None
    run_hours = None
    for day in DAY_KEYS:
        h = by_day.get(day)
        if h is None:
            if run_start:
                key = run_start if run_start == run_prev else f"{run_start}-{run_prev}"
                out[key] = run_hours
            run_start = run_prev = run_hours = None
            continue
        if run_hours == h:
            run_prev = day
        else:
            if run_start:
                key = run_start if run_start == run_prev else f"{run_start}-{run_prev}"
                out[key] = run_hours
            run_start = run_prev = day
            run_hours = h
    if run_start:
        key = run_start if run_start == run_prev else f"{run_start}-{run_prev}"
        out[key] = run_hours
    return out
