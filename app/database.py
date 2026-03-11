import sqlite3
import os
import csv
import re
from flask import g
from werkzeug.security import generate_password_hash
from .config import Config
from .utils.text_utils import clean_text, norm_state, norm_org
from .utils.data_parse import (
    parse_meta_from_file,
    detect_status,
    detect_year,
    detect_school,
    detect_chapter,
    detect_chapter_id,
    detect_notes,
    parse_location,
)

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
    if "security_question" not in users_columns:
        conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
    if "security_answer_hash" not in users_columns:
        conn.execute("ALTER TABLE users ADD COLUMN security_answer_hash TEXT")
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
            phone TEXT,
            organization_norm TEXT,
            state_norm TEXT,
            license_label TEXT,
            is_greek_licensed INTEGER DEFAULT 0,
            is_collegiate INTEGER DEFAULT 0
        )
        """
    )

    vendor_columns = {row[1] for row in conn.execute("PRAGMA table_info(vendors)").fetchall()}
    if "phone" not in vendor_columns:
        conn.execute("ALTER TABLE vendors ADD COLUMN phone TEXT")
    if "license_label" not in vendor_columns:
        conn.execute("ALTER TABLE vendors ADD COLUMN license_label TEXT")
    if "is_greek_licensed" not in vendor_columns:
        conn.execute("ALTER TABLE vendors ADD COLUMN is_greek_licensed INTEGER DEFAULT 0")
    if "is_collegiate" not in vendor_columns:
        conn.execute("ALTER TABLE vendors ADD COLUMN is_collegiate INTEGER DEFAULT 0")

    greek_exists = os.path.exists(Config.VENDOR_CSV_PATH)
    collegiate_exists = os.path.exists(Config.COLLEGIATE_VENDOR_CSV_PATH)
    if not greek_exists and not collegiate_exists:
        return

    greek_mtime = str(os.path.getmtime(Config.VENDOR_CSV_PATH)) if greek_exists else ""
    collegiate_mtime = str(os.path.getmtime(Config.COLLEGIATE_VENDOR_CSV_PATH)) if collegiate_exists else ""
    prev_greek = conn.execute("SELECT value FROM app_meta WHERE key='vendors_csv_mtime'").fetchone()
    prev_collegiate = conn.execute("SELECT value FROM app_meta WHERE key='collegiate_vendors_csv_mtime'").fetchone()
    if prev_greek and prev_collegiate:
        if (prev_greek[0] or "") == greek_mtime and (prev_collegiate[0] or "") == collegiate_mtime:
            return

    def split_city_state(value: str) -> tuple[str, str]:
        raw = clean_text(value)
        if not raw:
            return "", ""
        if "," in raw:
            city, state = raw.rsplit(",", 1)
            return city.strip(), state.strip()
        return raw, ""

    def license_label(greek_flag: bool, collegiate_flag: bool) -> str:
        if greek_flag and collegiate_flag:
            return "Greek Licensed Holding + Collegiate Vendor"
        if greek_flag:
            return "Greek Licensed Holding"
        if collegiate_flag:
            return "Collegiate Vendor"
        return ""

    conn.execute("DELETE FROM vendors")

    vendor_flags = {}
    records = []

    if greek_exists:
        with open(Config.VENDOR_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
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
                vendor_norm = clean_text(vendor).lower()
                if vendor_norm:
                    vendor_flags.setdefault(vendor_norm, {"greek": False, "collegiate": False})
                    vendor_flags[vendor_norm]["greek"] = True
                records.append(
                    {
                        "vendor": vendor,
                        "organization": organization,
                        "registered_org_count": reg_count,
                        "website": website,
                        "city": city,
                        "state": state,
                        "category": category,
                        "email": email,
                        "phone": "",
                        "organization_norm": norm_org(organization),
                        "state_norm": norm_state(state),
                        "vendor_norm": vendor_norm,
                    }
                )

    if collegiate_exists:
        with open(Config.COLLEGIATE_VENDOR_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                vendor = clean_text(row.get("nobull"))
                city_state_raw = clean_text(row.get("nobull 2"))
                phone = clean_text(row.get("nobull 3"))
                website_text = clean_text(row.get("nobull 4"))
                website_url = clean_text(row.get("nobull href"))
                email = clean_text(row.get("nobull 5"))
                email_url = clean_text(row.get("nobull href 2"))
                notes = clean_text(row.get("nobull 6"))
                if not any([vendor, city_state_raw, phone, website_text, website_url, email, email_url, notes]):
                    continue
                city, state_raw = split_city_state(city_state_raw)
                state = norm_state(state_raw) or clean_text(state_raw)
                website = website_url or website_text
                vendor_norm = clean_text(vendor).lower()
                if vendor_norm:
                    vendor_flags.setdefault(vendor_norm, {"greek": False, "collegiate": False})
                    vendor_flags[vendor_norm]["collegiate"] = True
                records.append(
                    {
                        "vendor": vendor,
                        "organization": "",
                        "registered_org_count": None,
                        "website": website,
                        "city": city,
                        "state": state,
                        "category": "",
                        "email": email,
                        "phone": phone,
                        "organization_norm": "",
                        "state_norm": norm_state(state),
                        "vendor_norm": vendor_norm,
                    }
                )

    merged = {}
    for rec in records:
        vendor_norm = rec.get("vendor_norm") or ""
        state_norm = clean_text(rec.get("state_norm"))
        city_norm = clean_text(rec.get("city")).lower()
        key = (vendor_norm, state_norm, city_norm)
        if key not in merged:
            merged[key] = dict(rec)
            continue
        current = merged[key]
        for field in ["organization", "website", "city", "state", "category", "email"]:
            if not clean_text(current.get(field)) and clean_text(rec.get(field)):
                current[field] = rec.get(field)
        if not clean_text(current.get("phone")) and clean_text(rec.get("phone")):
            current["phone"] = rec.get("phone")
        if current.get("registered_org_count") is None and rec.get("registered_org_count") is not None:
            current["registered_org_count"] = rec.get("registered_org_count")
        if not clean_text(current.get("organization_norm")) and clean_text(rec.get("organization_norm")):
            current["organization_norm"] = rec.get("organization_norm")
        if not clean_text(current.get("state_norm")) and clean_text(rec.get("state_norm")):
            current["state_norm"] = rec.get("state_norm")

    batch = []
    for rec in merged.values():
        vendor_norm = rec.get("vendor_norm") or ""
        flags = vendor_flags.get(vendor_norm, {"greek": False, "collegiate": False})
        greek_flag = bool(flags.get("greek"))
        collegiate_flag = bool(flags.get("collegiate"))
        batch.append(
            (
                rec.get("vendor"),
                rec.get("organization"),
                rec.get("registered_org_count"),
                rec.get("website"),
                rec.get("city"),
                rec.get("state"),
                rec.get("category"),
                rec.get("email"),
                rec.get("phone"),
                rec.get("organization_norm"),
                rec.get("state_norm"),
                license_label(greek_flag, collegiate_flag),
                1 if greek_flag else 0,
                1 if collegiate_flag else 0,
            )
        )

    if batch:
        conn.executemany(
            """
            INSERT INTO vendors
            (vendor, organization, registered_org_count, website, city, state, category, email, phone, organization_norm, state_norm,
             license_label, is_greek_licensed, is_collegiate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )

    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES('vendors_csv_mtime', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (greek_mtime,),
    )
    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES('collegiate_vendors_csv_mtime', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (collegiate_mtime,),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_name ON vendors(vendor)")
    conn.commit()

def ensure_institutions_table(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS institutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dapip_id TEXT,
            ope_id TEXT,
            ipeds_unit_ids TEXT,
            location_name TEXT,
            parent_name TEXT,
            parent_dapip_id TEXT,
            location_type TEXT,
            address TEXT,
            street TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            general_phone TEXT,
            admin_name TEXT,
            admin_phone TEXT,
            admin_email TEXT,
            fax TEXT,
            update_date TEXT,
            state_norm TEXT,
            location_name_norm TEXT,
            parent_name_norm TEXT
        )
        """
    )

    columns = {row[1] for row in conn.execute("PRAGMA table_info(institutions)").fetchall()}
    add_cols = [
        ("street", "TEXT"),
        ("city", "TEXT"),
        ("state", "TEXT"),
        ("zip", "TEXT"),
        ("state_norm", "TEXT"),
        ("location_name_norm", "TEXT"),
        ("parent_name_norm", "TEXT"),
    ]
    for col, ctype in add_cols:
        if col not in columns:
            conn.execute(f"ALTER TABLE institutions ADD COLUMN {col} {ctype}")

    if not os.path.exists(Config.INSTITUTIONS_CSV_PATH):
        return

    file_mtime = str(os.path.getmtime(Config.INSTITUTIONS_CSV_PATH))
    prev = conn.execute("SELECT value FROM app_meta WHERE key='institutions_csv_mtime'").fetchone()
    if prev and prev[0] == file_mtime:
        return

    def parse_address(raw: str) -> tuple[str, str, str, str]:
        raw = clean_text(raw)
        if not raw:
            return "", "", "", ""
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        street = ""
        city = ""
        state = ""
        zip_code = ""
        state_zip = ""
        if len(parts) >= 3:
            street = ", ".join(parts[:-2]).strip()
            city = parts[-2].strip()
            state_zip = parts[-1].strip()
        elif len(parts) == 2:
            street = parts[0].strip()
            state_zip = parts[1].strip()
        else:
            street = raw
        if state_zip:
            m = re.search(r"([A-Za-z]{2})\\s*(\\d{5}(?:-\\d{4})?)?", state_zip)
            if m:
                state_abbr = m.group(1).upper()
                zip_code = m.group(2) or ""
                state = norm_state(state_abbr) or state_abbr
            else:
                state = norm_state(state_zip) or state_zip
        return street, city, state, zip_code

    conn.execute("DELETE FROM institutions")
    with open(Config.INSTITUTIONS_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            location_name = clean_text(row.get("LocationName"))
            parent_name = clean_text(row.get("ParentName"))
            address = clean_text(row.get("Address"))
            street, city, state, zip_code = parse_address(address)
            state_norm = norm_state(state) or clean_text(state)
            batch.append(
                (
                    clean_text(row.get("DapipId")),
                    clean_text(row.get("OpeId")),
                    clean_text(row.get("IpedsUnitIds")),
                    location_name,
                    parent_name,
                    clean_text(row.get("ParentDapipId")),
                    clean_text(row.get("LocationType")),
                    address,
                    street,
                    city,
                    state,
                    zip_code,
                    clean_text(row.get("GeneralPhone")),
                    clean_text(row.get("AdminName")),
                    clean_text(row.get("AdminPhone")),
                    clean_text(row.get("AdminEmail")),
                    clean_text(row.get("Fax")),
                    clean_text(row.get("UpdateDate")),
                    state_norm,
                    norm_org(location_name),
                    norm_org(parent_name),
                )
            )

    conn.executemany(
        """
        INSERT INTO institutions
        (dapip_id, ope_id, ipeds_unit_ids, location_name, parent_name, parent_dapip_id, location_type, address,
         street, city, state, zip, general_phone, admin_name, admin_phone, admin_email, fax, update_date,
         state_norm, location_name_norm, parent_name_norm)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES('institutions_csv_mtime', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (file_mtime,),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inst_name ON institutions(location_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inst_state ON institutions(state)")
    conn.commit()

def ensure_chapters_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    def table_columns(table_name: str) -> list[str]:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]

    raw_cols = table_columns("chapters")
    if raw_cols and "chapter_uid" not in raw_cols and ("source_file" in raw_cols or "row_number" in raw_cols):
        conn.execute("ALTER TABLE chapters RENAME TO chapters_raw")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_uid TEXT UNIQUE,
            institution_id INTEGER,
            chapter_name TEXT,
            organization TEXT,
            school TEXT,
            city TEXT,
            state TEXT,
            instagram TEXT,
            chapter_id TEXT,
            status TEXT,
            founded_year INTEGER,
            notes TEXT,
            org_code TEXT,
            entity_type TEXT,
            scope TEXT,
            source_file TEXT,
            row_number TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chapter_inst ON chapters(institution_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chapter_org ON chapters(organization)")

    raw_exists = bool(table_columns("chapters_raw"))
    if not raw_exists:
        return

    raw_count = conn.execute("SELECT COUNT(*) FROM chapters_raw").fetchone()[0]
    prev_count_row = conn.execute("SELECT value FROM app_meta WHERE key='chapters_raw_count'").fetchone()
    prev_count = int(prev_count_row[0]) if prev_count_row and str(prev_count_row[0]).isdigit() else -1
    existing_count = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
    if existing_count > 0 and raw_count == prev_count:
        return

    ensure_institutions_table(conn)
    inst_rows = conn.execute("SELECT id, location_name FROM institutions").fetchall()
    inst_lookup = {}
    for row in inst_rows:
        name_norm = norm_org(row["location_name"])
        if name_norm and name_norm not in inst_lookup:
            inst_lookup[name_norm] = int(row["id"])

    conn.execute("DELETE FROM chapters")
    data_columns = [c for c in table_columns("chapters_raw") if c not in {"id"}]
    select_sql = "SELECT " + ", ".join([f'\"{c}\"' for c in data_columns]) + " FROM chapters_raw"
    chapter_rows = conn.execute(select_sql).fetchall()
    batch = []
    for r in chapter_rows:
        source_file = clean_text(r["source_file"]) if "source_file" in r.keys() else ""
        row_number = clean_text(r["row_number"]) if "row_number" in r.keys() else ""
        org_code, org_name, entity_type, scope = parse_meta_from_file(source_file)

        values: list[str] = []
        for c in data_columns:
            if c in {"source_file", "row_number"}:
                continue
            v = clean_text(r[c])
            if v and v not in {"[", "]"} and not v.lower().startswith("http"):
                values.append(v)

        if not values:
            continue

        chapter_name = detect_chapter(values)
        chapter_id = detect_chapter_id(values)
        school = detect_school(values)
        founded_year = detect_year(values)
        status = detect_status(values)
        notes = detect_notes(values)

        city = ""
        state = ""
        for v in values:
            if v in {chapter_name, school}:
                continue
            loc_city, loc_state = parse_location(v)
            city = city or loc_city
            state = state or loc_state

        if not any([chapter_name, school, city, state, status, founded_year]):
            continue

        chapter_uid = f"{source_file}::{row_number}" if source_file or row_number else chapter_id or chapter_name
        inst_id = inst_lookup.get(norm_org(school)) if school else None

        batch.append(
            (
                chapter_uid,
                inst_id,
                chapter_name,
                org_name,
                school,
                city,
                state,
                "",
                chapter_id,
                status,
                int(founded_year) if str(founded_year).isdigit() else None,
                notes,
                org_code,
                entity_type,
                scope,
                source_file,
                row_number,
            )
        )

    if batch:
        conn.executemany(
            """
            INSERT INTO chapters
            (chapter_uid, institution_id, chapter_name, organization, school, city, state, instagram, chapter_id,
             status, founded_year, notes, org_code, entity_type, scope, source_file, row_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )

    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES('chapters_raw_count', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(raw_count),),
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
