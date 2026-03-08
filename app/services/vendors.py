import sqlite3
from typing import List
from urllib.parse import quote

from ..database import get_connection, ensure_crm_tables
from ..utils.text_utils import clean_text
from .chapters import fetch_normalized_rows

def vendor_order_rows(conn: sqlite3.Connection, vendor: str, workspace_id: str = "") -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, vendor, chapter_id, org, chapter_name, school, city, state, year, order_type, product, quantity, notes, created_at
        FROM vendor_orders
        WHERE lower(vendor) = lower(?) AND workspace_id = ?
        ORDER BY created_at DESC
        """,
        (vendor, clean_text(workspace_id)),
    ).fetchall()

def build_vendor_hot_leads(vendor: str, limit: int = 100, workspace_id: str = "") -> List[dict]:
    if not vendor:
        return []
    conn = get_connection()
    ensure_crm_tables(conn)
    served = vendor_order_rows(conn, vendor, workspace_id=workspace_id)
    if not served:
        return []

    served_schools = {clean_text(r["school"]) for r in served if clean_text(r["school"])}
    served_orgs = {clean_text(r["org"]) for r in served if clean_text(r["org"])}
    served_chapter_ids = {clean_text(r["chapter_id"]) for r in served if clean_text(r["chapter_id"])}

    candidates: List[dict] = []
    for row in fetch_normalized_rows():
        school = clean_text(row.get("school"))
        chapter_id = clean_text(row.get("id"))
        if school not in served_schools:
            continue
        if chapter_id in served_chapter_ids:
            continue

        score = 0
        reasons: List[str] = []
        score += 2
        reasons.append("same campus")
        if clean_text(row.get("orgName")) in served_orgs:
            score += 1
            reasons.append("same org")
        if clean_text(row.get("status")).lower() == "active":
            score += 1
            reasons.append("active chapter")

        emoji = "🔥" if score >= 4 else ("⭐" if score >= 3 else "⚡")
        candidates.append(
            {
                "id": chapter_id,
                "encodedId": quote(chapter_id, safe=""),
                "org": clean_text(row.get("orgName")),
                "chapter": clean_text(row.get("chapterName")),
                "school": school,
                "city": clean_text(row.get("city")),
                "state": clean_text(row.get("state")),
                "status": clean_text(row.get("status")),
                "leadScore": score,
                "leadBadge": f"{emoji} {' / '.join(reasons)}",
            }
        )

    candidates.sort(key=lambda x: (-int(x["leadScore"]), x["school"], x["org"], x["chapter"]))
    return candidates[: max(1, min(int(limit), 300))]


def vendor_competitors(conn: sqlite3.Connection, vendor: str) -> List[dict]:
    served_org_rows = conn.execute(
        "SELECT DISTINCT org FROM vendor_orders WHERE lower(vendor)=lower(?) AND trim(coalesce(org,''))<>''",
        (vendor,),
    ).fetchall()
    served_orgs = [clean_text(r["org"]) for r in served_org_rows if clean_text(r["org"])]
    if not served_orgs:
        return []

    placeholders = ",".join("?" for _ in served_orgs)
    rows = conn.execute(
        f"""
        SELECT vendor, COUNT(DISTINCT chapter_id) AS chapter_count, GROUP_CONCAT(DISTINCT org) AS orgs
        FROM vendor_orders
        WHERE org IN ({placeholders}) AND lower(vendor) <> lower(?)
        GROUP BY vendor
        ORDER BY chapter_count DESC, vendor ASC
        """,
        tuple(served_orgs + [vendor]),
    ).fetchall()
    followed_rows = conn.execute(
        "SELECT competitor_vendor, is_starred FROM competitors_followed WHERE lower(vendor)=lower(?)",
        (vendor,),
    ).fetchall()
    followed = {clean_text(r["competitor_vendor"]).lower(): int(r["is_starred"] or 0) for r in followed_rows}

    out = []
    for r in rows:
        name = clean_text(r["vendor"])
        out.append(
            {
                "vendor": name,
                "chapterCount": int(r["chapter_count"] or 0),
                "orgs": clean_text(r["orgs"]),
                "starred": bool(followed.get(name.lower(), 0)),
            }
        )
    return out
