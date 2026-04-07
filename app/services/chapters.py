import time
from typing import Dict, List
from urllib.parse import quote

from flask import current_app, has_app_context

from ..cache import get_cache
from ..database import get_connection, ensure_crm_tables, ensure_vendor_table, load_vendor_lookup
from ..utils.text_utils import clean_text, norm_org, norm_state
from ..utils.data_parse import (
    parse_meta_from_file,
    detect_status,
    detect_year,
    detect_school,
    detect_chapter,
    detect_chapter_id,
    detect_notes,
    parse_location,
)

_UNIVERSE_CACHE = {"expires_at": 0.0, "value": None}
_FALLBACK_CACHE_TTL_SECONDS = 60.0
_CHAPTER_UNIVERSE_CACHE_KEY = "chapters:normalized_universe:v2"


def _chapter_cache_ttl() -> float:
    if has_app_context():
        return float(current_app.config.get("CHAPTER_REFERENCE_CACHE_TTL") or _FALLBACK_CACHE_TTL_SECONDS)
    return _FALLBACK_CACHE_TTL_SECONDS


def _copy_rows(rows: List[dict]) -> List[dict]:
    return [dict(row) for row in rows]


def _state_org_key(state_value: str, org_value: str) -> str:
    return f"{clean_text(state_value)}|{clean_text(org_value)}"


def _copy_universe_rows(universe: dict | None) -> List[dict]:
    if not isinstance(universe, dict):
        return []
    return _copy_rows(list(universe.get("rows") or []))


def _get_cached_universe(force_refresh: bool = False) -> dict | None:
    if force_refresh:
        return None

    now = time.monotonic()
    universe = _UNIVERSE_CACHE.get("value")
    if isinstance(universe, dict) and now < float(_UNIVERSE_CACHE.get("expires_at") or 0.0):
        return universe

    if has_app_context():
        cache = get_cache()
        if cache is not None:
            cached = cache.get(_CHAPTER_UNIVERSE_CACHE_KEY)
            if isinstance(cached, dict):
                _UNIVERSE_CACHE["value"] = cached
                _UNIVERSE_CACHE["expires_at"] = now + _chapter_cache_ttl()
                return cached
    return None


def _set_cached_universe(universe: dict) -> None:
    ttl = _chapter_cache_ttl()
    _UNIVERSE_CACHE["value"] = universe
    _UNIVERSE_CACHE["expires_at"] = time.monotonic() + ttl
    if has_app_context():
        cache = get_cache()
        if cache is not None:
            cache.set(_CHAPTER_UNIVERSE_CACHE_KEY, universe, timeout=int(ttl))


def _build_normalized_universe() -> dict:
    conn = get_connection()
    ensure_crm_tables(conn)

    table_name = "chapters"
    table_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if table_info:
        chapter_cols = {row[1] for row in table_info}
        if "chapter_uid" not in chapter_cols:
            table_info = []
    if not table_info:
        table_name = "chapters_raw"
        table_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    columns = [row[1] for row in table_info]
    if not columns:
        return {"rows": [], "by_id": {}, "by_school": {}, "by_state_org": {}}

    ensure_vendor_table(conn)
    instagram_lookup = {}
    chapters_info = conn.execute("PRAGMA table_info(chapters)").fetchall()
    chapter_cols = {row[1] for row in chapters_info}
    if chapters_info and "chapter_uid" in chapter_cols and "instagram" in chapter_cols:
        rows = conn.execute(
            "SELECT chapter_uid, instagram FROM chapters WHERE trim(coalesce(instagram,''))<>''"
        ).fetchall()
        instagram_lookup = {clean_text(r["chapter_uid"]): clean_text(r["instagram"]) for r in rows}

    vendor_exact_lookup, vendor_org_lookup = load_vendor_lookup(conn)
    served_rows = conn.execute("SELECT chapter_id, COUNT(*) AS c FROM vendor_orders GROUP BY chapter_id").fetchall()
    served_lookup = {clean_text(r["chapter_id"]): int(r["c"]) for r in served_rows}
    school_vendor_rows = conn.execute("SELECT school, COUNT(DISTINCT vendor) AS c FROM vendor_orders GROUP BY school").fetchall()
    school_vendor_lookup = {clean_text(r["school"]): int(r["c"]) for r in school_vendor_rows}
    state_vendor_rows = conn.execute("SELECT state_norm, COUNT(DISTINCT vendor) AS c FROM vendors GROUP BY state_norm").fetchall()
    state_vendor_lookup = {clean_text(r["state_norm"]): int(r["c"]) for r in state_vendor_rows}
    org_vendor_rows = conn.execute("SELECT organization_norm, COUNT(DISTINCT vendor) AS c FROM vendors GROUP BY organization_norm").fetchall()
    org_vendor_lookup = {clean_text(r["organization_norm"]): int(r["c"]) for r in org_vendor_rows}

    data_columns = [c for c in columns if c not in {"id"}]
    select_sql = "SELECT " + ", ".join([f'"{c}"' for c in data_columns]) + f" FROM {table_name}"
    chapter_rows = conn.execute(select_sql).fetchall()

    rows_out: List[dict] = []
    for row in chapter_rows:
        source_file = clean_text(row["source_file"]) if "source_file" in row.keys() else ""
        row_number = clean_text(row["row_number"]) if "row_number" in row.keys() else ""
        org_code, org_name, entity_type, scope = parse_meta_from_file(source_file)

        values: List[str] = []
        for column_name in data_columns:
            if column_name in {"source_file", "row_number"}:
                continue
            value = clean_text(row[column_name])
            if value and value not in {"[", "]"} and not value.lower().startswith("http"):
                values.append(value)

        if not values:
            continue

        chapter_name = detect_chapter(values)
        chapter_id = detect_chapter_id(values)
        school = detect_school(values)
        founded_year = detect_year(values)
        status = detect_status(values)
        notes = detect_notes(values)

        city = ""
        state = ""
        for value in values:
            if value in {chapter_name, school}:
                continue
            loc_city, loc_state = parse_location(value)
            city = city or loc_city
            state = state or loc_state

        if not any([chapter_name, school, city, state, status, founded_year]):
            continue

        org_norm = norm_org(org_name)
        state_norm = norm_state(state)
        matches = vendor_exact_lookup.get((org_norm, state_norm), []) if state_norm else []
        if not matches:
            matches = vendor_org_lookup.get(org_norm, [])

        vendor_names = [match["vendor"] for match in matches if match.get("vendor")]
        chapter_uid = f"{source_file}::{row_number}" if source_file or row_number else chapter_id or chapter_name
        instagram = clean_text(row["instagram"]) if "instagram" in row.keys() else instagram_lookup.get(chapter_uid, "")

        rows_out.append(
            {
                "id": chapter_uid,
                "orgCode": org_code,
                "orgName": org_name,
                "entityType": entity_type,
                "scope": scope,
                "chapterId": chapter_id,
                "chapterName": chapter_name,
                "foundedYear": founded_year,
                "initiatedYear": founded_year,
                "school": school,
                "city": city,
                "state": state,
                "status": status,
                "notes": notes,
                "instagram": instagram,
                "sourceFile": source_file,
                "rowNumber": row_number,
                "vendorCount": len(matches),
                "vendorNames": ", ".join(vendor_names[:5]),
                "vendors": matches[:30],
                "isServed": served_lookup.get(chapter_uid, 0) > 0,
                "servedCount": served_lookup.get(chapter_uid, 0),
            }
        )

    school_count: Dict[str, int] = {}
    for row in rows_out:
        school = clean_text(row.get("school"))
        if school:
            school_count[school] = school_count.get(school, 0) + 1

    by_id: dict[str, dict] = {}
    by_school: dict[str, list[str]] = {}
    by_state_org: dict[str, list[str]] = {}
    for row in rows_out:
        org_norm = norm_org(row.get("orgName", ""))
        state_norm = norm_state(row.get("state", ""))
        school = clean_text(row.get("school"))

        vendors_in_org = int(org_vendor_lookup.get(org_norm, 0))
        vendors_in_state = int(state_vendor_lookup.get(state_norm, 0)) if state_norm else 0
        vendors_in_school = int(school_vendor_lookup.get(school, 0)) if school else 0

        score = 0
        if clean_text(row.get("status")).lower() == "active":
            score += 2
        if int(row.get("vendorCount", 0)) >= 2:
            score += 1
        if vendors_in_school > 0:
            score += 3

        row["leadScore"] = score
        row["leadTier"] = "hot" if score >= 5 else ("warm" if score >= 3 else "cold")
        row["vendorsInOrg"] = vendors_in_org
        row["vendorsInState"] = vendors_in_state
        row["vendorsInSchool"] = vendors_in_school
        row["sameCampusChapterCount"] = max(school_count.get(school, 1) - 1, 0) if school else 0

        row_id = clean_text(row.get("id"))
        if not row_id:
            continue
        row_copy = dict(row)
        by_id[row_id] = row_copy
        if school:
            by_school.setdefault(school, []).append(row_id)
        if state_norm and org_norm:
            by_state_org.setdefault(_state_org_key(state_norm, org_norm), []).append(row_id)

    return {
        "rows": [dict(row) for row in rows_out],
        "by_id": by_id,
        "by_school": by_school,
        "by_state_org": by_state_org,
    }


def fetch_normalized_universe(force_refresh: bool = False) -> dict:
    cached = _get_cached_universe(force_refresh=force_refresh)
    if isinstance(cached, dict):
        return cached
    universe = _build_normalized_universe()
    _set_cached_universe(universe)
    return universe


def fetch_normalized_rows(force_refresh: bool = False) -> List[dict]:
    universe = fetch_normalized_universe(force_refresh=force_refresh)
    return _copy_universe_rows(universe)


def get_chapter_by_id(chapter_id: str, rows: List[dict] | None = None) -> dict:
    target = clean_text(chapter_id)
    if not target:
        return {}
    if rows is not None:
        for row in rows:
            if clean_text(row.get("id")) == target:
                return dict(row)
        return {}
    universe = fetch_normalized_universe()
    chapter = (universe.get("by_id") or {}).get(target)
    return dict(chapter) if isinstance(chapter, dict) else {}


def chapter_detail_bundle(chapter_id: str) -> dict:
    target = clean_text(chapter_id)
    if not target:
        return {"chapter": {}, "campus": [], "same_state": []}

    universe = fetch_normalized_universe()
    by_id = universe.get("by_id") or {}
    chapter = by_id.get(target)
    if not isinstance(chapter, dict):
        return {"chapter": {}, "campus": [], "same_state": []}

    chapter_copy = dict(chapter)
    school = clean_text(chapter_copy.get("school"))
    chapter_state = norm_state(chapter_copy.get("state")) or clean_text(chapter_copy.get("state"))
    chapter_org = norm_org(chapter_copy.get("orgName", ""))
    campus_ids = list((universe.get("by_school") or {}).get(school, []))
    related_state_ids = list((universe.get("by_state_org") or {}).get(_state_org_key(chapter_state, chapter_org), []))

    campus = [
        dict(by_id[row_id])
        for row_id in campus_ids
        if row_id != target and row_id in by_id
    ][:15]
    same_state = [
        dict(by_id[row_id])
        for row_id in related_state_ids
        if row_id != target and clean_text(by_id[row_id].get("school")) != school
    ][:15]

    chapter_copy["encodedId"] = quote(clean_text(chapter_copy.get("id")), safe="")
    for row in campus:
        row["encodedId"] = quote(clean_text(row.get("id")), safe="")
    for row in same_state:
        row["encodedId"] = quote(clean_text(row.get("id")), safe="")
    return {"chapter": chapter_copy, "campus": campus, "same_state": same_state}
