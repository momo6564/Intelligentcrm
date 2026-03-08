import os
from datetime import date
from urllib.parse import quote_plus
from flask import Blueprint, render_template, redirect, url_for
from ..auth import login_required, get_session_user
from ..config import Config
from ..database import get_connection, ensure_crm_tables
from ..services.dashboard import manufacturer_dashboard_dataset
from ..utils.text_utils import clean_text
from ..utils.workspace import workspace_id_for_user

bp = Blueprint('main', __name__)

def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)

@bp.route("/")
@login_required()
def index():
    return dashboard_page()

@bp.route("/dashboard")
@login_required()
def dashboard_page():
    if not os.path.exists(Config.DB_PATH):
        return render_app(
            "dashboards/dashboard.html",
            metrics={},
            schools_served=[],
            orgs_served=[],
            chapters_served=[],
            vendors_served=[],
            error="Run import_csv.py first",
        )
    
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    dataset = manufacturer_dashboard_dataset(user, activity_limit=0)
    chapters_served = dataset.get("chapters_served", [])
    vendors_served = dataset.get("vendors_served", [])

    school_map = {}
    for row in chapters_served:
        school = clean_text(row.get("school"))
        if not school:
            continue
        bucket = school_map.setdefault(
            school,
            {"school": school, "chapters_count": 0, "orgs": set(), "states": set()},
        )
        bucket["chapters_count"] += 1
        org = clean_text(row.get("org"))
        if org:
            bucket["orgs"].add(org)
        state = clean_text(row.get("state"))
        if state:
            bucket["states"].add(state)
    schools_served = sorted(
        [
            {
                "school": item["school"],
                "chapters_count": int(item["chapters_count"]),
                "org_count": len(item["orgs"]),
                "states": ", ".join(sorted(item["states"])),
            }
            for item in school_map.values()
        ],
        key=lambda r: (-int(r.get("chapters_count") or 0), r.get("school", "")),
    )

    org_type_lookup = {clean_text(name).lower(): clean_text(kind) for name, kind in Config.ORG_MAP.values()}
    org_map = {}
    for row in chapters_served:
        org = clean_text(row.get("org"))
        if not org:
            continue
        bucket = org_map.setdefault(org, {"org": org, "chapters_count": 0, "vendors_count": 0})
        bucket["chapters_count"] += 1
    for row in vendors_served:
        org = clean_text(row.get("org"))
        if not org:
            continue
        bucket = org_map.setdefault(org, {"org": org, "chapters_count": 0, "vendors_count": 0})
        bucket["vendors_count"] += 1
    orgs_served = sorted(
        [
            {
                "org": item["org"],
                "org_type": org_type_lookup.get(clean_text(item["org"]).lower(), "Organization"),
                "chapters_count": int(item["chapters_count"]),
                "vendors_count": int(item["vendors_count"]),
            }
            for item in org_map.values()
        ],
        key=lambda r: (-(int(r.get("chapters_count") or 0) + int(r.get("vendors_count") or 0)), r.get("org", "")),
    )

    for row in vendors_served:
        row["detail_href"] = (
            f"/vendors/detail?vendor_id={int(row.get('vendor_id'))}"
            if row.get("vendor_id") is not None
            else f"/vendors/detail?vendor_name={quote_plus(clean_text(row.get('name')))}"
        )

    conn = get_connection()
    ensure_crm_tables(conn)
    today = date.today().isoformat()
    follow_up_rows = conn.execute(
        """
        SELECT id, name, type, status, follow_up_date, priority
        FROM crm_contacts
        WHERE workspace_id=?
          AND trim(coalesce(follow_up_date,''))<>''
          AND follow_up_date <= ?
          AND lower(coalesce(status,'')) NOT IN ('closed','dormant')
        ORDER BY follow_up_date ASC, id DESC
        LIMIT 50
        """,
        (workspace_id, today),
    ).fetchall()
    task_rows = conn.execute(
        """
        SELECT t.id, t.crm_contact_id, t.title, t.due_date, t.priority, c.name, c.type
        FROM crm_tasks t
        JOIN crm_contacts c ON c.id=t.crm_contact_id
        WHERE t.workspace_id=?
          AND c.workspace_id=?
          AND lower(coalesce(t.status,''))='open'
          AND trim(coalesce(t.due_date,''))<>''
          AND t.due_date <= ?
        ORDER BY t.due_date ASC, t.id DESC
        LIMIT 50
        """,
        (workspace_id, workspace_id, today),
    ).fetchall()
    needs_follow_up = []
    for row in follow_up_rows:
        needs_follow_up.append(
            {
                "kind": "contact",
                "contact_id": int(row["id"]),
                "name": clean_text(row["name"]),
                "type": clean_text(row["type"]),
                "status": clean_text(row["status"]),
                "priority": clean_text(row["priority"]) or "normal",
                "due_date": clean_text(row["follow_up_date"]),
                "label": "Follow-up",
            }
        )
    for row in task_rows:
        needs_follow_up.append(
            {
                "kind": "task",
                "contact_id": int(row["crm_contact_id"]),
                "task_id": int(row["id"]),
                "name": clean_text(row["name"]),
                "type": clean_text(row["type"]),
                "status": "task_open",
                "priority": clean_text(row["priority"]) or "normal",
                "due_date": clean_text(row["due_date"]),
                "label": clean_text(row["title"]) or "Task",
            }
        )
    needs_follow_up = sorted(
        needs_follow_up,
        key=lambda r: (clean_text(r.get("due_date")) or "9999-12-31", clean_text(r.get("name"))),
    )[:60]

    activity_rows = conn.execute(
        """
        SELECT a.id, a.action, a.detail, a.created_at, c.id AS contact_id, c.name, c.type
        FROM crm_activities a
        LEFT JOIN crm_contacts c
          ON c.id = a.crm_contact_id
         AND c.workspace_id = a.workspace_id
        WHERE a.workspace_id=?
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 60
        """,
        (workspace_id,),
    ).fetchall()
    recent_activity = [
        {
            "id": int(row["id"]),
            "action": clean_text(row["action"]),
            "detail": clean_text(row["detail"]),
            "created_at": clean_text(row["created_at"]),
            "contact_id": int(row["contact_id"]) if row["contact_id"] is not None else None,
            "name": clean_text(row["name"]),
            "type": clean_text(row["type"]),
        }
        for row in activity_rows
    ]

    metrics = {
        "schools_served": len(schools_served),
        "orgs_served": len(orgs_served),
        "chapters_served": len(chapters_served),
        "vendors_served": len(vendors_served),
    }
    return render_app(
        "dashboards/dashboard.html",
        metrics=metrics,
        schools_served=schools_served,
        orgs_served=orgs_served,
        chapters_served=chapters_served,
        vendors_served=vendors_served,
        needs_follow_up=needs_follow_up,
        recent_activity=recent_activity,
        error="",
    )

@bp.route("/leads")
@login_required()
def leads_page():
    return redirect(url_for("vendors.crm_page"))
