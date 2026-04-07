import re
from urllib.parse import quote_plus

from .utils.text_utils import clean_text

MAX_RESEARCH_PROMPTS = 5

RESEARCH_PROMPT_DEFAULTS = {
    "chapter": [
        {
            "label": "Profiles",
            "prompt_text": 'Find the official website and Instagram for "{chapter_name}" of "{organization}" at "{school}". Include direct profile links.',
        },
        {
            "label": "Leadership",
            "prompt_text": 'Find the current president or chapter leadership for "{chapter_name}" of "{organization}" at "{school}" and include any public Instagram or LinkedIn profile links.',
        },
        {
            "label": "Campus Fit",
            "prompt_text": 'Research "{school}" in "{city}, {state}" and summarize chapter culture, campus size, and any notes relevant to selling to "{chapter_name}" of "{organization}".',
        },
    ],
    "vendor": [
        {
            "label": "Owner",
            "prompt_text": 'Who owns "{vendor_name}" in "{city}, {state}"? Find the owner name, official website, and public social profiles.',
        },
        {
            "label": "Licenses",
            "prompt_text": 'Research "{vendor_name}" and verify which Greek or collegiate organizations they appear licensed for. Current notes: {licensed_organizations}.',
        },
        {
            "label": "Credibility",
            "prompt_text": 'Find reviews, case studies, and recent Instagram activity for "{vendor_name}" in category "{category}".',
        },
    ],
    "institution": [
        {
            "label": "Procurement",
            "prompt_text": 'Find procurement, approved vendor, purchasing, and licensing pages for "{institution_name}" in "{city}, {state}". Include direct .edu links when possible.',
        },
        {
            "label": "Student Life",
            "prompt_text": 'Research "{institution_name}" student life, Greek life presence, enrollment ({students_total}), and any campus notes relevant to vendor outreach.',
        },
        {
            "label": "Leadership",
            "prompt_text": 'Find the purchasing, student affairs, or campus engagement decision makers for "{institution_name}" and list public profiles or contact pages.',
        },
    ],
}

RESEARCH_PLACEHOLDER_HINTS = {
    "chapter": [
        "{chapter_name}",
        "{organization}",
        "{school}",
        "{city}",
        "{state}",
    ],
    "vendor": [
        "{vendor_name}",
        "{organization}",
        "{category}",
        "{city}",
        "{state}",
        "{licensed_organizations}",
    ],
    "institution": [
        "{institution_name}",
        "{city}",
        "{state}",
        "{website}",
        "{control}",
        "{institution_level}",
        "{students_total}",
    ],
}


def normalize_research_category(value: str) -> str:
    category = clean_text(value).lower()
    return category if category in RESEARCH_PROMPT_DEFAULTS else ""


def research_placeholder_hints(category: str) -> list[str]:
    return list(RESEARCH_PLACEHOLDER_HINTS.get(normalize_research_category(category), []))


def _defaults_for_category(category: str) -> list[dict]:
    normalized = normalize_research_category(category)
    return [dict(item) for item in RESEARCH_PROMPT_DEFAULTS.get(normalized, [])]


def _blank_slots() -> list[dict]:
    return [{"slot_index": idx, "label": "", "prompt_text": ""} for idx in range(1, MAX_RESEARCH_PROMPTS + 1)]


def _default_slots_for_category(category: str) -> list[dict]:
    slots = _blank_slots()
    for index, item in enumerate(_defaults_for_category(category), start=1):
        if index > MAX_RESEARCH_PROMPTS:
            break
        slots[index - 1] = {
            "slot_index": index,
            "label": clean_text(item.get("label")),
            "prompt_text": clean_text(item.get("prompt_text")),
        }
    return slots


def _normalized_workspace_id(user: dict, workspace_id: str = "") -> str:
    explicit = clean_text(workspace_id)
    if explicit:
        return explicit
    return clean_text((user or {}).get("workspace_id"))


def research_prompt_slots_for_user(conn, user: dict, category: str, workspace_id: str = "") -> list[dict]:
    normalized = normalize_research_category(category)
    user_id = int((user or {}).get("id") or 0)
    workspace = _normalized_workspace_id(user, workspace_id)
    if not normalized or user_id <= 0:
        return _default_slots_for_category(normalized) if normalized else _blank_slots()
    rows = conn.execute(
        """
        SELECT slot_index, label, prompt_text
        FROM user_research_prompts
        WHERE user_id=? AND workspace_id=? AND category=?
        ORDER BY slot_index ASC
        """,
        (user_id, workspace, normalized),
    ).fetchall()
    slots = _default_slots_for_category(normalized)
    for row in rows:
        slot_index = int(row["slot_index"] or 0)
        if 1 <= slot_index <= MAX_RESEARCH_PROMPTS:
            slots[slot_index - 1] = {
                "slot_index": slot_index,
                "label": clean_text(row["label"]),
                "prompt_text": clean_text(row["prompt_text"]),
            }
    return slots


def save_research_prompt_slots(conn, user: dict, category: str, prompts: list[dict], workspace_id: str = "") -> list[dict]:
    normalized = normalize_research_category(category)
    user_id = int((user or {}).get("id") or 0)
    workspace = _normalized_workspace_id(user, workspace_id)
    if not normalized:
        raise ValueError("Unsupported category")
    if user_id <= 0:
        raise ValueError("User is required")
    cleaned_prompts = list(prompts or [])[:MAX_RESEARCH_PROMPTS]
    conn.execute(
        "DELETE FROM user_research_prompts WHERE user_id=? AND workspace_id=? AND category=?",
        (user_id, workspace, normalized),
    )
    for index in range(1, MAX_RESEARCH_PROMPTS + 1):
        item = cleaned_prompts[index - 1] if index - 1 < len(cleaned_prompts) else {}
        label = clean_text((item or {}).get("label")) or f"Research {index}"
        prompt_text = clean_text((item or {}).get("prompt_text"))
        if not prompt_text:
            continue
        conn.execute(
            """
            INSERT INTO user_research_prompts(user_id, workspace_id, category, slot_index, label, prompt_text)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (user_id, workspace, normalized, index, label[:80], prompt_text[:1200]),
        )
    conn.commit()
    return research_prompt_slots_for_user(conn, user, normalized, workspace_id=workspace)


def reset_research_prompt_slots(conn, user: dict, category: str, workspace_id: str = "") -> list[dict]:
    normalized = normalize_research_category(category)
    user_id = int((user or {}).get("id") or 0)
    workspace = _normalized_workspace_id(user, workspace_id)
    if normalized and user_id > 0:
        conn.execute(
            "DELETE FROM user_research_prompts WHERE user_id=? AND workspace_id=? AND category=?",
            (user_id, workspace, normalized),
        )
        conn.commit()
    return research_prompt_slots_for_user(conn, user, normalized, workspace_id=workspace)


_TOKEN_RE = re.compile(r"\{([a-z0-9_]+)\}", re.I)
_FRIENDLY_TOKEN_RE = re.compile(r"<<\s*([^>]+?)\s*>>", re.I)
_TOKEN_ALIASES = {
    "university": "institution_name",
    "university_name": "institution_name",
    "college": "institution_name",
    "college_name": "institution_name",
    "institution": "institution_name",
    "vendor": "vendor_name",
    "company": "vendor_name",
    "company_name": "vendor_name",
    "chapter": "chapter_name",
    "organization_name": "organization",
    "org": "organization",
    "org_name": "organization",
    "school_name": "school",
}


def _normalize_prompt_token(token: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", clean_text(token).lower()).strip("_")
    return _TOKEN_ALIASES.get(normalized, normalized)


def render_research_prompt(prompt_text: str, entity_context: dict) -> str:
    context = {}
    for key, value in (entity_context or {}).items():
        context[_normalize_prompt_token(key)] = clean_text(value)

    def replace_token(match):
        token = _normalize_prompt_token(match.group(1))
        return context.get(token, "")

    rendered = _TOKEN_RE.sub(replace_token, clean_text(prompt_text))
    rendered = _FRIENDLY_TOKEN_RE.sub(replace_token, rendered)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered


def build_research_prompt_links(slots: list[dict], entity_context: dict) -> list[dict]:
    links = []
    for item in list(slots or [])[:MAX_RESEARCH_PROMPTS]:
        prompt_text = clean_text((item or {}).get("prompt_text"))
        rendered_query = render_research_prompt(prompt_text, entity_context) if prompt_text else ""
        links.append(
            {
                "slot_index": int((item or {}).get("slot_index") or (len(links) + 1)),
                "label": clean_text((item or {}).get("label")) or f"Research {len(links) + 1}",
                "prompt_text": prompt_text,
                "rendered_query": rendered_query,
                "url": f"https://www.google.com/search?q={quote_plus(rendered_query)}" if rendered_query else "",
            }
        )
    return links
