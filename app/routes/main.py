import os
from datetime import date
from urllib.parse import quote, quote_plus
from flask import Blueprint, current_app, render_template, redirect, url_for, jsonify
from ..auth import account_type_for_user, is_brand_owner_user, login_required, get_session_user
from ..config import Config
from ..database import get_connection, ensure_chapters_table, ensure_crm_tables, ensure_institutions_table, ensure_vendor_table
from ..utils.text_utils import clean_text
from ..utils.workspace import workspace_id_for_user

bp = Blueprint('main', __name__)

DASHBOARD_LIST_LIMIT = 24

def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)


def _served_school_rows(conn, workspace_id: str) -> list[dict]:
    chapter_contact_rows = conn.execute(
        """
        SELECT c.chapter_id, c.name, c.connection, c.created_at, ch.school, ch.city, ch.state
        FROM crm_contacts c
        LEFT JOIN chapters ch ON ch.chapter_uid=c.chapter_id
        WHERE c.workspace_id=?
          AND lower(coalesce(c.type,''))='chapter'
          AND lower(coalesce(c.status,''))='closed'
        ORDER BY c.created_at DESC, c.id DESC
        """,
        (workspace_id,),
    ).fetchall()
    order_rows = conn.execute(
        """
        SELECT chapter_id, chapter_name, org, school, city, state, vendor, created_at
        FROM vendor_orders
        WHERE workspace_id=?
        ORDER BY created_at DESC, id DESC
        """,
        (workspace_id,),
    ).fetchall()

    chapter_map = {}
    for row in chapter_contact_rows:
        chapter_id = clean_text(row["chapter_id"])
        name = clean_text(row["name"])
        key = chapter_id or f"name::{name.lower()}"
        if not key:
            continue
        chapter_map.setdefault(
            key,
            {
                "chapter_id": chapter_id,
                "name": name,
                "org": clean_text(row["connection"]),
                "school": clean_text(row["school"]),
                "city": clean_text(row["city"]),
                "state": clean_text(row["state"]),
                "added_at": clean_text(row["created_at"]),
            },
        )
    for row in order_rows:
        chapter_id = clean_text(row["chapter_id"])
        name = clean_text(row["chapter_name"])
        key = chapter_id or f"name::{name.lower()}"
        if not key:
            continue
        chapter_map.setdefault(
            key,
            {
                "chapter_id": chapter_id,
                "name": name,
                "org": clean_text(row["org"]),
                "school": clean_text(row["school"]),
                "city": clean_text(row["city"]),
                "state": clean_text(row["state"]),
                "added_at": clean_text(row["created_at"]),
            },
        )

    chapters_served = [
        {
            **rec,
            "location": ", ".join(part for part in [clean_text(rec.get("city")), clean_text(rec.get("state"))] if part),
            "encodedId": quote(clean_text(rec.get("chapter_id")), safe="") if clean_text(rec.get("chapter_id")) else "",
        }
        for rec in chapter_map.values()
    ]
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
    return sorted(
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


def _served_map_payload(conn, workspace_id: str) -> dict:
    ws = clean_text(workspace_id)
    if not ws:
        return {
            "points": [],
            "stats": {
                "mapped": 0,
                "chapter_linked": 0,
                "institution_served": 0,
                "chapter_only": 0,
                "institution_only": 0,
                "both": 0,
            },
        }

    ensure_crm_tables(conn)
    ensure_chapters_table(conn)
    ensure_institutions_table(conn)

    schools_served = _served_school_rows(conn, ws)
    served_school_names = {
        clean_text(row.get("school"))
        for row in schools_served
        if clean_text(row.get("school"))
    }
    served_institution_rows = conn.execute(
        """
        SELECT name, connection
        FROM crm_contacts
        WHERE workspace_id=?
          AND type IN ('school', 'other')
          AND lower(coalesce(status, ''))='closed'
        ORDER BY id DESC
        """,
        (ws,),
    ).fetchall()
    institution_ids: set[str] = set()
    institution_names = {name.lower() for name in served_school_names}
    for row in served_institution_rows:
        connection = clean_text(row["connection"])
        if connection.startswith("institution:"):
            institution_ids.add(connection.split(":", 1)[1])
        name = clean_text(row["name"]).lower()
        if name:
            institution_names.add(name)

    institution_rows = []
    where_clauses = []
    params: list[str] = []
    if institution_ids:
        where_clauses.append(f"id IN ({','.join('?' for _ in institution_ids)})")
        params.extend(sorted(institution_ids))
    if institution_names:
        where_clauses.append(f"lower(location_name) IN ({','.join('?' for _ in institution_names)})")
        params.extend(sorted(institution_names))
    if where_clauses:
        institution_rows = conn.execute(
            f"""
            SELECT id, location_name, city, state, latitude, longitude, control, institution_level
            FROM institutions
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND trim(coalesce(latitude, '')) <> ''
              AND trim(coalesce(longitude, '')) <> ''
              AND ({' OR '.join(where_clauses)})
            """,
            tuple(params),
        ).fetchall()

    institution_by_id = {}
    institution_by_name = {}
    for row in institution_rows:
        item = {k: row[k] for k in row.keys()}
        try:
            item["latitude"] = float(item["latitude"])
            item["longitude"] = float(item["longitude"])
        except (TypeError, ValueError):
            continue
        institution_by_id[str(item["id"])] = item
        lookup_key = clean_text(item.get("location_name")).lower()
        if lookup_key and lookup_key not in institution_by_name:
            institution_by_name[lookup_key] = item

    served_map_points = {}

    def upsert_map_point(inst: dict, *, school_row=None, institution_contact=False):
        key = str(inst.get("id"))
        point = served_map_points.setdefault(
            key,
            {
                "institution_id": int(inst["id"]),
                "institution_name": clean_text(inst.get("location_name")),
                "city": clean_text(inst.get("city")),
                "state": clean_text(inst.get("state")),
                "latitude": float(inst["latitude"]),
                "longitude": float(inst["longitude"]),
                "control": clean_text(inst.get("control")),
                "institution_level": clean_text(inst.get("institution_level")),
                "chapter_count": 0,
                "org_count": 0,
                "has_chapter_service": False,
                "has_institution_service": False,
            },
        )
        if school_row:
            point["has_chapter_service"] = True
            point["chapter_count"] = max(int(point["chapter_count"]), int(school_row.get("chapters_count") or 0))
            point["org_count"] = max(int(point["org_count"]), int(school_row.get("org_count") or 0))
        if institution_contact:
            point["has_institution_service"] = True

    for row in schools_served:
        school_key = clean_text(row.get("school")).lower()
        inst = institution_by_name.get(school_key)
        if inst:
            upsert_map_point(inst, school_row=row)

    for row in served_institution_rows:
        connection = clean_text(row["connection"])
        inst = None
        if connection.startswith("institution:"):
            inst = institution_by_id.get(connection.split(":", 1)[1])
        if not inst:
            inst = institution_by_name.get(clean_text(row["name"]).lower())
        if inst:
            upsert_map_point(inst, institution_contact=True)

    points = sorted(
        served_map_points.values(),
        key=lambda item: (
            -int(item.get("has_institution_service") or 0),
            -int(item.get("chapter_count") or 0),
            item.get("institution_name", ""),
        ),
    )
    both = sum(1 for point in points if point.get("has_chapter_service") and point.get("has_institution_service"))
    chapter_only = sum(1 for point in points if point.get("has_chapter_service") and not point.get("has_institution_service"))
    institution_only = sum(1 for point in points if point.get("has_institution_service") and not point.get("has_chapter_service"))
    return {
        "points": points,
        "stats": {
            "mapped": len(points),
            "chapter_linked": chapter_only + both,
            "institution_served": institution_only + both,
            "chapter_only": chapter_only,
            "institution_only": institution_only,
            "both": both,
        },
    }


def _landing_asset_name(extension: str) -> str:
    static_root = current_app.static_folder or ""
    assets_dir = os.path.join(static_root, "ops_hub", "assets")
    if not os.path.isdir(assets_dir):
        return ""
    matches = sorted(
        name
        for name in os.listdir(assets_dir)
        if name.startswith("index-") and name.endswith(extension)
    )
    return matches[0] if matches else ""


@bp.route("/")
def index():
    user = get_session_user()
    default_cta_href = url_for("auth.signup_page")
    login_href = url_for("auth.login_page")
    if user:
        default_cta_href = (
            url_for("brand.brand_dashboard_page")
            if is_brand_owner_user(user)
            else url_for("main.dashboard_page")
        )
        login_href = default_cta_href
    landing_css = _landing_asset_name(".css")
    landing_js = _landing_asset_name(".js")
    if not landing_css or not landing_js:
        return redirect(login_href)
    return render_template(
        "landing/ops_hub.html",
        landing_css=landing_css,
        landing_js=landing_js,
        landing_links={
            "login": login_href,
            "signup": default_cta_href if user else url_for("auth.signup_page"),
            "get_started": default_cta_href,
            "start_free": default_cta_href,
            "track": url_for("ops.ops_customer_track"),
        },
    )


@bp.route("/terms")
def terms_page():
    return render_template(
        "legal/simple_page.html",
        page_title="Terms of Service",
        page_kicker="Legal",
        page_summary="Short-form workspace terms for using the Greek Chapters platform.",
        sections=[
            {
                "title": "Use of the platform",
                "body": "Use the app for legitimate relationship management, order tracking, and team collaboration. Do not misuse the platform for unlawful activity, credential abuse, or scraping protected data.",
            },
            {
                "title": "Your workspace data",
                "body": "You are responsible for the data entered into your workspace, including contacts, orders, notes, and files. Keep credentials secure and limit access to authorized teammates.",
            },
            {
                "title": "Availability",
                "body": "We aim to keep the product fast and available, but uptime, features, and workflows may change as the product evolves.",
            },
        ],
    )


@bp.route("/privacy")
def privacy_page():
    return render_template(
        "legal/simple_page.html",
        page_title="Privacy Policy",
        page_kicker="Legal",
        page_summary="A simple overview of how account and workspace information is handled.",
        sections=[
            {
                "title": "What we collect",
                "body": "We store the account details, workspace records, and operational data needed to let you sign in, manage contacts, and track orders.",
            },
            {
                "title": "How it is used",
                "body": "Information is used to operate the product, authenticate users, support workspace features, and improve performance and reliability.",
            },
            {
                "title": "Control",
                "body": "Workspace owners should avoid storing unnecessary sensitive data and should manage teammate access carefully.",
            },
        ],
    )

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
            served_map_points=[],
            needs_follow_up=[],
            recent_activity=[],
            error="Run import_csv.py first",
        )
    
    user = get_session_user()
    if is_brand_owner_user(user):
        return redirect(url_for("brand.brand_dashboard_page"))
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_chapters_table(conn)
    ensure_vendor_table(conn)
    chapter_contact_rows = conn.execute(
        """
        SELECT c.chapter_id, c.name, c.connection, c.created_at, ch.school, ch.city, ch.state
        FROM crm_contacts c
        LEFT JOIN chapters ch ON ch.chapter_uid=c.chapter_id
        WHERE c.workspace_id=?
          AND lower(coalesce(c.type,''))='chapter'
          AND lower(coalesce(c.status,''))='closed'
        ORDER BY c.created_at DESC, c.id DESC
        """,
        (workspace_id,),
    ).fetchall()
    order_rows = conn.execute(
        """
        SELECT chapter_id, chapter_name, org, school, city, state, vendor, created_at
        FROM vendor_orders
        WHERE workspace_id=?
        ORDER BY created_at DESC, id DESC
        """,
        (workspace_id,),
    ).fetchall()
    chapter_map = {}
    for row in chapter_contact_rows:
        chapter_id = clean_text(row["chapter_id"])
        name = clean_text(row["name"])
        key = chapter_id or f"name::{name.lower()}"
        if not key:
            continue
        chapter_map.setdefault(
            key,
            {
                "chapter_id": chapter_id,
                "name": name,
                "org": clean_text(row["connection"]),
                "school": clean_text(row["school"]),
                "city": clean_text(row["city"]),
                "state": clean_text(row["state"]),
                "added_at": clean_text(row["created_at"]),
            },
        )
    for row in order_rows:
        chapter_id = clean_text(row["chapter_id"])
        name = clean_text(row["chapter_name"])
        key = chapter_id or f"name::{name.lower()}"
        if not key:
            continue
        chapter_map.setdefault(
            key,
            {
                "chapter_id": chapter_id,
                "name": name,
                "org": clean_text(row["org"]),
                "school": clean_text(row["school"]),
                "city": clean_text(row["city"]),
                "state": clean_text(row["state"]),
                "added_at": clean_text(row["created_at"]),
            },
        )
    chapters_served = sorted(
        [
            {
                **rec,
                "location": ", ".join(part for part in [clean_text(rec.get("city")), clean_text(rec.get("state"))] if part),
                "encodedId": quote(clean_text(rec.get("chapter_id")), safe="") if clean_text(rec.get("chapter_id")) else "",
            }
            for rec in chapter_map.values()
        ],
        key=lambda r: (clean_text(r.get("added_at")), clean_text(r.get("name"))),
        reverse=True,
    )
    total_chapters_served = len(chapters_served)
    closed_vendor_rows = conn.execute(
        """
        SELECT c.name, c.connection, c.created_at, v.id AS vendor_id, v.category, v.state, v.city, v.website, v.email
        FROM crm_contacts c
        LEFT JOIN vendors v ON lower(v.vendor)=lower(c.name)
        WHERE c.workspace_id=?
          AND lower(coalesce(c.type,''))='vendor'
          AND lower(coalesce(c.status,''))='closed'
        ORDER BY c.created_at DESC, c.id DESC
        """,
        (workspace_id,),
    ).fetchall()
    vendor_map = {}
    for row in closed_vendor_rows:
        name = clean_text(row["name"])
        key = name.lower()
        if not key:
            continue
        vendor_map.setdefault(
            key,
            {
                "vendor_id": int(row["vendor_id"]) if row["vendor_id"] is not None else None,
                "name": name,
                "org": clean_text(row["connection"]),
                "products": clean_text(row["category"]),
                "state": clean_text(row["state"]),
                "city": clean_text(row["city"]),
                "website": clean_text(row["website"]),
                "email": clean_text(row["email"]),
                "added_at": clean_text(row["created_at"]),
            },
        )
    for row in order_rows:
        name = clean_text(row["vendor"])
        key = name.lower()
        if not key:
            continue
        vendor_map.setdefault(
            key,
            {
                "vendor_id": None,
                "name": name,
                "org": clean_text(row["org"]),
                "products": "",
                "state": clean_text(row["state"]),
                "city": clean_text(row["city"]),
                "website": "",
                "email": "",
                "added_at": clean_text(row["created_at"]),
            },
        )
    vendors_served = sorted(
        [
            {**rec, "location": ", ".join(part for part in [clean_text(rec.get("city")), clean_text(rec.get("state"))] if part)}
            for rec in vendor_map.values()
        ],
        key=lambda r: (clean_text(r.get("added_at")), clean_text(r.get("name"))),
        reverse=True,
    )
    total_vendors_served = len(vendors_served)

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
    total_schools_served = len(schools_served)

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
    total_orgs_served = len(orgs_served)

    for row in vendors_served:
        row["detail_href"] = (
            f"/vendors/detail?vendor_id={int(row.get('vendor_id'))}"
            if row.get("vendor_id") is not None
            else f"/vendors/detail?vendor_name={quote_plus(clean_text(row.get('name')))}"
        )

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
        "schools_served": total_schools_served,
        "orgs_served": total_orgs_served,
        "chapters_served": total_chapters_served,
        "vendors_served": total_vendors_served,
    }

    return render_app(
        "dashboards/dashboard.html",
        metrics=metrics,
        schools_served=schools_served[:DASHBOARD_LIST_LIMIT],
        orgs_served=orgs_served[:DASHBOARD_LIST_LIMIT],
        chapters_served=chapters_served[:DASHBOARD_LIST_LIMIT],
        vendors_served=vendors_served[:DASHBOARD_LIST_LIMIT],
        served_map_points=[],
        served_map_stats={"mapped": 0, "chapter_linked": 0, "institution_served": 0, "chapter_only": 0, "institution_only": 0, "both": 0},
        served_map_lazy=True,
        needs_follow_up=needs_follow_up,
        recent_activity=recent_activity,
        error="",
    )


@bp.route("/api/dashboard/served-map")
@login_required()
def dashboard_served_map_api():
    user = get_session_user()
    if is_brand_owner_user(user):
        return jsonify({"ok": False, "error": "manufacturer dashboard only"}), 403
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    payload = _served_map_payload(conn, workspace_id)
    return jsonify({"ok": True, **payload})

@bp.route("/leads")
@login_required()
def leads_page():
    return redirect(url_for("vendors.crm_page"))
