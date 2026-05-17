"""Multilingual phone parser. Handles DE/FR formats with em-dash, dots, slashes.sdsdsdsdsd"""

import re
from typing import Optional

_PHONE_RE = re.compile(
    r'(?<!\d)'
    r'('
    r'(?:\+(?:49|33|41)[\s\-–—\./]*(?:\(0\))?[\s\-–—\./]*)'
    r'|(?:\(\s*0\s*\d{1,4}\s*\)[\s\-–—\./]+)'
    r'|(?:0\d{2,4}[\s\-–—\./]+)'
    r')'
    r'\d[\d\s\-–—\./]{4,}\d',
    re.UNICODE,
)


def parse_phone(text: str) -> Optional[str]:
    """Return E.164-ish normalized phone, or None."""
    m = _PHONE_RE.search(text)
    if not m:
        return None
    raw = m.group(0).strip()
    digits = re.sub(r'[^\d+]', '', raw)
    if len(digits) < 7:
        return None
    digits = re.sub(r'^\+490', '+49', digits)
    digits = re.sub(r'^\+330', '+33', digits)
    return digits
