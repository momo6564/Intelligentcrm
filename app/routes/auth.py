from flask import Blueprint, request, session, redirect, url_for, render_template
from werkzeug.security import check_password_hash, generate_password_hash
import uuid
from urllib.parse import urlsplit

from ..database import get_connection, ensure_crm_tables, ensure_default_users, log_activity, derive_workspace_id
from ..utils.text_utils import clean_text

bp = Blueprint('auth', __name__)

def _table_has_column(conn, table_name: str, col_name: str) -> bool:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    return col_name in cols

def _workspace_has_any_data(conn, workspace_id: str) -> bool:
    ws = clean_text(workspace_id)
    if not ws:
        return False
    checks = ["crm_contacts", "vendor_orders", "leads", "chapter_contacts", "activities", "messages"]
    for table in checks:
        if not _table_has_column(conn, table, "workspace_id"):
            continue
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE workspace_id=? LIMIT 1",
            (ws,),
        ).fetchone()
        if row is not None:
            return True
    return False

def _recover_workspace_for_user(conn, user_id: int, manufacturer_id: int) -> str:
    uid = int(user_id or 0)
    if uid > 0 and _table_has_column(conn, "activities", "user_id") and _table_has_column(conn, "activities", "workspace_id"):
        row = conn.execute(
            """
            SELECT workspace_id, COUNT(*) AS c
            FROM activities
            WHERE user_id=? AND trim(coalesce(workspace_id,''))<>''
            GROUP BY workspace_id
            ORDER BY c DESC
            LIMIT 1
            """,
            (uid,),
        ).fetchone()
        if row and clean_text(row["workspace_id"]):
            return clean_text(row["workspace_id"])

    mid = int(manufacturer_id or 0)
    if mid <= 0:
        return ""
    if _table_has_column(conn, "crm_contacts", "manufacturer_id") and _table_has_column(conn, "crm_contacts", "workspace_id"):
        row = conn.execute(
            """
            SELECT workspace_id, COUNT(*) AS c
            FROM crm_contacts
            WHERE manufacturer_id=? AND trim(coalesce(workspace_id,''))<>''
            GROUP BY workspace_id
            ORDER BY c DESC
            LIMIT 1
            """,
            (mid,),
        ).fetchone()
        if row and clean_text(row["workspace_id"]):
            return clean_text(row["workspace_id"])
    if _table_has_column(conn, "activities", "manufacturer_id") and _table_has_column(conn, "activities", "workspace_id"):
        row = conn.execute(
            """
            SELECT workspace_id, COUNT(*) AS c
            FROM activities
            WHERE manufacturer_id=? AND trim(coalesce(workspace_id,''))<>''
            GROUP BY workspace_id
            ORDER BY c DESC
            LIMIT 1
            """,
            (mid,),
        ).fetchone()
        if row and clean_text(row["workspace_id"]):
            return clean_text(row["workspace_id"])
    return ""

def _safe_next_path(raw: str) -> str:
    path = clean_text(raw)
    if not path:
        return "/"
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path

@bp.route("/login", methods=["GET", "POST"])
def login_page():
    error = ""
    next_path = _safe_next_path(request.args.get("next"))
    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        password = clean_text(request.form.get("password"))
        next_path = _safe_next_path(request.form.get("next"))
        conn = get_connection()
        ensure_crm_tables(conn)
        ensure_default_users(conn)
        row = conn.execute(
            "SELECT id, username, password_hash, account_name, workspace_id, manufacturer_id FROM users WHERE lower(username)=lower(?)",
            (username,),
        ).fetchone()
        if not row or not check_password_hash(clean_text(row["password_hash"]), password):
            error = "Invalid username or password."
        else:
            user_id = int(row["id"])
            workspace_id = clean_text(row["workspace_id"])
            manufacturer_id = int(row["manufacturer_id"] or 0)
            if workspace_id and not _workspace_has_any_data(conn, workspace_id):
                recovered = _recover_workspace_for_user(conn, user_id, manufacturer_id)
                if recovered:
                    workspace_id = recovered
                    conn.execute("UPDATE users SET workspace_id=? WHERE id=?", (workspace_id, user_id))
                    conn.commit()
            if not workspace_id:
                recovered = _recover_workspace_for_user(conn, user_id, manufacturer_id)
                workspace_id = recovered or derive_workspace_id(
                    clean_text(row["account_name"]),
                    clean_text(row["username"]),
                    user_id,
                )
                conn.execute("UPDATE users SET workspace_id=? WHERE id=?", (workspace_id, user_id))
                conn.commit()
            session["user_id"] = user_id
            if (next_path or "/") == "/":
                return redirect(url_for("main.dashboard_page"))
            return redirect(next_path)
    return render_template("auth/login.html", error=error, next_path=next_path)


@bp.route("/signup", methods=["GET", "POST"])
def signup_page():
    error = ""
    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        password = clean_text(request.form.get("password"))
        manufacturer_name = clean_text(request.form.get("manufacturer_name"))
        contact_email = clean_text(request.form.get("contact_email"))
        if not username or not password or not manufacturer_name:
            error = "username, password and manufacturer name are required."
        else:
            conn = get_connection()
            ensure_crm_tables(conn)
            ensure_default_users(conn)
            exists = conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
            if exists:
                error = "username already exists."
            else:
                row = conn.execute("SELECT id FROM manufacturers WHERE lower(name)=lower(?)", (manufacturer_name,)).fetchone()
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO manufacturers(name, contact_email) VALUES(?, ?)",
                        (manufacturer_name, contact_email),
                    )
                    manufacturer_id = int(cur.lastrowid)
                else:
                    manufacturer_id = int(row["id"])
                workspace_id = str(uuid.uuid4())
                
                users_columns = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
                cols = ["username", "password_hash", "account_name"]
                vals = [username, generate_password_hash(password), manufacturer_name]
                if "role" in users_columns:
                    cols.append("role")
                    vals.append("builder")
                if "manufacturer_id" in users_columns:
                    cols.append("manufacturer_id")
                    vals.append(manufacturer_id)
                if "workspace_id" in users_columns:
                    cols.append("workspace_id")
                    vals.append(workspace_id)
                    
                query = f"INSERT INTO users ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
                cur_user = conn.execute(query, tuple(vals))
                user_id = int(cur_user.lastrowid)
                log_activity(
                    conn,
                    user_id,
                    "signup_completed",
                    "workspace",
                    workspace_id,
                    f"Manufacturer workspace created for {manufacturer_name}",
                    workspace_id=workspace_id,
                    manufacturer_id=manufacturer_id,
                )
                conn.commit()
                session["user_id"] = user_id
                return redirect(url_for("main.dashboard_page"))
    return render_template("auth/signup.html", error=error)


@bp.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("auth.login_page"))
