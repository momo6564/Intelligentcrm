from flask import Blueprint, request, render_template
from ..auth import login_required, get_session_user
from ..database import get_connection, ensure_crm_tables, ensure_chapters_table, ensure_institutions_table
from ..research_memory import research_placeholder_hints
from ..services.chapters import chapter_detail_bundle
from ..utils.text_utils import clean_text
from ..utils.workspace import workspace_id_for_user

bp = Blueprint('chapters', __name__)

def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)

@bp.route("/chapters")
@login_required()
def chapters_page():
    return render_app("explorer/chapters.html")

@bp.route("/chapters/<path:chapter_id>")
@login_required()
def chapter_detail_page(chapter_id: str):
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    bundle = chapter_detail_bundle(chapter_id)
    if not bundle.get("chapter"):
        return render_app(
            "explorer/chapter_detail.html",
            chapter={},
            campus=[],
            same_state=[],
            my_status="",
            crm_contact={},
            research_url="",
            research_query="",
            error="Chapter not found",
        )

    chapter = bundle["chapter"]
    chapter_id_clean = clean_text(chapter.get("id"))
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_chapters_table(conn)
    ensure_institutions_table(conn)
    crm_row = conn.execute(
        """
        SELECT id, status, notes, follow_up_date, priority, value_estimate, expected_close_date
        FROM crm_contacts
        WHERE workspace_id=? AND type='chapter' AND chapter_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (workspace_id, chapter_id_clean),
    ).fetchone()
    served_row = conn.execute(
        "SELECT 1 FROM vendor_orders WHERE workspace_id=? AND chapter_id=? LIMIT 1",
        (workspace_id, chapter_id_clean),
    ).fetchone()
    crm_status = clean_text(crm_row["status"]).lower() if crm_row else ""
    my_status = "served" if (served_row is not None or crm_status == "closed") else ("prospect" if crm_row else "")
    crm_contact = {
        "id": int(crm_row["id"]) if crm_row else None,
        "status": crm_status if crm_row else "",
        "notes": clean_text(crm_row["notes"]) if crm_row else "",
        "follow_up_date": clean_text(crm_row["follow_up_date"]) if crm_row else "",
        "priority": clean_text(crm_row["priority"]) if crm_row else "normal",
        "value_estimate": crm_row["value_estimate"] if crm_row and crm_row["value_estimate"] is not None else "",
        "expected_close_date": clean_text(crm_row["expected_close_date"]) if crm_row else "",
    }

    related_rows = list(bundle.get("campus") or []) + list(bundle.get("same_state") or [])
    related_ids = [clean_text(r.get("id")) for r in related_rows if clean_text(r.get("id"))]
    crm_map = {}
    served_set = set()
    if related_ids:
        placeholders = ",".join("?" for _ in related_ids)
        crm_rows = conn.execute(
            f"""
            SELECT chapter_id, status
            FROM crm_contacts
            WHERE workspace_id=? AND type='chapter' AND chapter_id IN ({placeholders})
            """,
            (workspace_id, *related_ids),
        ).fetchall()
        for row in crm_rows:
            crm_map[clean_text(row["chapter_id"])] = clean_text(row["status"]).lower()
        served_rows = conn.execute(
            f"""
            SELECT DISTINCT chapter_id
            FROM vendor_orders
            WHERE workspace_id=? AND chapter_id IN ({placeholders})
            """,
            (workspace_id, *related_ids),
        ).fetchall()
        served_set = {clean_text(r["chapter_id"]) for r in served_rows if clean_text(r["chapter_id"])}

    for row in bundle.get("campus", []):
        rid = clean_text(row.get("id"))
        status = crm_map.get(rid, "")
        if rid in served_set or status == "closed":
            row["crm_status"] = "served"
            row["in_crm"] = True
        elif status:
            row["crm_status"] = status
            row["in_crm"] = True
        else:
            row["crm_status"] = ""
            row["in_crm"] = False
    for row in bundle.get("same_state", []):
        rid = clean_text(row.get("id"))
        status = crm_map.get(rid, "")
        if rid in served_set or status == "closed":
            row["crm_status"] = "served"
            row["in_crm"] = True
        elif status:
            row["crm_status"] = status
            row["in_crm"] = True
        else:
            row["crm_status"] = ""
            row["in_crm"] = False

    chapter_name = clean_text(chapter.get("chapterName"))
    org_name = clean_text(chapter.get("orgName"))
    school = clean_text(chapter.get("school"))

    chapter_instagram = ""
    chapter_instagram_url = ""
    chapter_instagram_label = ""
    chapter_website = ""
    president_name = ""
    president_instagram_url = ""
    president_instagram_label = ""

    chapter_row = conn.execute(
        "SELECT instagram, institution_id FROM chapters WHERE chapter_uid=?",
        (chapter_id_clean,),
    ).fetchone()
    if chapter_row:
        chapter_instagram = clean_text(chapter_row["instagram"])
        inst_id = chapter_row["institution_id"]
        if inst_id is not None:
            inst = conn.execute("SELECT website, location_name FROM institutions WHERE id=?", (int(inst_id),)).fetchone()
            if inst:
                chapter_website = clean_text(inst["website"])
        if not chapter_website and school:
            inst = conn.execute("SELECT website FROM institutions WHERE location_name=? LIMIT 1", (school,)).fetchone()
            if inst:
                chapter_website = clean_text(inst["website"])

    if chapter_instagram:
        handle = chapter_instagram.lstrip("@") if not chapter_instagram.lower().startswith("http") else chapter_instagram
        if handle and not handle.lower().startswith("http"):
            chapter_instagram_url = f"https://instagram.com/{handle}"
            chapter_instagram_label = f"@{handle}"
        else:
            chapter_instagram_url = chapter_instagram
            chapter_instagram_label = chapter_instagram

    president_row = conn.execute(
        """
        SELECT contact_name, instagram
        FROM chapter_contacts
        WHERE chapter_id=? AND workspace_id=? AND lower(coalesce(role,'')) LIKE '%president%'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (chapter_id_clean, workspace_id),
    ).fetchone()
    if president_row:
        president_name = clean_text(president_row["contact_name"])
        president_instagram = clean_text(president_row["instagram"])
        if president_instagram:
            handle = president_instagram.lstrip("@") if not president_instagram.lower().startswith("http") else president_instagram
            if handle and not handle.lower().startswith("http"):
                president_instagram_url = f"https://instagram.com/{handle}"
                president_instagram_label = f"@{handle}"
            else:
                president_instagram_url = president_instagram
                president_instagram_label = president_instagram

    return render_app(
        "explorer/chapter_detail.html",
        chapter=chapter,
        campus=bundle["campus"],
        same_state=bundle["same_state"],
        my_status=my_status,
        crm_contact=crm_contact,
        research_url="",
        research_query="",
        research_entity_data={
            "chapter_name": chapter_name,
            "organization": org_name,
            "school": school,
            "city": clean_text(chapter.get("city")),
            "state": clean_text(chapter.get("state")),
        },
        research_placeholder_hints=research_placeholder_hints("chapter"),
        chapter_instagram_url=chapter_instagram_url,
        chapter_instagram_label=chapter_instagram_label,
        chapter_website=chapter_website,
        president_name=president_name,
        president_instagram_url=president_instagram_url,
        president_instagram_label=president_instagram_label,
        error="",
    )

@bp.route("/ui/chapter-drawer")
@login_required()
def chapter_drawer_partial():
    chapter_id = clean_text(request.args.get("chapter_id"))
    bundle = chapter_detail_bundle(chapter_id)
    return render_template(
        "components/chapter_drawer.html",
        chapter=bundle.get("chapter", {}),
        campus=bundle.get("campus", []),
        same_state=bundle.get("same_state", []),
        me=get_session_user(),
    )
