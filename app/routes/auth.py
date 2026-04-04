from flask import Blueprint, request, session, redirect, url_for, render_template, current_app
from werkzeug.security import check_password_hash, generate_password_hash
import uuid
from urllib.parse import urlsplit

try:
    from authlib.integrations.base_client.errors import MismatchingStateError
except Exception:  # pragma: no cover - Authlib optional in some environments
    MismatchingStateError = Exception

from ..database import get_connection, ensure_crm_tables, ensure_default_users, log_activity, derive_workspace_id
from ..config import Config
from ..utils.text_utils import clean_text

bp = Blueprint('auth', __name__)


def _default_dashboard_path(account_type: str) -> str:
    return "/brand/dashboard" if clean_text(account_type).lower() == "brand_owner" else "/dashboard"


def _pending_google_onboarding() -> dict:
    raw = session.get("google_onboarding")
    return raw if isinstance(raw, dict) else {}


def _clear_google_onboarding() -> None:
    session.pop("google_onboarding", None)

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
    error = clean_text(request.args.get("error"))
    if error == "google_state":
        error = "Google sign-in expired. Please try again."
    if error == "google_unavailable":
        error = "Google sign-in is not configured on this server."
    if error == "google_failed":
        error = "Google sign-in failed. Please try again."
    next_path = _safe_next_path(request.args.get("next"))
    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        password = clean_text(request.form.get("password"))
        next_path = _safe_next_path(request.form.get("next"))
        conn = get_connection()
        ensure_crm_tables(conn)
        ensure_default_users(conn)
        row = conn.execute(
            "SELECT id, username, password_hash, account_name, workspace_id, manufacturer_id, brand_owner_id, account_type FROM users WHERE lower(username)=lower(?)",
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
                return redirect(_default_dashboard_path(clean_text(row["account_type"])))
            return redirect(next_path)
    google_enabled = bool(getattr(current_app, "google_oauth", None))
    return render_template("auth/login.html", error=error, next_path=next_path, google_enabled=google_enabled)


@bp.route("/signup", methods=["GET", "POST"])
def signup_page():
    error = ""
    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        password = clean_text(request.form.get("password"))
        account_type = clean_text(request.form.get("account_type")).lower() or "manufacturer"
        account_name = clean_text(request.form.get("account_name") or request.form.get("manufacturer_name"))
        contact_email = clean_text(request.form.get("contact_email"))
        security_question = clean_text(request.form.get("security_question"))
        security_answer = clean_text(request.form.get("security_answer"))
        if account_type not in {"manufacturer", "brand_owner"}:
            error = "Choose whether this account is a manufacturer or a vendor / brand owner."
        elif not username or not password or not account_name or not security_question or not security_answer:
            error = "username, password, account name, security question and answer are required."
        elif security_question not in Config.SECURITY_QUESTIONS:
            error = "Invalid security question selection."
        else:
            conn = get_connection()
            ensure_crm_tables(conn)
            ensure_default_users(conn)
            exists = conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
            if exists:
                error = "username already exists."
            else:
                manufacturer_id = None
                brand_owner_id = None
                if account_type == "manufacturer":
                    row = conn.execute("SELECT id FROM manufacturers WHERE lower(name)=lower(?)", (account_name,)).fetchone()
                    if row is None:
                        cur = conn.execute(
                            "INSERT INTO manufacturers(name, contact_email) VALUES(?, ?)",
                            (account_name, contact_email),
                        )
                        manufacturer_id = int(cur.lastrowid)
                    else:
                        manufacturer_id = int(row["id"])
                else:
                    row = conn.execute("SELECT id, workspace_id FROM brand_owners WHERE lower(name)=lower(?)", (account_name,)).fetchone()
                    if row is None:
                        workspace_id = str(uuid.uuid4())
                        cur = conn.execute(
                            "INSERT INTO brand_owners(name, contact_email, workspace_id) VALUES(?, ?, ?)",
                            (account_name, contact_email, workspace_id),
                        )
                        brand_owner_id = int(cur.lastrowid)
                    else:
                        brand_owner_id = int(row["id"])
                        workspace_id = clean_text(row["workspace_id"]) or str(uuid.uuid4())
                if account_type == "manufacturer":
                    workspace_id = str(uuid.uuid4())
                
                users_columns = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
                cols = ["username", "password_hash", "account_name"]
                vals = [username, generate_password_hash(password), account_name]
                if "role" in users_columns:
                    cols.append("role")
                    vals.append("brand_owner_admin" if account_type == "brand_owner" else "builder")
                if "manufacturer_id" in users_columns:
                    cols.append("manufacturer_id")
                    vals.append(manufacturer_id)
                if "brand_owner_id" in users_columns:
                    cols.append("brand_owner_id")
                    vals.append(brand_owner_id)
                if "workspace_id" in users_columns:
                    cols.append("workspace_id")
                    vals.append(workspace_id)
                if "account_type" in users_columns:
                    cols.append("account_type")
                    vals.append(account_type)
                if "security_question" in users_columns:
                    cols.append("security_question")
                    vals.append(security_question)
                if "security_answer_hash" in users_columns:
                    cols.append("security_answer_hash")
                    vals.append(generate_password_hash(security_answer))
                    
                query = f"INSERT INTO users ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
                cur_user = conn.execute(query, tuple(vals))
                user_id = int(cur_user.lastrowid)
                log_activity(
                    conn,
                    user_id,
                    "signup_completed",
                    "workspace",
                    workspace_id,
                    f"{account_type.replace('_', ' ').title()} workspace created for {account_name}",
                    workspace_id=workspace_id,
                    manufacturer_id=manufacturer_id or 0,
                )
                conn.commit()
                session["user_id"] = user_id
                return redirect(_default_dashboard_path(account_type))
    google_enabled = bool(getattr(current_app, "google_oauth", None))
    return render_template("auth/signup.html", error=error, questions=Config.SECURITY_QUESTIONS, google_enabled=google_enabled)


@bp.route("/google/login")
def google_login():
    google = getattr(current_app, "google_oauth", None)
    if google is None:
        return redirect(url_for("auth.login_page", error="google_unavailable"))
    next_path = _safe_next_path(request.args.get("next"))
    session["google_next"] = next_path
    nonce = uuid.uuid4().hex
    session["google_nonce"] = nonce
    redirect_uri = url_for("auth.google_callback", _external=True)
    configured = clean_text(current_app.config.get("GOOGLE_REDIRECT_URI"))
    if configured:
        cfg = urlsplit(configured)
        if cfg.scheme and cfg.netloc and cfg.netloc == request.host:
            redirect_uri = configured
    return google.authorize_redirect(redirect_uri, nonce=nonce)


@bp.route("/google/callback")
def google_callback():
    google = getattr(current_app, "google_oauth", None)
    if google is None:
        return redirect(url_for("auth.login_page"))
    try:
        token = google.authorize_access_token()
    except MismatchingStateError:
        session.pop("google_next", None)
        session.pop("google_nonce", None)
        return redirect(url_for("auth.login_page", error="google_state"))
    except Exception:
        current_app.logger.exception("Google OAuth token exchange failed.")
        return redirect(url_for("auth.login_page", error="google_failed"))
    userinfo = None
    try:
        nonce = session.pop("google_nonce", None)
        userinfo = google.parse_id_token(token, nonce=nonce)
    except Exception:
        current_app.logger.exception("Google OAuth id_token parsing failed.")
        userinfo = None
    if not userinfo:
        try:
            resp = google.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
            if resp is not None and resp.ok:
                userinfo = resp.json()
            else:
                current_app.logger.error("Google OAuth userinfo failed: %s", getattr(resp, "text", "no response"))
        except Exception:
            current_app.logger.exception("Google OAuth userinfo request failed.")
    if not userinfo:
        return redirect(url_for("auth.login_page", error="google_failed"))

    google_id = clean_text(userinfo.get("sub"))
    email = clean_text(userinfo.get("email")).lower()
    full_name = clean_text(userinfo.get("name")) or clean_text(userinfo.get("given_name"))
    if not email and not google_id:
        return redirect(url_for("auth.login_page"))

    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_default_users(conn)

    user = None
    if google_id:
        user = conn.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
    if user is None and email:
        user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()

    users_columns = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if user is not None:
        user_id = int(user["id"])
        updates = []
        params = []
        if "google_id" in users_columns and google_id and not clean_text(user["google_id"]):
            updates.append("google_id=?")
            params.append(google_id)
        if "email" in users_columns and email and not clean_text(user["email"]):
            updates.append("email=?")
            params.append(email)
        if updates:
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", tuple(params))
            conn.commit()
        account_type = clean_text(user["account_type"]) if "account_type" in user.keys() else ""
        if account_type in {"manufacturer", "brand_owner"}:
            session["user_id"] = int(user_id)
            next_path = _safe_next_path(session.pop("google_next", "")) or "/"
            _clear_google_onboarding()
            if next_path == "/":
                return redirect(_default_dashboard_path(account_type))
            return redirect(next_path)
    session["google_onboarding"] = {
        "google_id": google_id,
        "email": email,
        "full_name": full_name,
        "existing_user_id": int(user["id"]) if user is not None else 0,
    }
    return redirect(url_for("auth.google_onboarding_page"))


@bp.route("/google/onboarding", methods=["GET", "POST"])
def google_onboarding_page():
    pending = _pending_google_onboarding()
    if not pending:
        return redirect(url_for("auth.login_page"))
    error = ""
    if request.method == "POST":
        account_type = clean_text(request.form.get("account_type")).lower()
        account_name = clean_text(request.form.get("account_name"))
        if account_type not in {"manufacturer", "brand_owner"}:
            error = "Choose whether this Google account is for a manufacturer or a vendor / brand owner."
        elif not account_name:
            error = "Account name is required."
        else:
            conn = get_connection()
            ensure_crm_tables(conn)
            ensure_default_users(conn)
            users_columns = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            existing_user_id = int(pending.get("existing_user_id") or 0)
            google_id = clean_text(pending.get("google_id"))
            email = clean_text(pending.get("email")).lower()
            manufacturer_id = None
            brand_owner_id = None
            if account_type == "manufacturer":
                row = conn.execute("SELECT id FROM manufacturers WHERE lower(name)=lower(?)", (account_name,)).fetchone()
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO manufacturers(name, contact_email) VALUES(?, ?)",
                        (account_name, email),
                    )
                    manufacturer_id = int(cur.lastrowid)
                else:
                    manufacturer_id = int(row["id"])
                workspace_id = derive_workspace_id(account_name, email, existing_user_id)
                role_value = "builder"
            else:
                row = conn.execute("SELECT id, workspace_id FROM brand_owners WHERE lower(name)=lower(?)", (account_name,)).fetchone()
                if row is None:
                    workspace_id = derive_workspace_id(account_name, email, existing_user_id)
                    cur = conn.execute(
                        "INSERT INTO brand_owners(name, contact_email, workspace_id) VALUES(?, ?, ?)",
                        (account_name, email, workspace_id),
                    )
                    brand_owner_id = int(cur.lastrowid)
                else:
                    brand_owner_id = int(row["id"])
                    workspace_id = clean_text(row["workspace_id"]) or derive_workspace_id(account_name, email, existing_user_id)
                role_value = "brand_owner_admin"

            if existing_user_id > 0:
                updates = ["account_name=?", "workspace_id=?"]
                params = [account_name, workspace_id]
                if "account_type" in users_columns:
                    updates.append("account_type=?")
                    params.append(account_type)
                if "role" in users_columns:
                    updates.append("role=?")
                    params.append(role_value)
                if "manufacturer_id" in users_columns:
                    updates.append("manufacturer_id=?")
                    params.append(manufacturer_id)
                if "brand_owner_id" in users_columns:
                    updates.append("brand_owner_id=?")
                    params.append(brand_owner_id)
                if "google_id" in users_columns and google_id:
                    updates.append("google_id=?")
                    params.append(google_id)
                if "email" in users_columns and email:
                    updates.append("email=?")
                    params.append(email)
                params.append(existing_user_id)
                conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", tuple(params))
                user_id = existing_user_id
            else:
                username_seed = (email.split("@")[0] if email else "google_user").lower() or "google_user"
                username = username_seed
                suffix = 1
                while conn.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)).fetchone():
                    suffix += 1
                    username = f"{username_seed}{suffix}"
                cols = ["username", "password_hash", "account_name"]
                vals = [username, generate_password_hash(uuid.uuid4().hex), account_name]
                if "account_type" in users_columns:
                    cols.append("account_type")
                    vals.append(account_type)
                if "role" in users_columns:
                    cols.append("role")
                    vals.append(role_value)
                if "manufacturer_id" in users_columns:
                    cols.append("manufacturer_id")
                    vals.append(manufacturer_id)
                if "brand_owner_id" in users_columns:
                    cols.append("brand_owner_id")
                    vals.append(brand_owner_id)
                if "workspace_id" in users_columns:
                    cols.append("workspace_id")
                    vals.append(workspace_id)
                if "google_id" in users_columns:
                    cols.append("google_id")
                    vals.append(google_id)
                if "email" in users_columns:
                    cols.append("email")
                    vals.append(email)
                query = f"INSERT INTO users ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
                cur_user = conn.execute(query, tuple(vals))
                user_id = int(cur_user.lastrowid)

            log_activity(
                conn,
                int(user_id),
                "google_signup_completed",
                "workspace",
                workspace_id,
                f"Google onboarding completed for {account_name}",
                workspace_id=workspace_id,
                manufacturer_id=manufacturer_id or 0,
            )
            conn.commit()
            session["user_id"] = int(user_id)
            next_path = _safe_next_path(session.pop("google_next", "")) or "/"
            _clear_google_onboarding()
            if next_path == "/":
                return redirect(_default_dashboard_path(account_type))
            return redirect(next_path)
    return render_template(
        "auth/google_onboarding.html",
        error=error,
        pending_name=clean_text(pending.get("full_name")) or clean_text(pending.get("email")).split("@")[0],
        pending_email=clean_text(pending.get("email")),
    )


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password_page():
    error = ""
    success = ""
    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        security_question = clean_text(request.form.get("security_question"))
        security_answer = clean_text(request.form.get("security_answer"))
        new_password = clean_text(request.form.get("new_password"))
        if not username or not security_question or not security_answer or not new_password:
            error = "All fields are required."
        elif security_question not in Config.SECURITY_QUESTIONS:
            error = "Invalid security question selection."
        else:
            conn = get_connection()
            ensure_crm_tables(conn)
            row = conn.execute(
                """
                SELECT id, security_question, security_answer_hash
                FROM users
                WHERE lower(username)=lower(?)
                """,
                (username,),
            ).fetchone()
            if not row:
                error = "User not found."
            elif not clean_text(row["security_question"]) or not clean_text(row["security_answer_hash"]):
                error = "No security question set for this account."
            elif clean_text(row["security_question"]) != security_question:
                error = "Security question does not match."
            elif not check_password_hash(clean_text(row["security_answer_hash"]), security_answer):
                error = "Security answer is incorrect."
            else:
                conn.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (generate_password_hash(new_password), int(row["id"])),
                )
                conn.commit()
                success = "Password updated. You can now log in."
    return render_template(
        "auth/reset_password.html",
        error=error,
        success=success,
        questions=Config.SECURITY_QUESTIONS,
    )


@bp.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("auth.login_page"))
