from functools import wraps
from typing import Tuple
from flask import request, jsonify, redirect, url_for, session
from .database import get_connection, ensure_crm_tables, ensure_default_users, derive_workspace_id
from .utils.text_utils import clean_text


def account_type_for_user(user: dict | None) -> str:
    return clean_text((user or {}).get("account_type")).lower() or "manufacturer"


def is_brand_owner_user(user: dict | None) -> bool:
    return account_type_for_user(user) == "brand_owner"

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
        row = conn.execute(f"SELECT 1 FROM {table} WHERE workspace_id=? LIMIT 1", (ws,)).fetchone()
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

def get_session_user() -> dict:
    user_id = session.get("user_id")
    if not user_id:
        return {}
    conn = get_connection()
    ensure_crm_tables(conn)
    ensure_default_users(conn)
    row = conn.execute(
        "SELECT id, username, account_name, workspace_id, manufacturer_id, brand_owner_id, account_type, role, team_id, team_role FROM users WHERE id=?",
        (int(user_id),),
    ).fetchone()
    if not row:
        session.clear()
        return {}
    user = {k: row[k] for k in row.keys()}
    current_ws = clean_text(user.get("workspace_id"))
    if current_ws and not _workspace_has_any_data(conn, current_ws):
        recovered = _recover_workspace_for_user(conn, int(user.get("id") or 0), int(user.get("manufacturer_id") or 0))
        if recovered:
            conn.execute("UPDATE users SET workspace_id=? WHERE id=?", (recovered, int(user["id"])))
            conn.commit()
            user["workspace_id"] = recovered
            current_ws = recovered
    if not current_ws:
        recovered = _recover_workspace_for_user(conn, int(user.get("id") or 0), int(user.get("manufacturer_id") or 0))
        workspace_id = recovered or derive_workspace_id(
            clean_text(user.get("account_name")),
            clean_text(user.get("username")),
            int(user.get("id") or 0),
        )
        conn.execute("UPDATE users SET workspace_id=? WHERE id=?", (workspace_id, int(user["id"])))
        conn.commit()
        user["workspace_id"] = workspace_id
    return user

def login_required():
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = get_session_user()
            if not user:
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "login required"}), 401
                return redirect(url_for("auth.login_page", next=request.path))
            return fn(*args, **kwargs)
        return wrapped
    return decorator
