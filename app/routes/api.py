import os
import re
import json
from datetime import datetime, date
from flask import Blueprint, request, jsonify, redirect, url_for, Response
from urllib.parse import quote

from ..auth import login_required, get_session_user
from ..database import get_connection, ensure_crm_tables, ensure_vendor_table, log_activity, log_lead_activity
from ..config import Config
from ..services.dashboard import manufacturer_dashboard_snapshot, manufacturer_dashboard_dataset
from ..services.chapters import fetch_normalized_rows, get_chapter_by_id
from ..services.vendors import vendor_competitors, build_vendor_hot_leads
from ..utils.text_utils import clean_text, clean_date
from ..utils.email import send_email_best_effort
from ..utils.workspace import workspace_id_for_user

bp = Blueprint('api', __name__)

def normalize_crm_status(value: str) -> str:
    raw = clean_text(value).lower().replace(" ", "_")
    aliases = {
        "served": "closed",
        "won": "closed",
        "lost": "dormant",
        "lead": "prospect",
    }
    raw = aliases.get(raw, raw)
    allowed = {"prospect", "contacted", "follow_up", "negotiating", "closed", "dormant"}
    return raw if raw in allowed else "prospect"

def action_to_status(value: str) -> str:
    action = clean_text(value).lower()
    if action in {"served", "closed"}:
        return "closed"
    if action in {"contacted", "follow_up", "negotiating", "dormant"}:
        return action
    return "prospect"

def _iso_date(value: str) -> str:
    return clean_date(value)

def _is_overdue(due_date: str) -> bool:
    d = _iso_date(due_date)
    if not d:
        return False
    try:
        return d < date.today().isoformat()
    except Exception:
        return False

def _manufacturer_id_from_user(conn, user: dict) -> int:
    current = int(user.get("manufacturer_id") or 0)
    if current > 0:
        return current
    user_id = int(user.get("id") or 0)
    if user_id <= 0:
        return 0
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "manufacturer_id" not in user_cols:
        return 0
    row = conn.execute("SELECT manufacturer_id FROM users WHERE id=?", (user_id,)).fetchone()
    return int(row["manufacturer_id"] or 0) if row else 0

@bp.route("/api/me")
def api_me():
    user = get_session_user()
    if not user:
        return jsonify({"ok": False, "error": "not logged in"}), 401
    return jsonify({"ok": True, "user": user})

@bp.route("/api/m/dashboard/snapshot")
@login_required()
def api_m_dashboard_snapshot():
    user = get_session_user()
    hot_limit_raw = clean_text(request.args.get("hot_limit")) or "12"
    hot_limit = int(hot_limit_raw) if hot_limit_raw.isdigit() else 12
    snapshot = manufacturer_dashboard_snapshot(user, hot_limit=hot_limit, activity_limit=25)
    return jsonify({"ok": True, **snapshot})

@bp.route("/api/m/dashboard/details")
@login_required()
def api_m_dashboard_details():
    user = get_session_user()
    kind = clean_text(request.args.get("kind")).lower()
    valid_kinds = {"hot_chapters", "hot_vendors", "chapters_served", "vendors_served"}
    if kind not in valid_kinds:
        return jsonify({"ok": False, "error": "kind must be one of hot_chapters, hot_vendors, chapters_served, vendors_served"}), 400

    page_raw = clean_text(request.args.get("page")) or "1"
    size_raw = clean_text(request.args.get("page_size")) or "10"
    page = int(page_raw) if page_raw.isdigit() else 1
    page_size = int(size_raw) if size_raw.isdigit() else 10
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    dataset = manufacturer_dashboard_dataset(user, activity_limit=0)
    rows = dataset.get(kind, [])
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    return jsonify(
        {
            "ok": True,
            "kind": kind,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "rows": rows[start:end],
        }
    )

@bp.route("/api/m/chapters")
@login_required()
def api_m_chapters():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    crm_rows = conn.execute(
        "SELECT chapter_id, status FROM crm_contacts WHERE type='chapter' AND workspace_id=?",
        (workspace_id,),
    ).fetchall()
    crm_map = {clean_text(r["chapter_id"]): clean_text(r["status"]).lower() for r in crm_rows if clean_text(r["chapter_id"])}
    
    # We will assume served is derived from vendor_orders for unified roles now, since manufacturer_orders is removed (or will be)
    # Actually, let's just query vendor_orders for served chapters
    served_rows = conn.execute(
        "SELECT DISTINCT chapter_id FROM vendor_orders WHERE workspace_id=?",
        (workspace_id,),
    ).fetchall()
    served_set = {clean_text(r["chapter_id"]) for r in served_rows if clean_text(r["chapter_id"])}
    rows = fetch_normalized_rows()
    out = []
    for r in rows:
        chapter_id = clean_text(r.get("id"))
        crm_status = crm_map.get(chapter_id, "")
        already_in_crm = bool(crm_status)
        already_served = chapter_id in served_set or crm_status == "closed"
        out.append(
            {
                "chapter_id": chapter_id,
                "chapter_name": clean_text(r.get("chapterName")),
                "organization": clean_text(r.get("orgName")),
                "license_type": clean_text(r.get("orgName")),
                "location": f"{clean_text(r.get('city'))}, {clean_text(r.get('state'))}".strip(", "),
                "school": clean_text(r.get("school")),
                "in_crm": already_in_crm,
                "crm_status": crm_status,
                "served": already_served,
            }
        )
    return jsonify({"ok": True, "rows": out})

@bp.route("/api/m/vendors")
@login_required()
def api_m_vendors():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_vendor_table(conn)
    crm_vendor_rows = conn.execute(
        "SELECT lower(name) AS vendor_name, status FROM crm_contacts WHERE type='vendor' AND workspace_id=?",
        (workspace_id,),
    ).fetchall()
    crm_vendor_map = {clean_text(r["vendor_name"]): clean_text(r["status"]).lower() for r in crm_vendor_rows if clean_text(r["vendor_name"])}
    
    # served mapping for unified vendors comes from vendor_orders if we consider them having filled an order
    served_vendor_rows = conn.execute(
        "SELECT DISTINCT lower(vendor) AS vendor_name FROM vendor_orders WHERE workspace_id=? AND trim(coalesce(vendor,''))<>''",
        (workspace_id,),
    ).fetchall()
    served_vendor_set = {clean_text(r["vendor_name"]) for r in served_vendor_rows if clean_text(r["vendor_name"])}
    
    rows = conn.execute(
        """
        SELECT id, vendor, category, organization, state, website
        FROM vendors
        ORDER BY vendor ASC
        LIMIT 5000
        """
    ).fetchall()
    out = []
    for row in rows:
        item = {k: row[k] for k in row.keys()}
        vnorm = clean_text(item.get("vendor")).lower()
        crm_status = crm_vendor_map.get(vnorm, "")
        in_crm = bool(crm_status)
        served = vnorm in served_vendor_set or crm_status == "closed"
        item["in_crm"] = in_crm
        item["crm_status"] = crm_status
        item["served"] = served
        out.append(item)
    return jsonify({"ok": True, "rows": out})

@bp.route("/api/m/crm/add-chapter", methods=["POST"])
@login_required()
def api_m_crm_add_chapter():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    chapter_id = clean_text(payload.get("chapter_id"))
    chapter_name = clean_text(payload.get("chapter_name"))
    license_type = clean_text(payload.get("license_type"))
    chapter_school = clean_text(payload.get("school"))
    chapter_city = clean_text(payload.get("city"))
    chapter_state = clean_text(payload.get("state"))
    action = clean_text(payload.get("action"))
    status = action_to_status(action)
    if not chapter_id:
        return jsonify({"ok": False, "error": "chapter_id is required"}), 400

    # Avoid heavy dataset lookup for normal prospect adds; only fallback when needed.
    needs_lookup = (not chapter_name) or (not license_type) or (status == "closed" and not chapter_school and not chapter_city and not chapter_state)
    if needs_lookup:
        chapter_row = get_chapter_by_id(chapter_id)
        if not chapter_name:
            chapter_name = clean_text(chapter_row.get("chapterName"))
        if not license_type:
            license_type = clean_text(chapter_row.get("orgName"))
        if not chapter_school:
            chapter_school = clean_text(chapter_row.get("school"))
        if not chapter_city:
            chapter_city = clean_text(chapter_row.get("city"))
        if not chapter_state:
            chapter_state = clean_text(chapter_row.get("state"))
    if not chapter_name:
        chapter_name = chapter_id

    conn = get_connection()
    ensure_crm_tables(conn)
    manufacturer_id = _manufacturer_id_from_user(conn, user)
    exists = conn.execute(
        """
        SELECT id FROM crm_contacts
        WHERE type='chapter' AND chapter_id=? AND workspace_id=?
        """,
        (chapter_id, workspace_id),
    ).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE crm_contacts
            SET status=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (status, int(exists["id"])),
        )
        contact_id = int(exists["id"])
        duplicate = True
    else:
        crm_cols = {row[1] for row in conn.execute("PRAGMA table_info(crm_contacts)").fetchall()}
        insert_cols = ["type", "name", "chapter_id", "connection", "status", "workspace_id"]
        insert_vals = ["chapter", chapter_name, chapter_id, license_type, status, workspace_id]
        if "priority" in crm_cols:
            insert_cols.append("priority")
            insert_vals.append("normal")
        if "updated_at" in crm_cols:
            insert_cols.append("updated_at")
            insert_vals.append(datetime.now().isoformat(timespec="seconds"))
        if "manufacturer_id" in crm_cols:
            insert_cols.insert(0, "manufacturer_id")
            insert_vals.insert(0, manufacturer_id)
        placeholders = ",".join("?" for _ in insert_cols)
        cur = conn.execute(
            f"INSERT INTO crm_contacts({','.join(insert_cols)}) VALUES({placeholders})",
            tuple(insert_vals),
        )
        contact_id = int(cur.lastrowid)
        duplicate = False

    if status == "closed":
        served_exists = conn.execute(
            """
            SELECT id FROM vendor_orders
            WHERE chapter_id=? AND workspace_id=?
            """,
            (chapter_id, workspace_id),
        ).fetchone()
        if served_exists is None:
            conn.execute(
                """
                INSERT INTO vendor_orders(vendor, chapter_id, chapter_name, org, school, city, state, year, product, quantity, notes, order_type, workspace_id)
                VALUES('', ?, ?, ?, ?, ?, ?, NULL, '', NULL, '', 'Served', ?)
                """,
                (
                    chapter_id,
                    chapter_name,
                    license_type,
                    chapter_school,
                    chapter_city,
                    chapter_state,
                    workspace_id,
                ),
            )

    conn.execute(
        """
        INSERT INTO crm_activities(crm_contact_id, action, detail, created_by_user_id, workspace_id)
        VALUES(?, ?, ?, ?, ?)
        """,
        (
            int(contact_id),
            "marked_served" if status == "closed" else "added_to_pipeline",
            f"{chapter_name} ({license_type or 'chapter'})",
            int(user.get("id") or 0),
            workspace_id,
        ),
    )

    log_activity(
        conn,
        int(user.get("id") or 0),
        "added_chapter_served" if status == "closed" else "added_chapter_prospect",
        "crm_contact",
        str(contact_id),
        chapter_name,
        workspace_id=workspace_id,
    )
    conn.commit()
    return jsonify({"ok": True, "duplicate": duplicate, "status": status})

@bp.route("/crm/add-chapter", methods=["POST"])
@login_required()
def api_alias_add_chapter():
    return api_m_crm_add_chapter()

@bp.route("/api/m/crm/add-vendor", methods=["POST"])
@login_required()
def api_m_crm_add_vendor():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    vendor_id_raw = clean_text(payload.get("vendor_id"))
    vendor_name = clean_text(payload.get("vendor_name"))
    license_type = clean_text(payload.get("license_type"))
    action = clean_text(payload.get("action"))
    status = action_to_status(action)
    if not vendor_name:
        return jsonify({"ok": False, "error": "vendor_name is required"}), 400
    vendor_id = int(vendor_id_raw) if vendor_id_raw.isdigit() else None

    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_vendor_table(conn)
    manufacturer_id = _manufacturer_id_from_user(conn, user)
    vendor_meta = None
    if vendor_id is not None:
        vendor_meta = conn.execute(
            "SELECT organization, city, state FROM vendors WHERE id=?",
            (vendor_id,),
        ).fetchone()
    if vendor_meta is None:
        vendor_meta = conn.execute(
            """
            SELECT organization, city, state
            FROM vendors
            WHERE lower(vendor)=lower(?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (vendor_name,),
        ).fetchone()
    if vendor_meta is not None and not license_type:
        license_type = clean_text(vendor_meta["organization"])
    vendor_city = clean_text(vendor_meta["city"]) if vendor_meta is not None else ""
    vendor_state = clean_text(vendor_meta["state"]) if vendor_meta is not None else ""
    exists = conn.execute(
        """
        SELECT id FROM crm_contacts
        WHERE type='vendor' AND lower(name)=lower(?) AND workspace_id=?
        """,
        (vendor_name, workspace_id),
    ).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE crm_contacts
            SET status=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (status, int(exists["id"])),
        )
        contact_id = int(exists["id"])
        duplicate = True
    else:
        crm_cols = {row[1] for row in conn.execute("PRAGMA table_info(crm_contacts)").fetchall()}
        insert_cols = ["type", "name", "vendor_id", "connection", "status", "workspace_id"]
        insert_vals = ["vendor", vendor_name, vendor_id, license_type, status, workspace_id]
        if "priority" in crm_cols:
            insert_cols.append("priority")
            insert_vals.append("normal")
        if "updated_at" in crm_cols:
            insert_cols.append("updated_at")
            insert_vals.append(datetime.now().isoformat(timespec="seconds"))
        if "manufacturer_id" in crm_cols:
            insert_cols.insert(0, "manufacturer_id")
            insert_vals.insert(0, manufacturer_id)
        placeholders = ",".join("?" for _ in insert_cols)
        cur = conn.execute(
            f"INSERT INTO crm_contacts({','.join(insert_cols)}) VALUES({placeholders})",
            tuple(insert_vals),
        )
        contact_id = int(cur.lastrowid)
        duplicate = False

    if status == "closed":
        served_exists = conn.execute(
            """
            SELECT id FROM vendor_orders
            WHERE lower(vendor)=lower(?) AND workspace_id=?
            """,
            (vendor_name, workspace_id),
        ).fetchone()
        if served_exists is None:
            conn.execute(
                """
                INSERT INTO vendor_orders(vendor, chapter_id, org, chapter_name, school, city, state, year, product, quantity, notes, order_type, workspace_id)
                VALUES(?, '', ?, '', '', ?, ?, NULL, '', NULL, '', 'Served', ?)
                """,
                (
                    vendor_name,
                    license_type,
                    vendor_city,
                    vendor_state,
                    workspace_id,
                ),
            )

    conn.execute(
        """
        INSERT INTO crm_activities(crm_contact_id, action, detail, created_by_user_id, workspace_id)
        VALUES(?, ?, ?, ?, ?)
        """,
        (
            int(contact_id),
            "marked_served" if status == "closed" else "added_to_pipeline",
            f"{vendor_name} ({license_type or 'vendor'})",
            int(user.get("id") or 0),
            workspace_id,
        ),
    )

    log_activity(
        conn,
        int(user.get("id") or 0),
        "added_vendor_served" if status == "closed" else "added_vendor_prospect",
        "crm_contact",
        str(contact_id),
        vendor_name,
        workspace_id=workspace_id,
    )
    conn.commit()
    return jsonify({"ok": True, "duplicate": duplicate, "status": status})

@bp.route("/crm/add-vendor", methods=["POST"])
@login_required()
def api_alias_add_vendor():
    return api_m_crm_add_vendor()

@bp.route("/api/m/crm")
@login_required()
def api_m_crm():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_vendor_table(conn)
    rows = conn.execute(
        """
        SELECT
            c.id,
            c.name,
            c.type,
            c.connection,
            c.status,
            c.notes,
            c.chapter_id,
            c.vendor_id,
            c.priority,
            c.value_estimate,
            c.expected_close_date,
            c.last_contact_at,
            c.follow_up_date,
            c.updated_at,
            c.created_at,
            (
                SELECT COUNT(*)
                FROM crm_notes n
                WHERE n.crm_contact_id = c.id AND n.workspace_id = c.workspace_id
            ) AS note_count,
            (
                SELECT COUNT(*)
                FROM crm_tasks t
                WHERE t.crm_contact_id = c.id
                  AND t.workspace_id = c.workspace_id
                  AND lower(coalesce(t.status,'')) = 'open'
            ) AS open_task_count
        FROM crm_contacts c
        WHERE c.workspace_id = ?
        ORDER BY c.updated_at DESC, c.created_at DESC, c.id DESC
        """,
        (workspace_id,),
    ).fetchall()
    return jsonify({"ok": True, "rows": [{k: row[k] for k in row.keys()} for row in rows]})

@bp.route("/api/m/crm/update", methods=["POST"])
@login_required()
def api_m_crm_update():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    contact_id_raw = clean_text(payload.get("contact_id"))
    if not contact_id_raw.isdigit():
        return jsonify({"ok": False, "error": "contact_id is required"}), 400
    status = normalize_crm_status(payload.get("status"))
    notes = clean_text(payload.get("notes"))
    follow_up_date = clean_date(payload.get("follow_up_date"))
    priority = clean_text(payload.get("priority")).lower() or "normal"
    if priority not in {"low", "normal", "high"}:
        priority = "normal"
    expected_close_date = clean_date(payload.get("expected_close_date"))
    value_estimate_raw = clean_text(payload.get("value_estimate"))
    value_estimate = None
    if value_estimate_raw:
        try:
            value_estimate = float(value_estimate_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "value_estimate must be numeric"}), 400
    if clean_text(payload.get("follow_up_date")) and not follow_up_date:
        return jsonify({"ok": False, "error": "follow_up_date must be YYYY-MM-DD"}), 400
    if clean_text(payload.get("expected_close_date")) and not expected_close_date:
        return jsonify({"ok": False, "error": "expected_close_date must be YYYY-MM-DD"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    existing = conn.execute(
        "SELECT id, status, name, notes, follow_up_date, priority, value_estimate, expected_close_date FROM crm_contacts WHERE id=? AND workspace_id=?",
        (int(contact_id_raw), workspace_id),
    ).fetchone()
    if existing is None:
        return jsonify({"ok": False, "error": "contact not found"}), 404
    touch_last_contact = bool(payload.get("touch_last_contact")) or status == "contacted"
    conn.execute(
        """
        UPDATE crm_contacts
        SET status=?,
            notes=?,
            follow_up_date=?,
            priority=?,
            value_estimate=?,
            expected_close_date=?,
            last_contact_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_contact_at END,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND workspace_id=?
        """,
        (
            status,
            notes,
            follow_up_date,
            priority,
            value_estimate,
            expected_close_date,
            1 if touch_last_contact else 0,
            int(contact_id_raw),
            workspace_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO crm_activities(crm_contact_id, action, detail, created_by_user_id, workspace_id)
        VALUES(?, ?, ?, ?, ?)
        """,
        (
            int(contact_id_raw),
            "updated_contact",
            f"status={status} follow_up={follow_up_date or '-'} priority={priority}",
            int(user.get("id") or 0),
            workspace_id,
        ),
    )
    log_activity(
        conn,
        int(user.get("id") or 0),
        "updated_status",
        "crm_contact",
        contact_id_raw,
        f"{clean_text(existing['status'])} -> {status}",
        workspace_id=workspace_id,
    )
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/m/crm/bulk-update", methods=["POST"])
@login_required()
def api_m_crm_bulk_update():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}

    contact_ids_raw = payload.get("contact_ids")
    if not isinstance(contact_ids_raw, list):
        return jsonify({"ok": False, "error": "contact_ids must be a list"}), 400

    clean_ids = []
    seen_ids = set()
    for raw_id in contact_ids_raw:
        text = clean_text(raw_id)
        if not text.isdigit():
            continue
        cid = int(text)
        if cid <= 0 or cid in seen_ids:
            continue
        seen_ids.add(cid)
        clean_ids.append(cid)
    if not clean_ids:
        return jsonify({"ok": False, "error": "at least one valid contact_id is required"}), 400

    status_raw = clean_text(payload.get("status"))
    has_status = bool(status_raw)
    status = normalize_crm_status(status_raw) if has_status else ""

    follow_present = "follow_up_date" in payload
    follow_up_input = clean_text(payload.get("follow_up_date")) if follow_present else ""
    follow_up_date = None
    if follow_present and follow_up_input:
        follow_up_date = clean_date(follow_up_input)
        if not follow_up_date:
            return jsonify({"ok": False, "error": "follow_up_date must be YYYY-MM-DD"}), 400

    priority_present = "priority" in payload
    priority = ""
    if priority_present:
        priority = clean_text(payload.get("priority")).lower() or "normal"
        if priority not in {"low", "normal", "high"}:
            return jsonify({"ok": False, "error": "priority must be low, normal, or high"}), 400

    if not has_status and not follow_present and not priority_present:
        return jsonify({"ok": False, "error": "provide status and/or follow_up_date and/or priority"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    placeholders = ",".join(["?"] * len(clean_ids))
    rows = conn.execute(
        f"""
        SELECT id
        FROM crm_contacts
        WHERE workspace_id=? AND id IN ({placeholders})
        """,
        tuple([workspace_id] + clean_ids),
    ).fetchall()
    found_ids = [int(r["id"]) for r in rows]
    if not found_ids:
        return jsonify({"ok": False, "error": "no contacts found"}), 404

    found_set = set(found_ids)
    missing_ids = [cid for cid in clean_ids if cid not in found_set]

    set_parts = []
    params = []
    if has_status:
        set_parts.append("status=?")
        params.append(status)
    if follow_present:
        set_parts.append("follow_up_date=?")
        params.append(follow_up_date)
    if priority_present:
        set_parts.append("priority=?")
        params.append(priority)
    touch_last_contact = bool(payload.get("touch_last_contact")) or (has_status and status == "contacted")
    if touch_last_contact:
        set_parts.append("last_contact_at=CURRENT_TIMESTAMP")
    set_parts.append("updated_at=CURRENT_TIMESTAMP")

    found_placeholders = ",".join(["?"] * len(found_ids))
    conn.execute(
        f"""
        UPDATE crm_contacts
        SET {", ".join(set_parts)}
        WHERE workspace_id=? AND id IN ({found_placeholders})
        """,
        tuple(params + [workspace_id] + found_ids),
    )

    detail_parts = []
    if has_status:
        detail_parts.append(f"status={status}")
    if follow_present:
        detail_parts.append(f"follow_up={follow_up_date or '-'}")
    if priority_present:
        detail_parts.append(f"priority={priority}")
    detail = " ".join(detail_parts) or "bulk update"
    user_id = int(user.get("id") or 0)
    for cid in found_ids:
        conn.execute(
            """
            INSERT INTO crm_activities(crm_contact_id, action, detail, created_by_user_id, workspace_id)
            VALUES(?, ?, ?, ?, ?)
            """,
            (cid, "bulk_updated_contact", detail, user_id, workspace_id),
        )

    conn.commit()
    return jsonify({"ok": True, "updated_count": len(found_ids), "missing_ids": missing_ids})

@bp.route("/api/m/crm/contact")
@login_required()
def api_m_crm_contact_detail():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    contact_id_raw = clean_text(request.args.get("contact_id"))
    if not contact_id_raw.isdigit():
        return jsonify({"ok": False, "error": "contact_id is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    contact = conn.execute(
        """
        SELECT id, name, type, connection, chapter_id, vendor_id, status, notes, priority,
               value_estimate, expected_close_date, last_contact_at, follow_up_date, updated_at, created_at
        FROM crm_contacts
        WHERE id=? AND workspace_id=?
        """,
        (int(contact_id_raw), workspace_id),
    ).fetchone()
    if contact is None:
        return jsonify({"ok": False, "error": "contact not found"}), 404

    notes = conn.execute(
        """
        SELECT id, note, created_by_user_id, created_at
        FROM crm_notes
        WHERE crm_contact_id=? AND workspace_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 100
        """,
        (int(contact_id_raw), workspace_id),
    ).fetchall()
    tasks = conn.execute(
        """
        SELECT id, title, due_date, status, priority, completed_at, created_at
        FROM crm_tasks
        WHERE crm_contact_id=? AND workspace_id=?
        ORDER BY
          CASE WHEN lower(coalesce(status,''))='open' THEN 0 ELSE 1 END ASC,
          coalesce(due_date,'9999-12-31') ASC,
          id DESC
        LIMIT 200
        """,
        (int(contact_id_raw), workspace_id),
    ).fetchall()
    activity_rows = conn.execute(
        """
        SELECT id, action, detail, created_by_user_id, created_at
        FROM crm_activities
        WHERE crm_contact_id=? AND workspace_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 200
        """,
        (int(contact_id_raw), workspace_id),
    ).fetchall()
    tags = conn.execute(
        """
        SELECT t.id, t.name
        FROM crm_contact_tags ct
        JOIN crm_tags t ON t.id = ct.crm_tag_id
        WHERE ct.crm_contact_id=? AND ct.workspace_id=?
        ORDER BY t.name ASC
        """,
        (int(contact_id_raw), workspace_id),
    ).fetchall()

    return jsonify(
        {
            "ok": True,
            "contact": {k: contact[k] for k in contact.keys()},
            "notes": [{k: row[k] for k in row.keys()} for row in notes],
            "tasks": [{k: row[k] for k in row.keys()} for row in tasks],
            "timeline": [{k: row[k] for k in row.keys()} for row in activity_rows],
            "tags": [{k: row[k] for k in row.keys()} for row in tags],
        }
    )

@bp.route("/api/m/crm/note", methods=["POST"])
@login_required()
def api_m_crm_add_note():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    contact_id_raw = clean_text(payload.get("contact_id"))
    note = clean_text(payload.get("note"))
    if not contact_id_raw.isdigit():
        return jsonify({"ok": False, "error": "contact_id is required"}), 400
    if not note:
        return jsonify({"ok": False, "error": "note is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    contact = conn.execute(
        "SELECT id, name FROM crm_contacts WHERE id=? AND workspace_id=?",
        (int(contact_id_raw), workspace_id),
    ).fetchone()
    if contact is None:
        return jsonify({"ok": False, "error": "contact not found"}), 404

    cur = conn.execute(
        """
        INSERT INTO crm_notes(crm_contact_id, note, created_by_user_id, workspace_id)
        VALUES(?, ?, ?, ?)
        """,
        (int(contact_id_raw), note, int(user.get("id") or 0), workspace_id),
    )
    conn.execute(
        """
        INSERT INTO crm_activities(crm_contact_id, action, detail, created_by_user_id, workspace_id)
        VALUES(?, 'note_added', ?, ?, ?)
        """,
        (int(contact_id_raw), note[:240], int(user.get("id") or 0), workspace_id),
    )
    conn.execute("UPDATE crm_contacts SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND workspace_id=?", (int(contact_id_raw), workspace_id))
    log_activity(
        conn,
        int(user.get("id") or 0),
        "added_note",
        "crm_contact",
        contact_id_raw,
        clean_text(contact["name"]),
        workspace_id=workspace_id,
    )
    conn.commit()
    return jsonify({"ok": True, "id": int(cur.lastrowid)})

@bp.route("/api/m/crm/task", methods=["POST"])
@login_required()
def api_m_crm_add_task():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    contact_id_raw = clean_text(payload.get("contact_id"))
    title = clean_text(payload.get("title"))
    due_date = clean_date(payload.get("due_date"))
    priority = clean_text(payload.get("priority")).lower() or "normal"
    if priority not in {"low", "normal", "high"}:
        priority = "normal"
    if not contact_id_raw.isdigit():
        return jsonify({"ok": False, "error": "contact_id is required"}), 400
    if not title:
        return jsonify({"ok": False, "error": "title is required"}), 400
    if clean_text(payload.get("due_date")) and not due_date:
        return jsonify({"ok": False, "error": "due_date must be YYYY-MM-DD"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    contact = conn.execute(
        "SELECT id, name FROM crm_contacts WHERE id=? AND workspace_id=?",
        (int(contact_id_raw), workspace_id),
    ).fetchone()
    if contact is None:
        return jsonify({"ok": False, "error": "contact not found"}), 404

    cur = conn.execute(
        """
        INSERT INTO crm_tasks(crm_contact_id, title, due_date, status, priority, created_by_user_id, workspace_id)
        VALUES(?, ?, ?, 'open', ?, ?, ?)
        """,
        (int(contact_id_raw), title, due_date, priority, int(user.get("id") or 0), workspace_id),
    )
    conn.execute(
        """
        INSERT INTO crm_activities(crm_contact_id, action, detail, created_by_user_id, workspace_id)
        VALUES(?, 'task_added', ?, ?, ?)
        """,
        (int(contact_id_raw), f"{title} (due {due_date or 'unscheduled'})", int(user.get("id") or 0), workspace_id),
    )
    conn.execute("UPDATE crm_contacts SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND workspace_id=?", (int(contact_id_raw), workspace_id))
    log_activity(
        conn,
        int(user.get("id") or 0),
        "added_task",
        "crm_contact",
        contact_id_raw,
        clean_text(contact["name"]),
        workspace_id=workspace_id,
    )
    conn.commit()
    return jsonify({"ok": True, "id": int(cur.lastrowid)})

@bp.route("/api/m/crm/task/complete", methods=["POST"])
@login_required()
def api_m_crm_complete_task():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    task_id_raw = clean_text(payload.get("task_id"))
    complete_flag = bool(payload.get("completed", True))
    if not task_id_raw.isdigit():
        return jsonify({"ok": False, "error": "task_id is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    row = conn.execute(
        """
        SELECT id, crm_contact_id, title, status
        FROM crm_tasks
        WHERE id=? AND workspace_id=?
        """,
        (int(task_id_raw), workspace_id),
    ).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "task not found"}), 404

    new_status = "done" if complete_flag else "open"
    conn.execute(
        """
        UPDATE crm_tasks
        SET status=?, completed_at=CASE WHEN ?='done' THEN CURRENT_TIMESTAMP ELSE NULL END
        WHERE id=? AND workspace_id=?
        """,
        (new_status, new_status, int(task_id_raw), workspace_id),
    )
    conn.execute(
        """
        INSERT INTO crm_activities(crm_contact_id, action, detail, created_by_user_id, workspace_id)
        VALUES(?, ?, ?, ?, ?)
        """,
        (
            int(row["crm_contact_id"]),
            "task_completed" if new_status == "done" else "task_reopened",
            clean_text(row["title"]),
            int(user.get("id") or 0),
            workspace_id,
        ),
    )
    conn.execute(
        "UPDATE crm_contacts SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND workspace_id=?",
        (int(row["crm_contact_id"]), workspace_id),
    )
    conn.commit()
    return jsonify({"ok": True, "status": new_status})

@bp.route("/api/m/crm/tag", methods=["POST"])
@login_required()
def api_m_crm_tag_contact():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    contact_id_raw = clean_text(payload.get("contact_id"))
    tag_name = clean_text(payload.get("tag"))
    if not contact_id_raw.isdigit():
        return jsonify({"ok": False, "error": "contact_id is required"}), 400
    if not tag_name:
        return jsonify({"ok": False, "error": "tag is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    contact = conn.execute(
        "SELECT id FROM crm_contacts WHERE id=? AND workspace_id=?",
        (int(contact_id_raw), workspace_id),
    ).fetchone()
    if contact is None:
        return jsonify({"ok": False, "error": "contact not found"}), 404
    conn.execute(
        """
        INSERT INTO crm_tags(name, workspace_id)
        VALUES(?, ?)
        ON CONFLICT(workspace_id, name) DO NOTHING
        """,
        (tag_name, workspace_id),
    )
    tag = conn.execute(
        "SELECT id FROM crm_tags WHERE workspace_id=? AND name=?",
        (workspace_id, tag_name),
    ).fetchone()
    if tag is not None:
        conn.execute(
            """
            INSERT INTO crm_contact_tags(crm_contact_id, crm_tag_id, workspace_id)
            VALUES(?, ?, ?)
            ON CONFLICT(workspace_id, crm_contact_id, crm_tag_id) DO NOTHING
            """,
            (int(contact_id_raw), int(tag["id"]), workspace_id),
        )
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/m/crm/board")
@login_required()
def api_m_crm_board():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    contacts = conn.execute(
        """
        SELECT id, name, type, connection, status, follow_up_date, priority, updated_at, created_at
        FROM crm_contacts
        WHERE workspace_id=?
        ORDER BY updated_at DESC, created_at DESC, id DESC
        """,
        (workspace_id,),
    ).fetchall()
    stages = {
        "prospect": [],
        "contacted": [],
        "follow_up": [],
        "negotiating": [],
        "closed": [],
        "dormant": [],
    }
    for row in contacts:
        item = {k: row[k] for k in row.keys()}
        status = normalize_crm_status(item.get("status"))
        item["status"] = status
        item["is_overdue_follow_up"] = _is_overdue(item.get("follow_up_date"))
        stages.setdefault(status, []).append(item)

    task_rows = conn.execute(
        """
        SELECT id, crm_contact_id, title, due_date, status, priority, created_at
        FROM crm_tasks
        WHERE workspace_id=? AND lower(coalesce(status,''))='open'
        ORDER BY coalesce(due_date,'9999-12-31') ASC, id DESC
        LIMIT 400
        """,
        (workspace_id,),
    ).fetchall()
    tasks = [{k: row[k] for k in row.keys()} for row in task_rows]
    today = date.today().isoformat()
    overdue_count = sum(1 for task in tasks if _iso_date(task.get("due_date")) and clean_text(task.get("due_date")) < today)
    due_today_count = sum(1 for task in tasks if clean_text(task.get("due_date")) == today)

    return jsonify(
        {
            "ok": True,
            "stages": stages,
            "counts": {k: len(v) for k, v in stages.items()},
            "tasks": tasks,
            "kpis": {
                "total_contacts": len(contacts),
                "open_tasks": len(tasks),
                "overdue_tasks": overdue_count,
                "due_today_tasks": due_today_count,
            },
        }
    )

@bp.route("/api/m/crm/calendar")
@login_required()
def api_m_crm_calendar():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    rows = conn.execute(
        """
        SELECT
            t.id,
            t.crm_contact_id,
            t.title,
            t.due_date,
            t.status,
            t.priority,
            c.name AS contact_name,
            c.type AS contact_type
        FROM crm_tasks t
        JOIN crm_contacts c ON c.id=t.crm_contact_id
        WHERE t.workspace_id=?
          AND c.workspace_id=?
          AND trim(coalesce(t.due_date,''))<>''
        ORDER BY t.due_date ASC, t.id DESC
        LIMIT 1000
        """,
        (workspace_id, workspace_id),
    ).fetchall()
    return jsonify({"ok": True, "events": [{k: row[k] for k in row.keys()} for row in rows]})

@bp.route("/api/m/research", methods=["POST"])
@login_required()
def api_m_research():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    name = clean_text(payload.get("name"))
    kind = clean_text(payload.get("type")) or "entity"
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400
    qbase = quote(name)
    links = {
        "website": f"https://www.google.com/search?q={qbase}+official+website",
        "linkedin": f"https://www.google.com/search?q={qbase}+linkedin",
        "instagram": f"https://www.google.com/search?q={qbase}+instagram",
        "email": f"https://www.google.com/search?q={qbase}+contact+email",
    }
    conn = get_connection()
    ensure_crm_tables(conn)
    log_activity(
        conn,
        int(user.get("id") or 0),
        "researched_lead",
        kind,
        name,
        "Generated research links",
        workspace_id=workspace_id,
    )
    conn.commit()
    return jsonify({"ok": True, "summary": f"Research links generated for {name}", "links": links})

@bp.route("/agent/research", methods=["POST"])
@login_required()
def api_alias_agent_research():
    return api_m_research()

@bp.route("/api/m/messages/send", methods=["POST"])
@login_required()
def api_m_messages_send():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    to_email = clean_text(payload.get("to"))
    subject = clean_text(payload.get("subject"))
    body = clean_text(payload.get("message"))
    crm_contact_id_raw = clean_text(payload.get("crm_contact_id"))
    crm_contact_id = int(crm_contact_id_raw) if crm_contact_id_raw.isdigit() else None
    if not to_email or not subject or not body:
        return jsonify({"ok": False, "error": "to, subject and message are required"}), 400

    sent, err = send_email_best_effort(to_email, subject, body)
    status = "sent" if sent else "queued"
    sent_at = datetime.now().isoformat(timespec="seconds") if sent else None

    conn = get_connection()
    ensure_crm_tables(conn)
    manufacturer_id = _manufacturer_id_from_user(conn, user)
    msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    insert_cols = ["crm_contact_id", "to_email", "subject", "body", "status", "sent_at", "error", "workspace_id"]
    insert_vals = [crm_contact_id, to_email, subject, body, status, sent_at, err, workspace_id]
    if "manufacturer_id" in msg_cols:
        insert_cols.insert(0, "manufacturer_id")
        insert_vals.insert(0, manufacturer_id)
    placeholders = ",".join("?" for _ in insert_cols)
    cur = conn.execute(
        f"INSERT INTO messages({','.join(insert_cols)}) VALUES({placeholders})",
        tuple(insert_vals),
    )
    log_activity(
        conn,
        int(user.get("id") or 0),
        "sent_message" if sent else "queued_message",
        "message",
        str(cur.lastrowid),
        f"to={to_email}",
        workspace_id=workspace_id,
    )
    conn.commit()
    return jsonify({"ok": True, "status": status, "error": err})

@bp.route("/api/m/activity")
@login_required()
def api_m_activity():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    rows = conn.execute(
        """
        SELECT action, entity_type, entity_id, details, created_at
        FROM activities
        WHERE workspace_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 100
        """,
        (workspace_id,),
    ).fetchall()
    return jsonify({"ok": True, "rows": [{k: row[k] for k in row.keys()} for row in rows]})

@bp.route("/api/m/hot-leads")
@login_required()
def api_m_hot_leads():
    user = get_session_user()
    snapshot = manufacturer_dashboard_snapshot(user, hot_limit=50, activity_limit=0)
    return jsonify(
        {
            "ok": True,
            "hot_chapters": snapshot["hot_chapters"],
            "hot_vendors": snapshot["hot_vendors"],
        }
    )



@bp.route("/api/chapters")
@login_required()
def api_chapters():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first", "rows": []}), 400
    try:
        user = get_session_user()
        workspace_id = workspace_id_for_user(user)
        conn = get_connection()
        ensure_crm_tables(conn)

        crm_rows = conn.execute(
            """
            SELECT id, chapter_id, status, follow_up_date, priority, updated_at
            FROM crm_contacts
            WHERE type='chapter' AND workspace_id=?
            ORDER BY id DESC
            """,
            (workspace_id,),
        ).fetchall()
        crm_map = {}
        for r in crm_rows:
            cid = clean_text(r["chapter_id"])
            if not cid or cid in crm_map:
                continue
            crm_map[cid] = {
                "crm_contact_id": int(r["id"]),
                "crm_stage": normalize_crm_status(r["status"]),
                "follow_up_date": clean_text(r["follow_up_date"]),
                "priority": clean_text(r["priority"]) or "normal",
                "updated_at": clean_text(r["updated_at"]),
            }
        task_rows = conn.execute(
            """
            SELECT crm_contact_id, COUNT(*) AS c
            FROM crm_tasks
            WHERE workspace_id=? AND lower(coalesce(status,''))='open'
            GROUP BY crm_contact_id
            """,
            (workspace_id,),
        ).fetchall()
        task_map = {int(r["crm_contact_id"]): int(r["c"]) for r in task_rows}
        served_rows = conn.execute(
            "SELECT DISTINCT chapter_id FROM vendor_orders WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall()
        served_set = {clean_text(r["chapter_id"]) for r in served_rows if clean_text(r["chapter_id"])}

        rows = fetch_normalized_rows()
        out = []
        for r in rows:
            chapter_id = clean_text(r.get("id"))
            crm_info = crm_map.get(chapter_id, {})
            crm_status = clean_text(crm_info.get("crm_stage"))
            contact_id = int(crm_info.get("crm_contact_id") or 0)
            item = dict(r)
            item["in_crm"] = bool(contact_id)
            item["crm_contact_id"] = contact_id or None
            item["crm_status"] = crm_status
            item["crm_stage"] = crm_status
            item["follow_up_date"] = clean_text(crm_info.get("follow_up_date"))
            item["priority"] = clean_text(crm_info.get("priority")) or "normal"
            item["open_task_count"] = int(task_map.get(contact_id, 0)) if contact_id else 0
            item["updated_at"] = clean_text(crm_info.get("updated_at"))
            item["served"] = chapter_id in served_set or crm_status == "closed"
            out.append(item)
        return jsonify({"ok": True, "rows": out})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "rows": []}), 500

@bp.route("/api/vendors")
@login_required()
def api_vendors():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first", "rows": []}), 400
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_vendor_table(conn)
    crm_vendor_rows = conn.execute(
        """
        SELECT id, lower(name) AS vendor_name, status, follow_up_date, priority, updated_at
        FROM crm_contacts
        WHERE type='vendor' AND workspace_id=?
        ORDER BY id DESC
        """,
        (workspace_id,),
    ).fetchall()
    crm_vendor_map = {}
    for r in crm_vendor_rows:
        vendor_name = clean_text(r["vendor_name"])
        if not vendor_name or vendor_name in crm_vendor_map:
            continue
        crm_vendor_map[vendor_name] = {
            "crm_contact_id": int(r["id"]),
            "crm_stage": normalize_crm_status(r["status"]),
            "follow_up_date": clean_text(r["follow_up_date"]),
            "priority": clean_text(r["priority"]) or "normal",
            "updated_at": clean_text(r["updated_at"]),
        }
    task_rows = conn.execute(
        """
        SELECT crm_contact_id, COUNT(*) AS c
        FROM crm_tasks
        WHERE workspace_id=? AND lower(coalesce(status,''))='open'
        GROUP BY crm_contact_id
        """,
        (workspace_id,),
    ).fetchall()
    task_map = {int(r["crm_contact_id"]): int(r["c"]) for r in task_rows}
    served_vendor_rows = conn.execute(
        "SELECT DISTINCT lower(vendor) AS vendor_name FROM vendor_orders WHERE workspace_id=? AND trim(coalesce(vendor,''))<>''",
        (workspace_id,),
    ).fetchall()
    served_vendor_set = {clean_text(r["vendor_name"]) for r in served_vendor_rows if clean_text(r["vendor_name"])}
    rows = conn.execute(
        """
        SELECT id, vendor, organization, category, state, city, website, email
        FROM vendors
        ORDER BY vendor ASC
        """
    ).fetchall()
    out = []
    for row in rows:
        item = {k: row[k] for k in row.keys()}
        vnorm = clean_text(item.get("vendor")).lower()
        crm_info = crm_vendor_map.get(vnorm, {})
        crm_status = clean_text(crm_info.get("crm_stage"))
        contact_id = int(crm_info.get("crm_contact_id") or 0)
        item["in_crm"] = bool(contact_id)
        item["crm_contact_id"] = contact_id or None
        item["crm_status"] = crm_status
        item["crm_stage"] = crm_status
        item["follow_up_date"] = clean_text(crm_info.get("follow_up_date"))
        item["priority"] = clean_text(crm_info.get("priority")) or "normal"
        item["open_task_count"] = int(task_map.get(contact_id, 0)) if contact_id else 0
        item["updated_at"] = clean_text(crm_info.get("updated_at"))
        item["served"] = vnorm in served_vendor_set or crm_status == "closed"
        out.append(item)
    return jsonify({"ok": True, "rows": out})

@bp.route("/api/leads/add", methods=["POST"])
@login_required()
def api_add_lead():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    chapter_id = clean_text(payload.get("chapter_id"))
    if not chapter_id:
        return jsonify({"ok": False, "error": "chapter_id is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO leads (chapter_id, org, chapter_name, school, city, state, status, notes, follow_up_date, workspace_id)
        VALUES (?, ?, ?, ?, ?, ?, 'prospect', ?, ?, ?)
        """,
        (
            chapter_id,
            clean_text(payload.get("org")),
            clean_text(payload.get("chapter_name")),
            clean_text(payload.get("school")),
            clean_text(payload.get("city")),
            clean_text(payload.get("state")),
            clean_text(payload.get("notes")),
            clean_date(payload.get("follow_up_date")),
            workspace_id,
        ),
    )
    log_lead_activity(conn, int(cur.lastrowid), "created", "Lead created in prospect stage", workspace_id=workspace_id)
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/leads/update-status", methods=["POST"])
@login_required()
def api_update_lead_status():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    lead_id_raw = clean_text(payload.get("lead_id"))
    status = clean_text(payload.get("status")).lower()
    if not lead_id_raw.isdigit():
        return jsonify({"ok": False, "error": "lead_id is required"}), 400
    if status not in Config.LEAD_STAGES:
        return jsonify({"ok": False, "error": "invalid status"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    existing = conn.execute(
        "SELECT status FROM leads WHERE id=? AND workspace_id=?",
        (int(lead_id_raw), workspace_id),
    ).fetchone()
    if existing is None:
        return jsonify({"ok": False, "error": "lead not found"}), 404
    previous = clean_text(existing["status"]).lower()
    cur = conn.execute(
        "UPDATE leads SET status=? WHERE id=? AND workspace_id=?",
        (status, int(lead_id_raw), workspace_id),
    )
    if previous != status:
        log_lead_activity(conn, int(lead_id_raw), "status_changed", f"{previous or 'unknown'} -> {status}", workspace_id=workspace_id)
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/leads/delete", methods=["POST"])
@login_required()
def api_delete_lead():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    lead_id_raw = clean_text(payload.get("lead_id"))
    if not lead_id_raw.isdigit():
        return jsonify({"ok": False, "error": "lead_id is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    existing = conn.execute(
        "SELECT org, chapter_name FROM leads WHERE id=? AND workspace_id=?",
        (int(lead_id_raw), workspace_id),
    ).fetchone()
    if existing is None:
        return jsonify({"ok": False, "error": "lead not found"}), 404
    log_lead_activity(
        conn,
        int(lead_id_raw),
        "deleted",
        f"{clean_text(existing['org'])} - {clean_text(existing['chapter_name'])}",
        workspace_id=workspace_id,
    )
    cur = conn.execute("DELETE FROM leads WHERE id=? AND workspace_id=?", (int(lead_id_raw), workspace_id))
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/leads/bulk-update-status", methods=["POST"])
@login_required()
def api_bulk_update_lead_status():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    lead_ids = payload.get("lead_ids") or []
    status = clean_text(payload.get("status")).lower()
    if status not in Config.LEAD_STAGES:
        return jsonify({"ok": False, "error": "invalid status"}), 400
    if not isinstance(lead_ids, list):
        return jsonify({"ok": False, "error": "lead_ids must be a list"}), 400

    clean_ids = []
    for value in lead_ids:
        raw = clean_text(value)
        if raw.isdigit():
            clean_ids.append(int(raw))
    clean_ids = sorted(set(clean_ids))
    if not clean_ids:
        return jsonify({"ok": False, "error": "at least one valid lead_id is required"}), 400

    placeholders = ",".join("?" for _ in clean_ids)
    conn = get_connection()
    ensure_crm_tables(conn)
    previous_rows = conn.execute(
        f"SELECT id, status FROM leads WHERE workspace_id=? AND id IN ({placeholders})",
        tuple([workspace_id] + clean_ids),
    ).fetchall()
    previous_map = {int(r["id"]): clean_text(r["status"]).lower() for r in previous_rows}
    cur = conn.execute(
        f"UPDATE leads SET status=? WHERE workspace_id=? AND id IN ({placeholders})",
        tuple([status, workspace_id] + clean_ids),
    )
    for lead_id in clean_ids:
        old = previous_map.get(lead_id)
        if old and old != status:
            log_lead_activity(conn, lead_id, "status_changed", f"{old} -> {status}", workspace_id=workspace_id)
    conn.commit()
    return jsonify({"ok": True, "updated": int(cur.rowcount)})

@bp.route("/api/leads/bulk-delete", methods=["POST"])
@login_required()
def api_bulk_delete_leads():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    lead_ids = payload.get("lead_ids") or []
    if not isinstance(lead_ids, list):
        return jsonify({"ok": False, "error": "lead_ids must be a list"}), 400

    clean_ids = []
    for value in lead_ids:
        raw = clean_text(value)
        if raw.isdigit():
            clean_ids.append(int(raw))
    clean_ids = sorted(set(clean_ids))
    if not clean_ids:
        return jsonify({"ok": False, "error": "at least one valid lead_id is required"}), 400

    placeholders = ",".join("?" for _ in clean_ids)
    conn = get_connection()
    ensure_crm_tables(conn)
    existing_rows = conn.execute(
        f"SELECT id, org, chapter_name FROM leads WHERE workspace_id=? AND id IN ({placeholders})",
        tuple([workspace_id] + clean_ids),
    ).fetchall()
    for row in existing_rows:
        log_lead_activity(
            conn,
            int(row["id"]),
            "deleted",
            f"{clean_text(row['org'])} - {clean_text(row['chapter_name'])}",
            workspace_id=workspace_id,
        )
    cur = conn.execute(
        f"DELETE FROM leads WHERE workspace_id=? AND id IN ({placeholders})",
        tuple([workspace_id] + clean_ids),
    )
    conn.commit()
    return jsonify({"ok": True, "deleted": int(cur.rowcount)})

@bp.route("/api/leads/update-details", methods=["POST"])
@login_required()
def api_update_lead_details():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    lead_id_raw = clean_text(payload.get("lead_id"))
    if not lead_id_raw.isdigit():
        return jsonify({"ok": False, "error": "lead_id is required"}), 400

    notes = clean_text(payload.get("notes"))
    follow_up_date = clean_date(payload.get("follow_up_date"))
    if clean_text(payload.get("follow_up_date")) and not follow_up_date:
        return jsonify({"ok": False, "error": "follow_up_date must be YYYY-MM-DD"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    existing = conn.execute(
        "SELECT notes, follow_up_date FROM leads WHERE id=? AND workspace_id=?",
        (int(lead_id_raw), workspace_id),
    ).fetchone()
    if existing is None:
        return jsonify({"ok": False, "error": "lead not found"}), 404

    conn.execute(
        "UPDATE leads SET notes=?, follow_up_date=? WHERE id=? AND workspace_id=?",
        (notes, follow_up_date, int(lead_id_raw), workspace_id),
    )
    previous_notes = clean_text(existing["notes"])
    previous_follow = clean_text(existing["follow_up_date"])
    if previous_notes != notes or previous_follow != follow_up_date:
        detail = f"follow_up: {previous_follow or 'none'} -> {follow_up_date or 'none'}"
        log_lead_activity(conn, int(lead_id_raw), "details_updated", detail, workspace_id=workspace_id)
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/leads/timeline")
@login_required()
def api_lead_timeline():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first", "events": []}), 400

    lead_id_raw = clean_text(request.args.get("lead_id"))
    if not lead_id_raw.isdigit():
        return jsonify({"ok": False, "error": "lead_id is required", "events": []}), 400

    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    rows = conn.execute(
        """
        SELECT id, lead_id, action, details, created_at
        FROM lead_activities
        WHERE lead_id = ? AND workspace_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 100
        """,
        (int(lead_id_raw), workspace_id),
    ).fetchall()
    events = [{k: row[k] for k in row.keys()} for row in rows]
    return jsonify({"ok": True, "events": events})

@bp.route("/api/orders/mark-served", methods=["POST"])
@login_required()
def api_mark_served():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    chapter_id = clean_text(payload.get("chapter_id"))
    if not chapter_id:
        return jsonify({"ok": False, "error": "chapter_id is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    conn.execute(
        """
        INSERT INTO vendor_orders (vendor, chapter_id, org, chapter_name, school, city, state, year, product, quantity, notes, workspace_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_text(payload.get("vendor")) or "My Vendor",
            chapter_id,
            clean_text(payload.get("org")),
            clean_text(payload.get("chapter_name")),
            clean_text(payload.get("school")),
            clean_text(payload.get("city")),
            clean_text(payload.get("state")),
            int(clean_text(payload.get("year")) or 0) or None,
            clean_text(payload.get("product")) or "General",
            int(clean_text(payload.get("quantity")) or 0) or None,
            clean_text(payload.get("notes")),
            workspace_id,
        ),
    )
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/orders/by-chapter")
@login_required()
def api_orders_by_chapter():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first", "orders": []}), 400

    chapter_id = clean_text(request.args.get("chapter_id"))
    if not chapter_id:
        return jsonify({"ok": True, "orders": []})

    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    rows = conn.execute(
        """
        SELECT id, vendor, chapter_id, org, chapter_name, school, city, state, year, product, quantity, notes, created_at
        FROM vendor_orders
        WHERE chapter_id = ? AND workspace_id = ?
        ORDER BY created_at DESC
        LIMIT 100
        """,
        (chapter_id, workspace_id),
    ).fetchall()
    orders = [{k: row[k] for k in row.keys()} for row in rows]
    return jsonify({"ok": True, "orders": orders})

@bp.route("/api/chapters/campus")
@login_required()
def api_chapters_campus():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first", "rows": []}), 400

    school = clean_text(request.args.get("school"))
    exclude_id = clean_text(request.args.get("exclude_id"))
    if not school:
        return jsonify({"ok": True, "rows": []})

    rows = fetch_normalized_rows()
    campus = [r for r in rows if clean_text(r.get("school")) == school and clean_text(r.get("id")) != exclude_id]
    trimmed = [
        {
            "id": r["id"],
            "orgCode": r.get("orgCode", ""),
            "orgName": r.get("orgName", ""),
            "chapterName": r.get("chapterName", ""),
            "status": r.get("status", ""),
            "vendorCount": r.get("vendorCount", 0),
        }
        for r in campus[:30]
    ]
    return jsonify({"ok": True, "rows": trimmed})

@bp.route("/api/views", methods=["GET", "POST"])
@login_required()
def api_saved_views():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)

    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, name, filters_json, created_at FROM saved_views WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
        prefix = f"{workspace_id}::"
        views = [
            {
                "id": r["id"],
                "name": clean_text(r["name"])[len(prefix):] if clean_text(r["name"]).startswith(prefix) else clean_text(r["name"]),
                "filters": json.loads(r["filters_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return jsonify({"ok": True, "views": views})

    payload = request.get_json(silent=True) or {}
    name = clean_text(payload.get("name"))
    scoped_name = f"{workspace_id}::{name}"
    filters = payload.get("filters", {})
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400

    filters_json = json.dumps(filters)
    existing = conn.execute(
        "SELECT id FROM saved_views WHERE workspace_id=? AND name=?",
        (workspace_id, scoped_name),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE saved_views SET filters_json=? WHERE id=?",
            (filters_json, int(existing["id"])),
        )
    else:
        conn.execute(
            "INSERT INTO saved_views(name, filters_json, workspace_id) VALUES(?, ?, ?)",
            (scoped_name, filters_json, workspace_id),
        )
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/leads/export")
@login_required()
def api_export_leads():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)
    rows = conn.execute(
        """
        SELECT chapter_id, org, chapter_name, school, city, state, status, follow_up_date, notes
        FROM leads
        WHERE workspace_id = ?
        ORDER BY created_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    header = ["org", "chapter", "school", "city", "state", "status", "follow_up_date", "notes", "search_link"]
    lines = [",".join(header)]
    for r in rows:
        org = clean_text(r["org"])
        chapter = clean_text(r["chapter_name"])
        school = clean_text(r["school"])
        city = clean_text(r["city"])
        state = clean_text(r["state"])
        status = clean_text(r["status"])
        follow_up_date = clean_text(r["follow_up_date"])
        notes = clean_text(r["notes"])
        q = f"{org} {chapter} {school} {state} president instagram"
        search_link = f"https://www.google.com/search?q={q.replace(' ', '+')}"

        vals = [org, chapter, school, city, state, status, follow_up_date, notes, search_link]
        escaped = []
        for v in vals:
            v = str(v or "")
            if any(ch in v for ch in [",", "\"", "\n"]):
                v = '"' + v.replace('"', '""') + '"'
            escaped.append(v)
        lines.append(",".join(escaped))

    csv_text = "\n".join(lines)
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"},
    )

@bp.route("/api/contacts", methods=["GET", "POST"])
@login_required()
def api_contacts():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_connection()
    ensure_crm_tables(conn)

    if request.method == "GET":
        chapter_id = clean_text(request.args.get("chapter_id"))
        if not chapter_id:
            return jsonify({"ok": True, "contacts": []})
        rows = conn.execute(
            """
            SELECT id, chapter_id, contact_name, role, instagram, email, notes, created_at
            FROM chapter_contacts
            WHERE chapter_id = ? AND workspace_id = ?
            ORDER BY created_at DESC
            """,
            (chapter_id, workspace_id),
        ).fetchall()
        contacts = [{k: row[k] for k in row.keys()} for row in rows]
        return jsonify({"ok": True, "contacts": contacts})

    payload = request.get_json(silent=True) or {}
    chapter_id = clean_text(payload.get("chapter_id"))
    if not chapter_id:
        return jsonify({"ok": False, "error": "chapter_id is required"}), 400

    conn.execute(
        """
        INSERT INTO chapter_contacts (chapter_id, contact_name, role, instagram, email, notes, workspace_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chapter_id,
            clean_text(payload.get("contact_name")),
            clean_text(payload.get("role")),
            clean_text(payload.get("instagram")),
            clean_text(payload.get("email")),
            clean_text(payload.get("notes")),
            workspace_id,
        ),
    )
    conn.commit()
    return jsonify({"ok": True})

@bp.route("/api/contacts/delete", methods=["POST"])
@login_required()
def api_contacts_delete():
    if not os.path.exists(Config.DB_PATH):
        return jsonify({"ok": False, "error": "Run import_csv.py first"}), 400

    payload = request.get_json(silent=True) or {}
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    contact_id_raw = clean_text(payload.get("contact_id"))
    if not contact_id_raw.isdigit():
        return jsonify({"ok": False, "error": "contact_id is required"}), 400

    conn = get_connection()
    ensure_crm_tables(conn)
    cur = conn.execute(
        "DELETE FROM chapter_contacts WHERE id=? AND workspace_id=?",
        (int(contact_id_raw), workspace_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return jsonify({"ok": False, "error": "contact not found"}), 404
    return jsonify({"ok": True})

@bp.route("/api/message-template")
@login_required()
def api_message_template():
    org = clean_text(request.args.get("org"))
    school = clean_text(request.args.get("school"))
    chapter = clean_text(request.args.get("chapter"))
    peer_org = clean_text(request.args.get("peer_org"))

    if not org or not chapter:
        return jsonify({"ok": False, "error": "org and chapter are required"}), 400

    peer_line = f"We recently produced custom apparel for {peer_org} at {school}." if peer_org and school else ""
    message = (
        f"Hi! {peer_line} "
        f"We would love to create something for {org} - {chapter} as well. "
        "Would your chapter be interested?"
    ).strip()
    message = re.sub(r"\s+", " ", message)
    return jsonify({"ok": True, "message": message})
