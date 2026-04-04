from flask import Blueprint, request, render_template, redirect
from ..auth import login_required, get_session_user
from ..database import get_connection, ensure_institutions_table, ensure_chapters_table, ensure_crm_tables
from ..research_memory import research_placeholder_hints
from ..services.institutions import fetch_institution_profile
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


@bp.route("/institutions/map")
@login_required()
def institutions_map_page():
    query = request.query_string.decode("utf-8")
    target = "/institutions/detail"
    if query:
        target = f"{target}?{query}"
    return redirect(target)


@bp.route("/institutions/detail")
@login_required()
def institution_detail_page():
    inst_id_raw = clean_text(request.args.get("institution_id"))
    institution = {}
    chapters = []
    my_status = ""
    error = ""
    if inst_id_raw:
        conn = get_connection()
        ensure_crm_tables(conn)
        ensure_institutions_table(conn)
        ensure_chapters_table(conn)
        if inst_id_raw.isdigit():
            user = get_session_user()
            workspace_id = workspace_id_for_user(user)
            institution, chapters, my_status = fetch_institution_profile(conn, int(inst_id_raw), workspace_id=workspace_id)
            if not institution:
                error = "Institution not found."
        else:
            error = "Institution not found."
    return render_app(
        "explorer/institution_detail.html",
        institution=institution,
        chapters=chapters,
        my_status=my_status,
        error=error,
        research_placeholder_hints=research_placeholder_hints("institution"),
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
    institution, _chapters, _my_status = fetch_institution_profile(conn, int(inst_id_raw))
    return render_template("components/institution_drawer.html", institution=institution, me=get_session_user())
