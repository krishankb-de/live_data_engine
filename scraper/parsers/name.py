"""Business name parser. og:site_name → h1 → title."""

import re
from typing import Optional


def parse_name_from_page(page) -> Optional[str]:
    try:
        og = page.css('meta[property="og:site_name"]::attr(content)').get()
        if og and og.strip():
            return og.strip()
    except Exception:
        pass
    try:
        h1 = page.css("h1::text").get()
        if h1 and h1.strip():
            return h1.strip()
    except Exception:
        pass
    try:
        title = page.css("title::text").get()
        if title:
            name = re.split(r'\s*[\|–—\-]\s*', title)[0].strip()
            return name if name else None
    except Exception:
        pass
    return None
