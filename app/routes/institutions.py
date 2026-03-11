from flask import Blueprint, request, render_template
from ..auth import login_required, get_session_user
from ..database import get_connection, ensure_institutions_table
from ..utils.text_utils import clean_text

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
    ensure_institutions_table(conn)

    row = None
    if inst_id_raw.isdigit():
        row = conn.execute(
            """
            SELECT id, location_name, parent_name, location_type, address, street, city, state, zip,
                   general_phone, admin_name, admin_phone, admin_email, fax, update_date,
                   dapip_id, ope_id, ipeds_unit_ids, parent_dapip_id
            FROM institutions
            WHERE id=?
            """,
            (int(inst_id_raw),),
        ).fetchone()

    institution = {k: row[k] for k in row.keys()} if row else {}
    error = "" if institution else "Institution not found."
    return render_app(
        "explorer/institution_detail.html",
        institution=institution,
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
    row = conn.execute(
        """
        SELECT id, location_name, parent_name, location_type, address, street, city, state, zip,
               general_phone, admin_name, admin_phone, admin_email, fax, update_date,
               dapip_id, ope_id, ipeds_unit_ids, parent_dapip_id
        FROM institutions
        WHERE id=?
        """,
        (int(inst_id_raw),),
    ).fetchone()
    institution = {k: row[k] for k in row.keys()} if row else {}
    return render_template("components/institution_drawer.html", institution=institution, me=get_session_user())
