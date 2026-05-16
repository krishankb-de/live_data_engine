"""Parser subpackage: jsonld, phone, address, hours, name."""

from .jsonld import parse_jsonld
from .phone import parse_phone
from .address import parse_address
from .hours import parse_opening_hours, DAY_KEYS
from .name import parse_name_from_page

__all__ = [
    "parse_jsonld",
    "parse_phone",
    "parse_address",
    "parse_opening_hours",
    "parse_name_from_page",
    "DAY_KEYS",
]
