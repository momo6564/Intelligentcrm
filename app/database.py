import sqlite3
import os
import csv
import re
from flask import g
from werkzeug.security import generate_password_hash
from .config import Config
from .utils.text_utils import clean_text, norm_state, norm_org

def get_connection() -> sqlite3.Connection:
    if 'db' not in g:
        g.db = sqlite3.connect(Config.DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_connection(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def ensure_crm_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id TEXT NOT NULL,
            org TEXT,
            chapter_name TEXT,
            school TEXT,
            city TEXT,
            state TEXT,
            status TEXT DEFAULT 'prospect',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT,
            chapter_id TEXT NOT NULL,
            org TEXT,
            chapter_name TEXT,
            school TEXT,
            city TEXT,
            state TEXT,
            year INTEGER,
            product TEXT,
            quantity INTEGER,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            filters_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id TEXT NOT NULL,
            contact_name TEXT,
            role TEXT,
            instagram TEXT,
            email TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_org_licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT NOT NULL,
            org TEXT NOT NULL,
            state TEXT,
            status TEXT DEFAULT 'active',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS competitors_followed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT NOT NULL,
            competitor_vendor TEXT NOT NULL,
            is_starred INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(vendor, competitor_vendor)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manufacturers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            city TEXT,
            state TEXT,
            contact_email TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manufacturer_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manufacturer_id INTEGER,
            manufacturer_name TEXT,
            workspace_id TEXT,
            vendor TEXT NOT NULL,
            org TEXT,
            chapter_id TEXT,
            chapter_name TEXT,
            school TEXT,
            city TEXT,
            state TEXT,
            year INTEGER,
            order_type TEXT,
            quantity INTEGER,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            account_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            chapter_id TEXT,
            vendor_id INTEGER,
            connection TEXT,
            status TEXT DEFAULT 'lead',
            notes TEXT,
            last_contact_at TEXT,
            follow_up_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crm_contact_id INTEGER,
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT DEFAULT 'queued',
            sent_at TEXT,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crm_contact_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            created_by_user_id INTEGER,
            workspace_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crm_contact_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            due_date TEXT,
            status TEXT DEFAULT 'open',
            priority TEXT DEFAULT 'normal',
            created_by_user_id INTEGER,
            completed_at TEXT,
            workspace_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crm_contact_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_by_user_id INTEGER,
            workspace_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_contact_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crm_contact_id INTEGER NOT NULL,
            crm_tag_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, crm_contact_id, crm_tag_id)
        )
        """
    )

    vendor_order_columns = {row[1] for row in conn.execute("PRAGMA table_info(vendor_orders)").fetchall()}
    if "order_type" not in vendor_order_columns:
        conn.execute("ALTER TABLE vendor_orders ADD COLUMN order_type TEXT")
    if "workspace_id" not in vendor_order_columns:
        conn.execute("ALTER TABLE vendor_orders ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in vendor_order_columns:
        conn.execute("ALTER TABLE vendor_orders ADD COLUMN manufacturer_id INTEGER")
    lead_columns = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    if "follow_up_date" not in lead_columns:
        conn.execute("ALTER TABLE leads ADD COLUMN follow_up_date TEXT")
    if "workspace_id" not in lead_columns:
        conn.execute("ALTER TABLE leads ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in lead_columns:
        conn.execute("ALTER TABLE leads ADD COLUMN manufacturer_id INTEGER")
    users_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "account_name" not in users_columns:
        conn.execute("ALTER TABLE users ADD COLUMN account_name TEXT")
    if "role" not in users_columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT")
    if "manufacturer_id" not in users_columns:
        conn.execute("ALTER TABLE users ADD COLUMN manufacturer_id INTEGER")
    if "workspace_id" not in users_columns:
        conn.execute("ALTER TABLE users ADD COLUMN workspace_id TEXT")
    crm_contact_columns = {row[1] for row in conn.execute("PRAGMA table_info(crm_contacts)").fetchall()}
    if "workspace_id" not in crm_contact_columns:
        conn.execute("ALTER TABLE crm_contacts ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in crm_contact_columns:
        conn.execute("ALTER TABLE crm_contacts ADD COLUMN manufacturer_id INTEGER")
    if "priority" not in crm_contact_columns:
        conn.execute("ALTER TABLE crm_contacts ADD COLUMN priority TEXT DEFAULT 'normal'")
    if "value_estimate" not in crm_contact_columns:
        conn.execute("ALTER TABLE crm_contacts ADD COLUMN value_estimate REAL")
    if "expected_close_date" not in crm_contact_columns:
        conn.execute("ALTER TABLE crm_contacts ADD COLUMN expected_close_date TEXT")
    if "updated_at" not in crm_contact_columns:
        conn.execute("ALTER TABLE crm_contacts ADD COLUMN updated_at TEXT")
    activities_columns = {row[1] for row in conn.execute("PRAGMA table_info(activities)").fetchall()}
    if "workspace_id" not in activities_columns:
        conn.execute("ALTER TABLE activities ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in activities_columns:
        conn.execute("ALTER TABLE activities ADD COLUMN manufacturer_id INTEGER")
    contacts_columns = {row[1] for row in conn.execute("PRAGMA table_info(chapter_contacts)").fetchall()}
    if "workspace_id" not in contacts_columns:
        conn.execute("ALTER TABLE chapter_contacts ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in contacts_columns:
        conn.execute("ALTER TABLE chapter_contacts ADD COLUMN manufacturer_id INTEGER")
    messages_columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "workspace_id" not in messages_columns:
        conn.execute("ALTER TABLE messages ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in messages_columns:
        conn.execute("ALTER TABLE messages ADD COLUMN manufacturer_id INTEGER")
    saved_views_columns = {row[1] for row in conn.execute("PRAGMA table_info(saved_views)").fetchall()}
    if "workspace_id" not in saved_views_columns:
        conn.execute("ALTER TABLE saved_views ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in saved_views_columns:
        conn.execute("ALTER TABLE saved_views ADD COLUMN manufacturer_id INTEGER")
    lead_activities_columns = {row[1] for row in conn.execute("PRAGMA table_info(lead_activities)").fetchall()}
    if "workspace_id" not in lead_activities_columns:
        conn.execute("ALTER TABLE lead_activities ADD COLUMN workspace_id TEXT")
    if "manufacturer_id" not in lead_activities_columns:
        conn.execute("ALTER TABLE lead_activities ADD COLUMN manufacturer_id INTEGER")
    crm_notes_columns = {row[1] for row in conn.execute("PRAGMA table_info(crm_notes)").fetchall()}
    if "workspace_id" not in crm_notes_columns:
        conn.execute("ALTER TABLE crm_notes ADD COLUMN workspace_id TEXT")
    crm_tasks_columns = {row[1] for row in conn.execute("PRAGMA table_info(crm_tasks)").fetchall()}
    if "workspace_id" not in crm_tasks_columns:
        conn.execute("ALTER TABLE crm_tasks ADD COLUMN workspace_id TEXT")
    crm_activities_columns = {row[1] for row in conn.execute("PRAGMA table_info(crm_activities)").fetchall()}
    if "workspace_id" not in crm_activities_columns:
        conn.execute("ALTER TABLE crm_activities ADD COLUMN workspace_id TEXT")

    # Ensure every user has a stable workspace_id to preserve CRM ownership across sessions.
    missing_ws_users = conn.execute(
        "SELECT id, username, account_name FROM users WHERE trim(coalesce(workspace_id,''))=''"
    ).fetchall()
    for row in missing_ws_users:
        conn.execute(
            "UPDATE users SET workspace_id=? WHERE id=?",
            (
                derive_workspace_id(
                    clean_text(row["account_name"]),
                    clean_text(row["username"]),
                    int(row["id"]),
                ),
                int(row["id"]),
            ),
        )

    # Backfill workspace on activity rows keyed by user_id where possible.
    if "workspace_id" in activities_columns:
        conn.execute(
            """
            UPDATE activities
            SET workspace_id=(
                SELECT u.workspace_id FROM users u WHERE u.id=activities.user_id
            )
            WHERE trim(coalesce(activities.workspace_id,''))=''
              AND activities.user_id IS NOT NULL
              AND EXISTS(SELECT 1 FROM users u WHERE u.id=activities.user_id)
            """
        )

    # Backfill workspace via manufacturer mapping for legacy rows.
    users_cols_after = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    has_user_mid = "manufacturer_id" in users_cols_after
    manufacturer_workspace = {}
    if has_user_mid:
        mid_rows = conn.execute(
            """
            SELECT manufacturer_id, workspace_id
            FROM users
            WHERE manufacturer_id IS NOT NULL
              AND manufacturer_id > 0
              AND trim(coalesce(workspace_id,''))<>''
            ORDER BY id ASC
            """
        ).fetchall()
        for row in mid_rows:
            mid = int(row["manufacturer_id"] or 0)
            if mid <= 0 or mid in manufacturer_workspace:
                continue
            manufacturer_workspace[mid] = clean_text(row["workspace_id"])

    table_defs = [
        ("crm_contacts", "manufacturer_id"),
        ("vendor_orders", "manufacturer_id"),
        ("leads", "manufacturer_id"),
        ("messages", "manufacturer_id"),
        ("activities", "manufacturer_id"),
    ]
    for table_name, mid_col in table_defs:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if "workspace_id" not in cols or mid_col not in cols:
            continue
        for mid, ws in manufacturer_workspace.items():
            if not ws:
                continue
            conn.execute(
                f"""
                UPDATE {table_name}
                SET workspace_id=?
                WHERE trim(coalesce(workspace_id,''))=''
                  AND {mid_col}=?
                """,
                (ws, int(mid)),
            )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_orders_workspace ON vendor_orders(workspace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_orders_workspace_vendor ON vendor_orders(workspace_id, vendor)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_orders_workspace_chapter ON vendor_orders(workspace_id, chapter_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_contacts_workspace ON crm_contacts(workspace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_contacts_workspace_type_status ON crm_contacts(workspace_id, type, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_contacts_workspace_followup ON crm_contacts(workspace_id, follow_up_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_workspace ON activities(workspace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_workspace ON leads(workspace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_tasks_workspace_due_status ON crm_tasks(workspace_id, due_date, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_notes_workspace_contact ON crm_notes(workspace_id, crm_contact_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_activities_workspace_contact ON crm_activities(workspace_id, crm_contact_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_contact_tags_workspace_contact ON crm_contact_tags(workspace_id, crm_contact_id)")
    conn.commit()

def derive_workspace_id(account_name: str = "", username: str = "", user_id: int = 0) -> str:
    base = clean_text(account_name) or clean_text(username)
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    if slug:
        return f"ws-{slug}"
    if int(user_id or 0) > 0:
        return f"user-{int(user_id)}"
    return "ws-default"

def log_lead_activity(
    conn: sqlite3.Connection,
    lead_id: int,
    action: str,
    details: str = "",
    workspace_id: str = "",
) -> None:
    conn.execute(
        "INSERT INTO lead_activities (lead_id, action, details, workspace_id) VALUES (?, ?, ?, ?)",
        (int(lead_id), clean_text(action), clean_text(details), clean_text(workspace_id)),
    )

def ensure_default_users(conn: sqlite3.Connection) -> None:
    defaults = [
        ("demo", "demo123", "Demo Account"),
    ]
    users_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    for username, password, account_name in defaults:
        existing = conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
        if existing:
            if "workspace_id" in users_columns:
                user_row = conn.execute(
                    "SELECT id, username, account_name, workspace_id FROM users WHERE id=?",
                    (int(existing["id"]),),
                ).fetchone()
                if user_row and not clean_text(user_row["workspace_id"]):
                    conn.execute(
                        "UPDATE users SET workspace_id=? WHERE id=?",
                        (
                            derive_workspace_id(
                                clean_text(user_row["account_name"]),
                                clean_text(user_row["username"]),
                                int(user_row["id"]),
                            ),
                            int(user_row["id"]),
                        ),
                    )
            continue

        workspace_id = derive_workspace_id(account_name, username)
        if "role" in users_columns and "workspace_id" in users_columns:
            conn.execute(
                "INSERT INTO users(username, password_hash, account_name, role, workspace_id) VALUES(?, ?, ?, 'admin', ?)",
                (username, generate_password_hash(password), account_name, workspace_id),
            )
        else:
            conn.execute(
                "INSERT INTO users(username, password_hash, account_name) VALUES(?, ?, ?)",
                (username, generate_password_hash(password), account_name),
            )
    conn.commit()

def log_activity(
    conn: sqlite3.Connection,
    user_id: int,
    action: str,
    entity_type: str = "",
    entity_id: str = "",
    details: str = "",
    workspace_id: str = "",
    manufacturer_id: int = 0,
) -> None:
    uid = int(user_id or 0) or None
    mid = int(manufacturer_id or 0)
    ws = clean_text(workspace_id)

    users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if uid:
        user_row = conn.execute(
            "SELECT manufacturer_id, workspace_id, account_name, username FROM users WHERE id=?",
            (uid,),
        ).fetchone()
        if user_row is not None:
            if "manufacturer_id" in users_cols and not mid:
                mid = int(user_row["manufacturer_id"] or 0)
            if not ws:
                ws = clean_text(user_row["workspace_id"]) or derive_workspace_id(
                    clean_text(user_row["account_name"]),
                    clean_text(user_row["username"]),
                    uid,
                )
    if not ws:
        ws = derive_workspace_id(user_id=uid or 0)
    if mid < 0:
        mid = 0

    activity_cols = {row[1] for row in conn.execute("PRAGMA table_info(activities)").fetchall()}
    cols = []
    vals = []
    if "manufacturer_id" in activity_cols:
        cols.append("manufacturer_id")
        vals.append(mid)
    if "workspace_id" in activity_cols:
        cols.append("workspace_id")
        vals.append(ws)
    if "user_id" in activity_cols:
        cols.append("user_id")
        vals.append(uid)
    cols.extend(["action", "entity_type", "entity_id", "details"])
    vals.extend(
        [
            clean_text(action),
            clean_text(entity_type),
            clean_text(entity_id),
            clean_text(details),
        ]
    )
    placeholders = ",".join("?" for _ in cols)
    conn.execute(f"INSERT INTO activities({','.join(cols)}) VALUES({placeholders})", tuple(vals))

def ensure_vendor_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT,
            organization TEXT,
            registered_org_count INTEGER,
            website TEXT,
            city TEXT,
            state TEXT,
            category TEXT,
            email TEXT,
            organization_norm TEXT,
            state_norm TEXT
        )
        """
    )

    if not os.path.exists(Config.VENDOR_CSV_PATH):
        return

    file_mtime = str(os.path.getmtime(Config.VENDOR_CSV_PATH))
    prev = conn.execute("SELECT value FROM app_meta WHERE key='vendors_csv_mtime'").fetchone()
    if prev and prev[0] == file_mtime:
        return

    conn.execute("DELETE FROM vendors")
    with open(Config.VENDOR_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            vendor = clean_text(row.get("Vendor"))
            organization = clean_text(row.get("Organization"))
            reg_count_raw = clean_text(row.get("RegisteredOrgCount"))
            reg_count = int(reg_count_raw) if reg_count_raw.isdigit() else None
            website = clean_text(row.get("Website"))
            city = clean_text(row.get("City"))
            state = norm_state(row.get("State")) or clean_text(row.get("State"))
            category = clean_text(row.get("Category"))
            email = clean_text(row.get("Email"))
            batch.append(
                (vendor, organization, reg_count, website, city, state, category, email, norm_org(organization), norm_state(state))
            )

    conn.executemany(
        """
        INSERT INTO vendors
        (vendor, organization, registered_org_count, website, city, state, category, email, organization_norm, state_norm)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES('vendors_csv_mtime', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (file_mtime,),
    )
    conn.commit()

def load_vendor_lookup(conn: sqlite3.Connection) -> tuple:
    rows = conn.execute(
        "SELECT vendor, organization, website, city, state, category, email, organization_norm, state_norm FROM vendors"
    ).fetchall()

    def org_keys(value: str) -> list[str]:
        raw = clean_text(value)
        if not raw:
            return []
        parts = [p.strip() for p in re.split(r",|/|&|\band\b", raw, flags=re.I) if clean_text(p)]
        keys = {norm_org(p) for p in parts if norm_org(p)}
        whole = norm_org(raw)
        if whole:
            keys.add(whole)
        lower_raw = raw.lower()
        for full_name, _kind in Config.ORG_MAP.values():
            if full_name.lower() in lower_raw:
                nk = norm_org(full_name)
                if nk:
                    keys.add(nk)
        return sorted(keys)

    exact_lookup = {}
    org_lookup = {}
    for r in rows:
        org_norms = org_keys(clean_text(r["organization"]))
        state_norm = clean_text(r["state_norm"])
        base = {
            "vendor": clean_text(r["vendor"]),
            "organization": clean_text(r["organization"]),
            "website": clean_text(r["website"]),
            "city": clean_text(r["city"]),
            "state": clean_text(r["state"]),
            "category": clean_text(r["category"]),
            "email": clean_text(r["email"]),
        }
        for org_norm in org_norms:
            if state_norm:
                rec_exact = dict(base)
                rec_exact["matchType"] = "Org + State"
                exact_lookup.setdefault((org_norm, state_norm), []).append(rec_exact)

            rec_org = dict(base)
            rec_org["matchType"] = "Org-wide"
            org_lookup.setdefault(org_norm, []).append(rec_org)

    return exact_lookup, org_lookup
