import re
from typing import Tuple

def clean_text(value: object) -> str:
    text = str(value or "")
    text = text.replace("\u00A0", " ").replace("Â", "").replace("â€“", "-")
    return re.sub(r"\s+", " ", text).strip()

def clean_date(value: object) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    return ""

def join_location(city: object, state: object) -> str:
    parts = [clean_text(city), clean_text(state)]
    return ", ".join([p for p in parts if p])

def norm_org(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_text(value).lower())

def norm_state(value: str) -> str:
    from ..config import Config
    raw = clean_text(value)
    if not raw:
        return ""
    if raw in Config.US_STATES:
        return raw
    upper = raw.upper()
    if upper in Config.STATE_ABBR:
        return Config.STATE_ABBR[upper]
    return ""
