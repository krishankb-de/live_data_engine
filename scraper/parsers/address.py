"""Multilingual address parser. PLZ-anchored, multi-separator, city-validated."""

import re
from typing import Optional

_NOISE_HEAD = re.compile(
    r'^(Telefon|Tel|Fax|E-Mail|Mail|Phone|Web|www|http|©|Impressum|'
    r'Öffnungszeiten|Opening|Kontakt|Contact|Umsatzsteuer|Handelsregister|'
    r'Amtsgericht|Geschäftsführer|Inhaber|GmbH|UG|AG|KG|GbR)\b',
    re.IGNORECASE,
)

# Street separator: comma, newline, em-dash, en-dash, bullet, double-space.
# \x95 = Windows-1252 bullet used by some CMS sites (e.g. Walther König stores page)
_SEP = r'[,\n\r·•\x95|]|\s+[–—-]\s+|\s{2,}'

_PLZ_RE = re.compile(
    r'(?P<street>[A-Za-zÄÖÜäöüß][^\n\r,·•|]{4,80}?\d+[a-zA-Z]?)\s*'
    r'(?:' + _SEP + r')\s*'
    r'(?P<plz>\d{5})\s+'
    r'(?P<city>[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-\s]{2,40})',
    re.UNICODE,
)


def _clean_city(city: str) -> str:
    city = city.split("\n")[0].split("\t")[0].strip()
    city = re.split(r'\s*[,\|·•–—]\s*|\s{2,}', city)[0].strip()
    return city.rstrip(".,;:")


def _clean_street(street: str) -> str:
    return street.strip().strip(",").strip("–—").strip()


def parse_address(text: str, target_city: Optional[str] = None) -> Optional[str]:
    """
    Parse first address whose city matches target_city (case-insensitive
    substring). If target_city is None, return first match. If target_city
    given and no match found, return None (so caller tries the next page).
    """
    matches = list(_PLZ_RE.finditer(text))
    if not matches:
        # Fallback: bare "PLZ City" with no street
        simple = re.search(
            r'(\d{5})\s+([A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-\s]{2,30})', text
        )
        if simple and (
            not target_city or target_city.lower() in simple.group(2).lower()
        ):
            return f"{simple.group(1)} {_clean_city(simple.group(2))}"
        return None

    for m in matches:
        street = _clean_street(m.group("street"))
        plz = m.group("plz")
        city = _clean_city(m.group("city"))
        if _NOISE_HEAD.match(street):
            continue
        if target_city and target_city.lower() not in city.lower():
            continue
        return f"{street}, {plz} {city}"

    # No city-matching address — return None so caller continues
    if target_city:
        return None

    m = matches[0]
    return (
        f"{_clean_street(m.group('street'))}, "
        f"{m.group('plz')} {_clean_city(m.group('city'))}"
    )
