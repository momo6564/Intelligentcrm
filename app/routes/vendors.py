from datetime import datetime
from flask import Blueprint, request, render_template, redirect, url_for
from urllib.parse import quote
import re
from ..auth import login_required, get_session_user
from ..database import get_connection, ensure_crm_tables, ensure_vendor_table
from ..research_memory import research_placeholder_hints
from ..utils.text_utils import clean_text
from ..utils.workspace import workspace_id_for_user

bp = Blueprint('vendors', __name__)

def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)

@bp.route("/vendors")
@login_required()
def vendors_page():
    return render_app("explorer/vendors.html")

@bp.route("/vendors/detail")
@login_required()
def vendor_detail_page():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    vendor_id_raw = clean_text(request.args.get("vendor_id"))
    vendor_name = clean_text(request.args.get("vendor_name"))

    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_vendor_table(conn)

    row = None
    if vendor_id_raw.isdigit():
        row = conn.execute(
            """
            SELECT id, vendor, organization, category, state, city, website, email, phone, instagram_url,
                   license_label, is_greek_licensed, is_collegiate
            FROM vendors
            WHERE id=?
            """,
            (int(vendor_id_raw),),
        ).fetchone()
    if row is None and vendor_name:
        row = conn.execute(
            """
            SELECT id, vendor, organization, category, state, city, website, email, phone, instagram_url,
                   license_label, is_greek_licensed, is_collegiate
            FROM vendors
            WHERE lower(vendor)=lower(?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (vendor_name,),
        ).fetchone()

    vendor = {k: row[k] for k in row.keys()} if row else {}
    if not vendor and vendor_name:
        vendor = {
            "id": None,
            "vendor": vendor_name,
            "organization": "",
            "category": "",
            "state": "",
            "city": "",
            "website": "",
            "email": "",
            "phone": "",
            "instagram_url": "",
            "license_label": "",
            "is_greek_licensed": 0,
            "is_collegiate": 0,
        }

    lookup_name = clean_text(vendor.get("vendor")) or vendor_name
    raw_instagram = clean_text(vendor.get("instagram_url"))
    vendor_instagram_url = ""
    vendor_instagram_label = ""
    instagram_handle = raw_instagram.lstrip("@") if raw_instagram and not raw_instagram.lower().startswith("http") else raw_instagram
    if instagram_handle and not instagram_handle.lower().startswith("http"):
        vendor_instagram_url = f"https://instagram.com/{instagram_handle}"
        vendor_instagram_label = f"@{instagram_handle}"
    else:
        vendor_instagram_url = raw_instagram
        vendor_instagram_label = raw_instagram
    served_rows = []
    my_status = ""
    crm_contact = {}
    research_query = ""
    licensed_orgs = []
    if lookup_name:
        rows = conn.execute(
            """
            SELECT chapter_id, chapter_name, org, school, city, state, created_at
            FROM vendor_orders
            WHERE workspace_id=? AND lower(vendor)=lower(?)
            ORDER BY created_at DESC, id DESC
            LIMIT 100
            """,
            (workspace_id, lookup_name),
        ).fetchall()
        for r in rows:
            chapter_id = clean_text(r["chapter_id"])
            served_rows.append(
                {
                    "chapter_id": chapter_id,
                    "encodedId": quote(chapter_id, safe="") if chapter_id else "",
                    "chapter_name": clean_text(r["chapter_name"]),
                    "org": clean_text(r["org"]),
                    "school": clean_text(r["school"]),
                    "city": clean_text(r["city"]),
                    "state": clean_text(r["state"]),
                    "created_at": clean_text(r["created_at"]),
                }
            )
        crm_row = conn.execute(
            """
            SELECT id, status, notes, follow_up_date, priority, value_estimate, expected_close_date
            FROM crm_contacts
            WHERE workspace_id=? AND type='vendor' AND lower(name)=lower(?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (workspace_id, lookup_name),
        ).fetchone()
        served_exists = conn.execute(
            "SELECT 1 FROM vendor_orders WHERE workspace_id=? AND lower(vendor)=lower(?) LIMIT 1",
            (workspace_id, lookup_name),
        ).fetchone()
        crm_status = clean_text(crm_row["status"]).lower() if crm_row else ""
        my_status = "served" if (served_exists is not None or crm_status == "closed") else ("prospect" if crm_row else "")
        crm_contact = {
            "id": int(crm_row["id"]) if crm_row else None,
            "status": crm_status if crm_row else "",
            "notes": clean_text(crm_row["notes"]) if crm_row else "",
            "follow_up_date": clean_text(crm_row["follow_up_date"]) if crm_row else "",
            "priority": clean_text(crm_row["priority"]) if crm_row else "normal",
            "value_estimate": crm_row["value_estimate"] if crm_row and crm_row["value_estimate"] is not None else "",
            "expected_close_date": clean_text(crm_row["expected_close_date"]) if crm_row else "",
        }

        org_rows = conn.execute(
            """
            SELECT DISTINCT organization
            FROM vendors
            WHERE lower(vendor)=lower(?) AND trim(coalesce(organization,''))<>''
            ORDER BY organization ASC
            """,
            (lookup_name,),
        ).fetchall()
        org_set = set()
        for r in org_rows:
            raw = clean_text(r["organization"])
            if not raw:
                continue
            parts = [clean_text(p) for p in re.split(r",|/|&| and ", raw, flags=re.I) if clean_text(p)]
            if parts:
                org_set.update(parts)
            else:
                org_set.add(raw)
        if not org_set and clean_text(vendor.get("organization")):
            org_set.add(clean_text(vendor.get("organization")))
        licensed_orgs = sorted(org_set)
        org_text = ", ".join(licensed_orgs) if licensed_orgs else "Unknown"
        research_query = org_text

    error = "" if vendor else "Vendor not found."
    return render_app(
        "explorer/vendor_detail.html",
        vendor=vendor,
        vendor_instagram_url=vendor_instagram_url,
        vendor_instagram_label=vendor_instagram_label,
        served_chapters=served_rows,
        my_status=my_status,
        crm_contact=crm_contact,
        licensed_orgs=licensed_orgs,
        research_url="",
        research_query=research_query,
        research_entity_data={
            "vendor_name": lookup_name,
            "organization": clean_text(vendor.get("organization")),
            "category": clean_text(vendor.get("category")),
            "city": clean_text(vendor.get("city")),
            "state": clean_text(vendor.get("state")),
            "licensed_organizations": ", ".join(licensed_orgs) if licensed_orgs else "",
        },
        research_placeholder_hints=research_placeholder_hints("vendor"),
        error=error,
    )

@bp.route("/crm")
@login_required()
def crm_page():
    # Will be a unified CRM view
    return render_app("crm/crm.html")

@bp.route("/ui/vendor-drawer")
@login_required()
def vendor_drawer_partial():
    vendor_id_raw = clean_text(request.args.get("vendor_id"))
    if not vendor_id_raw.isdigit():
        return render_template("components/vendor_drawer.html", vendor={}, me=get_session_user())
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_vendor_table(conn)
    row = conn.execute(
        """
        SELECT id, vendor, organization, category, state, city, website, email, phone, instagram_url,
               license_label, is_greek_licensed, is_collegiate
        FROM vendors
        WHERE id=?
        """,
        (int(vendor_id_raw),),
    ).fetchone()
    vendor = {k: row[k] for k in row.keys()} if row else {}
    return render_template("components/vendor_drawer.html", vendor=vendor, me=get_session_user())

# Legacy redirects
@bp.route("/competitors")
@login_required()
def competitors_page():
    return redirect(url_for("vendors.crm_page"))

@bp.route("/vendor")
@login_required()
def vendor_portal_page():
    return redirect(url_for("main.dashboard_page"))

@bp.route("/manufacturer")
@login_required()
def manufacturer_portal_page():
    return redirect(url_for("main.dashboard_page"))

@bp.route("/m/dashboard")
@login_required()
def m_dashboard_page():
    return redirect(url_for("main.dashboard_page"))

@bp.route("/m/chapters")
@login_required()
def m_chapters_page():
    return redirect(url_for("chapters.chapters_page"))

@bp.route("/m/vendors")
@login_required()
def m_vendors_page():
    return redirect(url_for("vendors.vendors_page"))

@bp.route("/m/crm")
@login_required()
def m_crm_page():
    return redirect(url_for("vendors.crm_page"))
