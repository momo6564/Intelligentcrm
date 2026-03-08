import sqlite3
from typing import List, Dict
from urllib.parse import quote
import time

from ..database import get_connection, ensure_crm_tables, ensure_vendor_table, load_vendor_lookup
from ..utils.text_utils import clean_text, norm_org, norm_state
from ..utils.data_parse import (
    parse_meta_from_file, detect_status, detect_year,
    detect_school, detect_chapter, detect_chapter_id,
    detect_notes, parse_location
)

_ROWS_CACHE = {"expires_at": 0.0, "rows": []}
_CACHE_TTL_SECONDS = 5.0

def fetch_normalized_rows(force_refresh: bool = False) -> List[dict]:
    now = time.monotonic()
    if not force_refresh and _ROWS_CACHE["rows"] and now < float(_ROWS_CACHE["expires_at"]):
        # Return detached dicts so callers can safely enrich per-request fields.
        return [dict(r) for r in _ROWS_CACHE["rows"]]

    conn = get_connection()
    ensure_crm_tables(conn)

    table_info = conn.execute("PRAGMA table_info(chapters)").fetchall()
    columns = [row[1] for row in table_info]
    if not columns:
        return []

    ensure_vendor_table(conn)
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
    select_sql = "SELECT " + ", ".join([f'"{c}"' for c in data_columns]) + " FROM chapters"
    chapter_rows = conn.execute(select_sql).fetchall()
    out: List[dict] = []
    for r in chapter_rows:
        source_file = clean_text(r["source_file"]) if "source_file" in r.keys() else ""
        row_number = clean_text(r["row_number"]) if "row_number" in r.keys() else ""
        org_code, org_name, entity_type, scope = parse_meta_from_file(source_file)

        values: List[str] = []
        for c in data_columns:
            if c in {"source_file", "row_number"}:
                continue
            v = clean_text(r[c])
            if v and v not in {"[", "]"} and not v.lower().startswith("http"):
                values.append(v)

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
        for v in values:
            if v in {chapter_name, school}:
                continue
            loc_city, loc_state = parse_location(v)
            city = city or loc_city
            state = state or loc_state

        if not any([chapter_name, school, city, state, status, founded_year]):
            continue

        org_norm = norm_org(org_name)
        st_norm = norm_state(state)

        matches = vendor_exact_lookup.get((org_norm, st_norm), []) if st_norm else []
        if not matches:
            matches = vendor_org_lookup.get(org_norm, [])

        vendor_names = [m["vendor"] for m in matches if m.get("vendor")]

        out.append(
            {
                "id": f"{source_file}::{row_number}",
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
                "sourceFile": source_file,
                "rowNumber": row_number,
                "vendorCount": len(matches),
                "vendorNames": ", ".join(vendor_names[:5]),
                "vendors": matches[:30],
                "isServed": served_lookup.get(f"{source_file}::{row_number}", 0) > 0,
                "servedCount": served_lookup.get(f"{source_file}::{row_number}", 0),
            }
        )

    school_count: Dict[str, int] = {}
    for r in out:
        school = clean_text(r.get("school"))
        if school:
            school_count[school] = school_count.get(school, 0) + 1

    for r in out:
        org_norm = norm_org(r.get("orgName", ""))
        state_norm = norm_state(r.get("state", ""))
        school = clean_text(r.get("school"))

        vendors_in_org = int(org_vendor_lookup.get(org_norm, 0))
        vendors_in_state = int(state_vendor_lookup.get(state_norm, 0)) if state_norm else 0
        vendors_in_school = int(school_vendor_lookup.get(school, 0)) if school else 0

        score = 0
        if clean_text(r.get("status")).lower() == "active":
            score += 2
        if int(r.get("vendorCount", 0)) >= 2:
            score += 1
        if vendors_in_school > 0:
            score += 3

        lead_tier = "hot" if score >= 5 else ("warm" if score >= 3 else "cold")

        r["leadScore"] = score
        r["leadTier"] = lead_tier
        r["vendorsInOrg"] = vendors_in_org
        r["vendorsInState"] = vendors_in_state
        r["vendorsInSchool"] = vendors_in_school
        r["sameCampusChapterCount"] = max(school_count.get(school, 1) - 1, 0) if school else 0

    _ROWS_CACHE["rows"] = [dict(r) for r in out]
    _ROWS_CACHE["expires_at"] = time.monotonic() + _CACHE_TTL_SECONDS
    return [dict(r) for r in out]

def get_chapter_by_id(chapter_id: str, rows: List[dict] | None = None) -> dict:
    target = clean_text(chapter_id)
    if not target:
        return {}
    source_rows = rows if rows is not None else fetch_normalized_rows()
    for row in source_rows:
        if clean_text(row.get("id")) == target:
            return dict(row)
    return {}

def chapter_detail_bundle(chapter_id: str) -> dict:
    all_rows = fetch_normalized_rows()
    chapter = get_chapter_by_id(chapter_id, rows=all_rows)
    if not chapter:
        return {"chapter": {}, "campus": [], "same_state": []}
    campus = [
        dict(r)
        for r in all_rows
        if clean_text(r.get("school")) and clean_text(r.get("school")) == clean_text(chapter.get("school")) and clean_text(r.get("id")) != clean_text(chapter.get("id"))
    ][:15]
    same_state = [
        dict(r)
        for r in all_rows
        if clean_text(r.get("state")) and clean_text(r.get("state")) == clean_text(chapter.get("state")) and clean_text(r.get("id")) != clean_text(chapter.get("id"))
    ][:15]
    chapter["encodedId"] = quote(clean_text(chapter.get("id")), safe="")
    for row in campus:
        row["encodedId"] = quote(clean_text(row.get("id")), safe="")
    for row in same_state:
        row["encodedId"] = quote(clean_text(row.get("id")), safe="")
    return {"chapter": chapter, "campus": campus, "same_state": same_state}
