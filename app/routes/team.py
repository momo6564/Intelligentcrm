from flask import Blueprint, render_template, redirect, url_for
from ..auth import login_required, get_session_user

bp = Blueprint("team", __name__)

def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)

@bp.route("/team")
@login_required()
def team_setup_page():
    return render_app("team/setup.html")

@bp.route("/team/dashboard")
@login_required()
def team_dashboard_page():
    user = get_session_user()
    if not user.get("team_id"):
        return redirect(url_for("team.team_setup_page"))
    return render_app("team/dashboard.html")
