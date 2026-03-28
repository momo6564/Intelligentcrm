from __future__ import annotations

import sqlite3

from ..utils.text_utils import clean_text


INSTITUTION_SELECT = """
SELECT id, location_name, parent_name, location_type, address, street, city, state, zip,
       general_phone, admin_name, admin_phone, admin_email, fax, update_date,
       dapip_id, ope_id, ipeds_unit_ids, parent_dapip_id, unitid,
       institution_id, alias, zip_five_digit, fips_state_code, telephone, ein, website,
       institution_level, control, highest_offering, ug_offering, grad_offering,
       degree_granting_status, locale, public_status, post_secondary_status,
       fips_county_code, county, congressional_district, longitude, latitude,
       students_total, dorm_capacity, acceptance_rate
FROM institutions
WHERE id=?
"""


def _fetch_chapters_for_institution(conn: sqlite3.Connection, institution: dict) -> list[dict]:
    chapters = conn.execute(
        """
        SELECT chapter_uid, chapter_name, organization, city, state, status
        FROM chapters
        WHERE institution_id=?
        ORDER BY organization ASC, chapter_name ASC
        LIMIT 250
        """,
        (int(institution["id"]),),
    ).fetchall()
    if not chapters and clean_text(institution.get("location_name")):
        chapters = conn.execute(
            """
            SELECT chapter_uid, chapter_name, organization, city, state, status
            FROM chapters
            WHERE school=?
            ORDER BY organization ASC, chapter_name ASC
            LIMIT 250
            """,
            (clean_text(institution.get("location_name")),),
        ).fetchall()
    return [{k: row[k] for k in row.keys()} for row in chapters]


def fetch_institution_profile(
    conn: sqlite3.Connection,
    institution_id: int,
    workspace_id: str | None = None,
) -> tuple[dict, list[dict], str]:
    row = conn.execute(INSTITUTION_SELECT, (int(institution_id),)).fetchone()
    institution = {k: row[k] for k in row.keys()} if row else {}
    if not institution:
        return {}, [], ""

    chapters = _fetch_chapters_for_institution(conn, institution)
    institution["chapter_count"] = len(chapters)
    institution["active_chapter_count"] = sum(
        1 for chapter in chapters if clean_text(chapter.get("status")).lower() == "active"
    )
    institution["organization_count"] = len(
        {
            clean_text(chapter.get("organization"))
            for chapter in chapters
            if clean_text(chapter.get("organization"))
        }
    )

    my_status = ""
    if workspace_id:
        connection = f"institution:{institution.get('id')}"
        crm_row = conn.execute(
            """
            SELECT status
            FROM crm_contacts
            WHERE workspace_id=? AND type IN ('school', 'other') AND connection=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (workspace_id, connection),
        ).fetchone()
        if crm_row:
            status = clean_text(crm_row["status"]).lower()
            my_status = "served" if status == "closed" else "prospect"

    return institution, chapters, my_status
