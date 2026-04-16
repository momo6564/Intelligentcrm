import os
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, send_from_directory

from ..auth import get_session_user
from ..database import get_connection, ensure_crm_tables, ensure_default_users, derive_workspace_id, log_activity
from ..config import Config
from ..utils.passwords import hash_password
from ..utils.text_utils import clean_text

bp = Blueprint("admin", __name__)


def admin_required():
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = get_session_user()
            if not user:
                return redirect(url_for("auth.login_page", next=request.path))
            role = clean_text(user.get("role")).lower()
            if role != "admin":
                return redirect(url_for("main.dashboard_page"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def render_admin(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)


@bp.route("/admin")
@admin_required()
def admin_dashboard():
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_default_users(conn)
    me = get_session_user()

    feedback = conn.execute(
        """
        SELECT f.id, f.message, f.page_url, f.page_title, f.image_path, f.image_name,
               f.created_at, u.username, u.email, u.account_name, u.role
        FROM feedback_messages f
        LEFT JOIN users u ON u.id = f.user_id
        ORDER BY f.created_at DESC
        LIMIT 200
        """
    ).fetchall()
    users = conn.execute(
        """
        SELECT id, username, email, account_name, role, workspace_id,
               team_id, team_role, created_at
        FROM users
        ORDER BY id DESC
        LIMIT 200
        """
    ).fetchall()
    activity_rows = conn.execute(
        """
        SELECT a.id, a.action, a.entity_type, a.entity_id, a.details, a.workspace_id, a.created_at,
               u.id AS actor_user_id, u.username, u.email, u.account_name, u.role
        FROM activities a
        LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 250
        """
    ).fetchall()
    error = clean_text(request.args.get("error"))
    notice = clean_text(request.args.get("notice"))
    return render_admin(
        "admin/dashboard.html",
        me=me,
        feedback=feedback,
        users=users,
        activity_rows=activity_rows,
        error=error,
        notice=notice,
    )


@bp.route("/admin/users/create", methods=["POST"])
@admin_required()
def admin_create_user():
    me = get_session_user()
    username = clean_text(request.form.get("username"))
    password = clean_text(request.form.get("password"))
    account_name = clean_text(request.form.get("account_name"))
    email = clean_text(request.form.get("email"))
    role = clean_text(request.form.get("role")).lower() or "member"
    workspace_id = clean_text(request.form.get("workspace_id"))

    if not username or not password:
        return redirect(url_for("admin.admin_dashboard", error="username and password are required"))
    if role not in {"admin", "manager", "member", "builder"}:
        role = "member"

    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_default_users(conn)

    exists = conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
    if exists:
        return redirect(url_for("admin.admin_dashboard", error="username already exists"))

    manufacturer_id = None
    if account_name:
        row = conn.execute(
            "SELECT id FROM manufacturers WHERE lower(name)=lower(?)",
            (account_name,),
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO manufacturers(name, contact_email) VALUES(?, ?)",
                (account_name, email),
            )
            manufacturer_id = int(cur.lastrowid)
        else:
            manufacturer_id = int(row["id"])

    users_columns = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    cols = ["username", "password_hash", "account_name"]
    vals = [username, hash_password(password), account_name]
    if "role" in users_columns:
        cols.append("role")
        vals.append(role)
    if "email" in users_columns:
        cols.append("email")
        vals.append(email)
    if "manufacturer_id" in users_columns:
        cols.append("manufacturer_id")
        vals.append(manufacturer_id)
    if "workspace_id" in users_columns:
        ws_id = workspace_id or derive_workspace_id(account_name, username)
        cols.append("workspace_id")
        vals.append(ws_id)

    query = f"INSERT INTO users ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
    cur = conn.execute(query, tuple(vals))
    created_user_id = int(cur.lastrowid or 0)
    log_activity(
        conn,
        int(me.get("id") or 0),
        "admin_created_user",
        "user",
        str(created_user_id),
        f"Created user {username} ({role})" + (f" for {account_name}" if account_name else ""),
        workspace_id=clean_text(me.get("workspace_id")),
        manufacturer_id=int(me.get("manufacturer_id") or 0),
    )
    conn.commit()
    return redirect(url_for("admin.admin_dashboard", notice="User created"))


@bp.route("/admin/feedback-image/<path:filename>")
@admin_required()
def admin_feedback_image(filename):
    base = Config.FEEDBACK_UPLOAD_DIR
    abs_base = os.path.abspath(base)
    abs_path = os.path.abspath(os.path.join(base, filename))
    if not abs_path.startswith(abs_base):
        return redirect(url_for("admin.admin_dashboard"))
    return send_from_directory(base, filename)
