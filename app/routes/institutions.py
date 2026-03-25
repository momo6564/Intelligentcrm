from flask import Blueprint, request, render_template
from ..auth import login_required, get_session_user
from ..database import get_connection, ensure_institutions_table, ensure_chapters_table, ensure_crm_tables
from ..utils.text_utils import clean_text
from ..utils.workspace import workspace_id_for_user

bp = Blueprint("institutions", __name__)


def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)


@bp.route("/institutions")
@login_required()
def institutions_page():
    return render_app("explorer/institutions.html")


@bp.route("/institutions/detail")
@login_required()
def institution_detail_page():
    inst_id_raw = clean_text(request.args.get("institution_id"))
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_institutions_table(conn)
    ensure_chapters_table(conn)

    row = None
    if inst_id_raw.isdigit():
        row = conn.execute(
            """
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
            """,
            (int(inst_id_raw),),
        ).fetchone()

    institution = {k: row[k] for k in row.keys()} if row else {}
    chapters = []
    if institution:
        chapters = conn.execute(
            """
            SELECT chapter_uid, chapter_name, organization, city, state, status
            FROM chapters
            WHERE institution_id=?
            ORDER BY organization ASC, chapter_name ASC
            LIMIT 200
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
                LIMIT 200
                """,
                (clean_text(institution.get("location_name")),),
            ).fetchall()
    my_status = ""
    if institution:
        user = get_session_user()
        workspace_id = workspace_id_for_user(user)
        connection = f"institution:{institution.get('id')}"
        crm_row = conn.execute(
            """
            SELECT id, status
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
    error = "" if institution else "Institution not found."
    return render_app(
        "explorer/institution_detail.html",
        institution=institution,
        chapters=[{k: row[k] for k in row.keys()} for row in chapters],
        my_status=my_status,
        error=error,
    )


@bp.route("/ui/institution-drawer")
@login_required()
def institution_drawer_partial():
    inst_id_raw = clean_text(request.args.get("institution_id"))
    if not inst_id_raw.isdigit():
        return render_template("components/institution_drawer.html", institution={}, me=get_session_user())
    conn = get_connection()
    ensure_institutions_table(conn)
    ensure_chapters_table(conn)
    row = conn.execute(
        """
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
        """,
        (int(inst_id_raw),),
    ).fetchone()
    institution = {k: row[k] for k in row.keys()} if row else {}
    if institution:
        chapter_count = conn.execute(
            "SELECT COUNT(*) AS c FROM chapters WHERE institution_id=?",
            (int(institution["id"]),),
        ).fetchone()
        count = int(chapter_count["c"] or 0) if chapter_count else 0
        if count == 0 and clean_text(institution.get("location_name")):
            chapter_count = conn.execute(
                "SELECT COUNT(*) AS c FROM chapters WHERE school=?",
                (clean_text(institution.get("location_name")),),
            ).fetchone()
            count = int(chapter_count["c"] or 0) if chapter_count else 0
        institution["chapter_count"] = count
    return render_template("components/institution_drawer.html", institution=institution, me=get_session_user())
