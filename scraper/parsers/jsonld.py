"""schema.org JSON-LD parser. Walks LocalBusiness / Organization for fields."""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

BIZ_TYPES = {
    "LocalBusiness", "Store", "BookStore", "Restaurant", "Cafe",
    "Organization", "Corporation", "Place",
}

# Map schema.org day URIs / strings → DE 2-letter keys
_DAY_NORM = {
    "monday": "Mo", "tuesday": "Di", "wednesday": "Mi",
    "thursday": "Do", "friday": "Fr", "saturday": "Sa", "sunday": "So",
    "mo": "Mo", "tu": "Di", "we": "Mi", "th": "Do",
    "fr": "Fr", "sa": "Sa", "su": "So",
}


def _iter_jsonld_blocks(page):
    try:
        for node in page.css('script[type="application/ld+json"]'):
            raw = node.css("::text").get() or ""
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                # Some sites concatenate multiple objects; try to salvage
                try:
                    yield json.loads(raw.rstrip(",") + "]" if raw.startswith("[") else raw)
                except Exception:
                    continue
    except Exception as e:
        logger.debug("jsonld iter failed: %s", e)


def _walk(obj):
    """Yield every dict inside a nested JSON-LD value."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _is_business(d: dict) -> bool:
    t = d.get("@type")
    if isinstance(t, list):
        return any(x in BIZ_TYPES for x in t)
    return t in BIZ_TYPES


def _norm_day_uri(s: str) -> Optional[str]:
    s = s.lower().rsplit("/", 1)[-1]
    return _DAY_NORM.get(s) or _DAY_NORM.get(s[:2])


def _extract_address(node: dict) -> Optional[str]:
    a = node.get("address")
    if not a:
        return None
    if isinstance(a, list):
        a = a[0]
    if isinstance(a, str):
        return a.strip() or None
    if not isinstance(a, dict):
        return None
    street = a.get("streetAddress", "")
    plz = a.get("postalCode", "")
    city = a.get("addressLocality", "")
    parts = [p for p in (street, f"{plz} {city}".strip()) if p]
    return ", ".join(parts) if parts else None


def _extract_hours(node: dict) -> Optional[dict]:
    """
    Handle openingHours string array AND openingHoursSpecification objects.
    """
    out: dict[str, str] = {}

    oh = node.get("openingHours")
    if isinstance(oh, str):
        oh = [oh]
    if isinstance(oh, list):
        for spec in oh:
            if not isinstance(spec, str):
                continue
            # Format: "Mo-Fr 10:00-18:00" or "Mo,Tu 09:00-12:00"
            m = re.match(
                r'([A-Za-z,\- ]+)\s+(\d{1,2}:\d{2})-(\d{1,2}:\d{2})',
                spec.strip(),
            )
            if not m:
                continue
            days_raw, t_open, t_close = m.groups()
            for d in _expand_day_spec(days_raw):
                out.setdefault(d, f"{t_open}-{t_close}")

    ohs = node.get("openingHoursSpecification")
    if isinstance(ohs, dict):
        ohs = [ohs]
    if isinstance(ohs, list):
        for spec in ohs:
            if not isinstance(spec, dict):
                continue
            t_open = (spec.get("opens") or "")[:5]
            t_close = (spec.get("closes") or "")[:5]
            if not t_open or not t_close:
                continue
            days = spec.get("dayOfWeek") or []
            if isinstance(days, str):
                days = [days]
            for raw in days:
                d = _norm_day_uri(str(raw))
                if d:
                    out.setdefault(d, f"{t_open}-{t_close}")

    if not out:
        return None
    from .hours import _compact  # local import to avoid cycle on first import
    return _compact(out)


def _expand_day_spec(days_raw: str) -> list[str]:
    """'Mo-Fr', 'Mo,Tu', 'Monday' → list of DE 2-letter keys."""
    from .hours import DAY_KEYS
    result: list[str] = []
    parts = [p.strip() for p in days_raw.split(",")]
    for p in parts:
        if "-" in p:
            a, b = [x.strip() for x in p.split("-", 1)]
            a_n = _DAY_NORM.get(a.lower()) or _DAY_NORM.get(a.lower()[:2])
            b_n = _DAY_NORM.get(b.lower()) or _DAY_NORM.get(b.lower()[:2])
            if a_n and b_n and a_n in DAY_KEYS and b_n in DAY_KEYS:
                i, j = DAY_KEYS.index(a_n), DAY_KEYS.index(b_n)
                if j >= i:
                    result.extend(DAY_KEYS[i : j + 1])
        else:
            d = _DAY_NORM.get(p.lower()) or _DAY_NORM.get(p.lower()[:2])
            if d:
                result.append(d)
    return result


def parse_jsonld(page) -> dict:
    """
    Return {name?, address?, phone?, opening_hours?} from any
    LocalBusiness/Organization node found in <script type=ld+json>.
    """
    found: dict = {}
    for block in _iter_jsonld_blocks(page):
        for node in _walk(block):
            if not isinstance(node, dict) or not _is_business(node):
                continue
            if "name" not in found:
                n = node.get("name")
                if isinstance(n, str) and n.strip():
                    found["name"] = n.strip()
            if "phone" not in found:
                t = node.get("telephone")
                if isinstance(t, str) and t.strip():
                    found["phone"] = re.sub(r'[^\d+]', '', t)
            if "address" not in found:
                a = _extract_address(node)
                if a:
                    found["address"] = a
            if "opening_hours" not in found:
                h = _extract_hours(node)
                if h:
                    found["opening_hours"] = h
            if len(found) == 4:
                return found
    return found
