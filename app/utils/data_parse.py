import re
from typing import Tuple, List
from .text_utils import clean_text
from ..config import Config

def parse_meta_from_file(file_name: str) -> Tuple[str, str, str, str]:
    base = file_name.lower().replace(".csv", "")
    org_code = (base.split("_")[-1] or "").upper()
    org_name, entity_type = Config.ORG_MAP.get(org_code, (org_code, "Unknown"))

    scope = "Unknown"
    if "greatlakes" in base:
        scope = "Great Lakes Collegiate"
    elif "eatern" in base:
        scope = "Eastern Collegiate"
    elif "collegiate" in base:
        scope = "Collegiate"
    elif "graduate" in base:
        scope = "Graduate"
    elif "alumni" in base:
        scope = "Alumni"

    return org_code, org_name, entity_type, scope

def detect_status(values: List[str]) -> str:
    for raw in values:
        lowered = raw.lower()
        for key in Config.STATUS_KEYWORDS:
            if key.lower() in lowered:
                return key
    return ""

def detect_year(values: List[str]) -> str:
    years = []
    for raw in values:
        for match in re.finditer(r"\b(18\d{2}|19\d{2}|20\d{2})\b", raw):
            year = int(match.group(1))
            if 1800 <= year <= 2035:
                years.append(year)
    if years:
        return str(min(years))
    return ""

def detect_school(values: List[str]) -> str:
    for raw in values:
        if re.search(r"university|college|institute|school|academy|campus", raw, re.I):
            return raw
    return ""

def detect_chapter(values: List[str]) -> str:
    greek_pattern = r"\b(Alpha|Beta|Gamma|Delta|Epsilon|Zeta|Eta|Theta|Iota|Kappa|Lambda|Mu|Nu|Xi|Omicron|Pi|Rho|Sigma|Tau|Upsilon|Phi|Chi|Psi|Omega)\b"
    for raw in values:
        if re.search(greek_pattern, raw, re.I) and not re.search(r"\d", raw):
            return raw
    for raw in values:
        if not re.search(r"\d", raw) and len(raw) < 36 and not re.search(r"university|college|institute|school", raw, re.I):
            return raw
    return ""

def detect_chapter_id(values: List[str]) -> str:
    for raw in values:
        if re.fullmatch(r"\d{1,5}", raw):
            return raw
    return ""

def detect_notes(values: List[str]) -> str:
    for raw in values:
        if len(raw) > 45 or re.search(r"represents|originally|hosted|county|formerly", raw, re.I):
            return raw
    return ""

def parse_location(raw: str) -> Tuple[str, str]:
    if raw in Config.US_STATES:
        return "", raw
    upper = raw.upper()
    if upper in Config.STATE_ABBR:
        return "", Config.STATE_ABBR[upper]
    if "," in raw:
        parts = [clean_text(p) for p in raw.split(",") if clean_text(p)]
        if len(parts) >= 2:
            state_candidate = parts[-1]
            state = state_candidate if state_candidate in Config.US_STATES else Config.STATE_ABBR.get(state_candidate.upper(), "")
            return ", ".join(parts[:-1]), state
    return "", ""
