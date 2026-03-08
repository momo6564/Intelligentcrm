from datetime import datetime
from typing import List, Dict
from urllib.parse import quote

from ..database import get_connection, ensure_crm_tables, ensure_vendor_table
from ..utils.text_utils import clean_text, join_location
from ..utils.workspace import workspace_id_for_user
from .chapters import fetch_normalized_rows

def manufacturer_dashboard_dataset(user: dict, activity_limit: int = 25) -> dict:
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_vendor_table(conn)
    workspace_id = workspace_id_for_user(user)
    contact_rows = conn.execute(
        """
        SELECT id, type, name, chapter_id, vendor_id, connection, status, notes, created_at
        FROM crm_contacts
        WHERE workspace_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (workspace_id,),
    ).fetchall()
    order_rows = conn.execute(
        """
        SELECT id, vendor, org, chapter_id, chapter_name, school, city, state, order_type, notes, created_at
        FROM vendor_orders
        WHERE workspace_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (workspace_id,),
    ).fetchall()
    vendor_rows = conn.execute(
        """
        SELECT id, vendor, organization, category, state, city, website, email
        FROM vendors
        ORDER BY id ASC
        """
    ).fetchall()
    activities = []
    if int(activity_limit or 0) > 0:
        activities = conn.execute(
            """
            SELECT action, entity_type, entity_id, details, created_at
            FROM activities
            WHERE workspace_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (workspace_id, int(activity_limit)),
        ).fetchall()
    chapters = fetch_normalized_rows()
    chapter_by_id: Dict[str, dict] = {clean_text(r.get("id")): r for r in chapters if clean_text(r.get("id"))}
    vendor_lookup_by_name: Dict[str, List[dict]] = {}
    for row in vendor_rows:
        item = {k: row[k] for k in row.keys()}
        name = clean_text(item.get("vendor"))
        if not name:
            continue
        vendor_lookup_by_name.setdefault(name.lower(), []).append(item)

    existing_chapter_ids: set[str] = set()
    served_chapter_ids: set[str] = set()
    served_schools: set[str] = set()
    served_orgs: set[str] = set()
    existing_vendor_names: set[str] = set()

    chapter_closed_contacts: List[dict] = []
    vendor_closed_contacts: List[dict] = []
    for row in contact_rows:
        item = {k: row[k] for k in row.keys()}
        item_type = clean_text(item.get("type")).lower()
        status = clean_text(item.get("status")).lower()
        if item_type == "chapter":
            chapter_id = clean_text(item.get("chapter_id"))
            if chapter_id:
                existing_chapter_ids.add(chapter_id)
            if status == "closed":
                chapter_closed_contacts.append(item)
                if chapter_id:
                    served_chapter_ids.add(chapter_id)
                org = clean_text(item.get("connection"))
                if org:
                    served_orgs.add(org)
        elif item_type == "vendor":
            name = clean_text(item.get("name"))
            if name:
                existing_vendor_names.add(name.lower())
            if status == "closed":
                vendor_closed_contacts.append(item)

    order_items = [{k: row[k] for k in row.keys()} for row in order_rows]
    for item in order_items:
        chapter_id = clean_text(item.get("chapter_id"))
        if chapter_id:
            served_chapter_ids.add(chapter_id)
            existing_chapter_ids.add(chapter_id)
        vendor_name = clean_text(item.get("vendor"))
        if vendor_name:
            existing_vendor_names.add(vendor_name.lower())
        school = clean_text(item.get("school"))
        if school:
            served_schools.add(school)
        org = clean_text(item.get("org"))
        if org:
            served_orgs.add(org)

    for chapter_id in served_chapter_ids:
        chapter = chapter_by_id.get(chapter_id)
        if not chapter:
            continue
        school = clean_text(chapter.get("school"))
        if school:
            served_schools.add(school)
        org = clean_text(chapter.get("orgName"))
        if org:
            served_orgs.add(org)

    hot_chapters: List[dict] = []
    if served_schools:
        for chapter in chapters:
            chapter_id = clean_text(chapter.get("id"))
            school = clean_text(chapter.get("school"))
            if not school or school not in served_schools:
                continue
            if chapter_id in served_chapter_ids or chapter_id in existing_chapter_ids:
                continue
            org = clean_text(chapter.get("orgName"))
            status = clean_text(chapter.get("status"))
            reasons = ["same campus"]
            score = 2
            if org and org in served_orgs:
                score += 1
                reasons.append("same org")
            if status.lower() == "active":
                score += 1
                reasons.append("active chapter")
            emoji = "🔥" if score >= 4 else ("⭐" if score >= 3 else "⚡")
            hot_chapters.append(
                {
                    "id": chapter_id,
                    "encodedId": quote(chapter_id, safe=""),
                    "name": clean_text(chapter.get("chapterName")),
                    "org": org,
                    "school": school,
                    "city": clean_text(chapter.get("city")),
                    "state": clean_text(chapter.get("state")),
                    "status": status,
                    "location": join_location(chapter.get("city"), chapter.get("state")),
                    "leadScore": score,
                    "leadBadge": f"{emoji} {' / '.join(reasons)}",
                }
            )
    hot_chapters.sort(key=lambda r: (-int(r.get("leadScore") or 0), r.get("school", ""), r.get("org", ""), r.get("name", "")))

    vendor_candidates: Dict[str, dict] = {}
    if served_schools:
        for chapter in chapters:
            school = clean_text(chapter.get("school"))
            if not school or school not in served_schools:
                continue
            chapter_id = clean_text(chapter.get("id"))
            chapter_org = clean_text(chapter.get("orgName"))
            chapter_state = clean_text(chapter.get("state"))
            chapter_city = clean_text(chapter.get("city"))
            chapter_status = clean_text(chapter.get("status")).lower()
            for match in chapter.get("vendors", []) or []:
                vendor_name = clean_text(match.get("vendor"))
                if not vendor_name:
                    continue
                key = vendor_name.lower()
                if key in existing_vendor_names:
                    continue
                bucket = vendor_candidates.setdefault(
                    key,
                    {
                        "vendor_id": None,
                        "name": vendor_name,
                        "license": clean_text(match.get("organization")) or chapter_org,
                        "products": clean_text(match.get("category")),
                        "state": clean_text(match.get("state")) or chapter_state,
                        "city": clean_text(match.get("city")) or chapter_city,
                        "website": clean_text(match.get("website")),
                        "email": clean_text(match.get("email")),
                        "schools": set(),
                        "chapter_ids": set(),
                        "same_org_matches": 0,
                        "active_matches": 0,
                    },
                )
                bucket["schools"].add(school)
                if chapter_id:
                    bucket["chapter_ids"].add(chapter_id)
                if chapter_status == "active":
                    bucket["active_matches"] += 1
                if chapter_org and chapter_org in served_orgs:
                    bucket["same_org_matches"] += 1

    hot_vendors: List[dict] = []
    for key, candidate in vendor_candidates.items():
        meta = vendor_lookup_by_name.get(key, [])
        if meta:
            best = meta[0]
            candidate["vendor_id"] = int(best["id"]) if best.get("id") is not None else None
            candidate["license"] = candidate["license"] or clean_text(best.get("organization"))
            candidate["products"] = candidate["products"] or clean_text(best.get("category"))
            candidate["state"] = candidate["state"] or clean_text(best.get("state"))
            candidate["city"] = candidate["city"] or clean_text(best.get("city"))
            candidate["website"] = candidate["website"] or clean_text(best.get("website"))
            candidate["email"] = candidate["email"] or clean_text(best.get("email"))

        reasons = ["same campus"]
        score = 2
        if candidate["same_org_matches"] > 0 or clean_text(candidate.get("license")) in served_orgs:
            score += 1
            reasons.append("same org")
        if int(candidate.get("active_matches") or 0) > 0:
            score += 1
            reasons.append("active chapter overlap")
        emoji = "🔥" if score >= 4 else ("⭐" if score >= 3 else "⚡")
        hot_vendors.append(
            {
                "vendor_id": candidate.get("vendor_id"),
                "name": clean_text(candidate.get("name")),
                "license": clean_text(candidate.get("license")),
                "products": clean_text(candidate.get("products")),
                "state": clean_text(candidate.get("state")),
                "city": clean_text(candidate.get("city")),
                "website": clean_text(candidate.get("website")),
                "email": clean_text(candidate.get("email")),
                "schoolCount": len(candidate.get("schools", set())),
                "chapterMatches": len(candidate.get("chapter_ids", set())),
                "leadScore": score,
                "leadBadge": f"{emoji} {' / '.join(reasons)}",
            }
        )
    hot_vendors.sort(key=lambda r: (-int(r.get("leadScore") or 0), -int(r.get("chapterMatches") or 0), r.get("name", "")))

    served_chapter_map: Dict[str, dict] = {}
    for item in chapter_closed_contacts:
        chapter_id = clean_text(item.get("chapter_id"))
        chapter = chapter_by_id.get(chapter_id, {})
        name = clean_text(item.get("name")) or clean_text(chapter.get("chapterName"))
        if not chapter_id and not name:
            continue
        key = chapter_id or f"name::{name.lower()}"
        rec = {
            "chapter_id": chapter_id,
            "name": name,
            "org": clean_text(item.get("connection")) or clean_text(chapter.get("orgName")),
            "school": clean_text(chapter.get("school")),
            "city": clean_text(chapter.get("city")),
            "state": clean_text(chapter.get("state")),
            "added_at": clean_text(item.get("created_at")),
        }
        prev = served_chapter_map.get(key)
        if not prev or rec["added_at"] >= clean_text(prev.get("added_at")):
            served_chapter_map[key] = rec

    for item in order_items:
        chapter_id = clean_text(item.get("chapter_id"))
        chapter_name = clean_text(item.get("chapter_name"))
        if not chapter_id and not chapter_name:
            continue
        chapter = chapter_by_id.get(chapter_id, {})
        key = chapter_id or f"name::{chapter_name.lower()}"
        rec = {
            "chapter_id": chapter_id,
            "name": chapter_name or clean_text(chapter.get("chapterName")),
            "org": clean_text(item.get("org")) or clean_text(chapter.get("orgName")),
            "school": clean_text(item.get("school")) or clean_text(chapter.get("school")),
            "city": clean_text(item.get("city")) or clean_text(chapter.get("city")),
            "state": clean_text(item.get("state")) or clean_text(chapter.get("state")),
            "added_at": clean_text(item.get("created_at")),
        }
        prev = served_chapter_map.get(key)
        if not prev or rec["added_at"] >= clean_text(prev.get("added_at")):
            served_chapter_map[key] = rec

    chapters_served = sorted(
        [
            {
                **rec,
                "location": join_location(rec.get("city"), rec.get("state")),
                "encodedId": quote(clean_text(rec.get("chapter_id")), safe="") if clean_text(rec.get("chapter_id")) else "",
            }
            for rec in served_chapter_map.values()
        ],
        key=lambda r: (clean_text(r.get("added_at")), clean_text(r.get("name"))),
        reverse=True,
    )

    served_vendor_map: Dict[str, dict] = {}
    for item in vendor_closed_contacts:
        name = clean_text(item.get("name"))
        if not name:
            continue
        key = name.lower()
        meta = vendor_lookup_by_name.get(key, [])
        best = meta[0] if meta else {}
        rec = {
            "vendor_id": int(best.get("id")) if best.get("id") is not None else None,
            "name": name,
            "org": clean_text(item.get("connection")) or clean_text(best.get("organization")),
            "products": clean_text(best.get("category")),
            "state": clean_text(best.get("state")),
            "city": clean_text(best.get("city")),
            "website": clean_text(best.get("website")),
            "email": clean_text(best.get("email")),
            "added_at": clean_text(item.get("created_at")),
        }
        prev = served_vendor_map.get(key)
        if not prev or rec["added_at"] >= clean_text(prev.get("added_at")):
            served_vendor_map[key] = rec

    for item in order_items:
        name = clean_text(item.get("vendor"))
        if not name:
            continue
        key = name.lower()
        meta = vendor_lookup_by_name.get(key, [])
        best = meta[0] if meta else {}
        rec = {
            "vendor_id": int(best.get("id")) if best.get("id") is not None else None,
            "name": name,
            "org": clean_text(item.get("org")) or clean_text(best.get("organization")),
            "products": clean_text(best.get("category")),
            "state": clean_text(item.get("state")) or clean_text(best.get("state")),
            "city": clean_text(item.get("city")) or clean_text(best.get("city")),
            "website": clean_text(best.get("website")),
            "email": clean_text(best.get("email")),
            "added_at": clean_text(item.get("created_at")),
        }
        prev = served_vendor_map.get(key)
        if not prev or rec["added_at"] >= clean_text(prev.get("added_at")):
            served_vendor_map[key] = rec

    vendors_served = sorted(
        [
            {
                **rec,
                "location": join_location(rec.get("city"), rec.get("state")),
            }
            for rec in served_vendor_map.values()
        ],
        key=lambda r: (clean_text(r.get("added_at")), clean_text(r.get("name"))),
        reverse=True,
    )

    return {
        "hot_chapters": hot_chapters,
        "hot_vendors": hot_vendors,
        "chapters_served": chapters_served,
        "vendors_served": vendors_served,
        "activities": [{k: row[k] for k in row.keys()} for row in activities],
    }


def manufacturer_dashboard_snapshot(user: dict, hot_limit: int = 12, activity_limit: int = 25) -> dict:
    dataset = manufacturer_dashboard_dataset(user, activity_limit=max(0, min(int(activity_limit), 200)))
    safe_hot_limit = max(1, min(int(hot_limit), 200))
    return {
        "metrics": {
            "hot_chapters": len(dataset["hot_chapters"]),
            "hot_vendors": len(dataset["hot_vendors"]),
            "chapters_served": len(dataset["chapters_served"]),
            "vendors_served": len(dataset["vendors_served"]),
        },
        "hot_chapters": dataset["hot_chapters"][:safe_hot_limit],
        "hot_vendors": dataset["hot_vendors"][:safe_hot_limit],
        "activities": dataset["activities"],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

def manufacturer_hot_chapters(user: dict, limit: int = 25) -> List[dict]:
    dataset = manufacturer_dashboard_dataset(user, activity_limit=0)
    return dataset["hot_chapters"][: max(1, min(int(limit), 200))]

def manufacturer_hot_vendors(user: dict, limit: int = 25) -> List[dict]:
    dataset = manufacturer_dashboard_dataset(user, activity_limit=0)
    return dataset["hot_vendors"][: max(1, min(int(limit), 200))]
