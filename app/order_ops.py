import json
import math
import secrets
import sqlite3
import string
from datetime import date, datetime, timedelta

from .database import get_connection
from .utils.text_utils import clean_date, clean_text


DEFAULT_WORKFLOW_STAGES = [
    {
        "key": "planning-tech-pack",
        "name": "Planning / Internal Tech Pack",
        "department": "Operations",
        "checklist": [
            "Confirm order scope and product details",
            "Attach internal tech pack",
            "Lock customer expectations and dates",
        ],
        "customer_visible": 1,
        "approval_required": 1,
    },
    {
        "key": "pattern-making",
        "name": "Pattern Making",
        "department": "Pattern",
        "checklist": [
            "Draft pattern set",
            "Review construction notes",
            "Prepare for fit confirmation",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "fabric-procurement",
        "name": "Fabric Procurement by Pantone",
        "department": "Sourcing",
        "checklist": [
            "Confirm pantone and fabric composition",
            "Place supplier request",
            "Record incoming material ETA",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "fabric-quality-check",
        "name": "Fabric Quality Check",
        "department": "QC",
        "checklist": [
            "Inspect fabric shade and hand feel",
            "Check roll quality",
            "Approve or reject fabric lot",
        ],
        "customer_visible": 0,
        "approval_required": 1,
    },
    {
        "key": "pattern-size-confirmation",
        "name": "Pattern Check / Size Confirmation",
        "department": "Pattern",
        "checklist": [
            "Review pattern against measurements",
            "Confirm size breakdown",
            "Log changes before cutting",
        ],
        "customer_visible": 1,
        "approval_required": 1,
    },
    {
        "key": "cutting",
        "name": "Cutting",
        "department": "Production",
        "checklist": [
            "Approve marker",
            "Cut fabric by order requirement",
            "Tag batches for bundling",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "bundling-count-check",
        "name": "Bundling and Count Check",
        "department": "Production",
        "checklist": [
            "Bundle parts by size and style",
            "Verify counts against order",
            "Flag shortages before decoration",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "embroidery-prep",
        "name": "Embroidery Prep",
        "department": "Decoration",
        "checklist": [
            "Prepare embroidery file",
            "Confirm placement",
            "Approve thread colors",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "printing-prep",
        "name": "Printing Prep",
        "department": "Decoration",
        "checklist": [
            "Prepare print art and screens",
            "Confirm color recipe",
            "Stage samples for testing",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "decoration-testing",
        "name": "Printing / Embroidery Testing",
        "department": "Decoration",
        "checklist": [
            "Run strike off or sew out",
            "Review result with QC",
            "Approve decoration start",
        ],
        "customer_visible": 1,
        "approval_required": 1,
    },
    {
        "key": "stitching",
        "name": "Stitching",
        "department": "Production",
        "checklist": [
            "Start line production",
            "Track units completed",
            "Flag rework immediately",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "in-process-qc",
        "name": "In-Process QC",
        "department": "QC",
        "checklist": [
            "Inspect stitching quality",
            "Check measurements in line",
            "Release or hold bundles",
        ],
        "customer_visible": 0,
        "approval_required": 1,
    },
    {
        "key": "final-qc",
        "name": "Final QC",
        "department": "QC",
        "checklist": [
            "Inspect finished goods",
            "Approve packing release",
            "Record final QC notes",
        ],
        "customer_visible": 1,
        "approval_required": 1,
    },
    {
        "key": "packing",
        "name": "Packing",
        "department": "Dispatch",
        "checklist": [
            "Pack by order instructions",
            "Verify carton counts",
            "Attach dispatch paperwork",
        ],
        "customer_visible": 0,
        "approval_required": 0,
    },
    {
        "key": "dispatch-delivery",
        "name": "Dispatch / Delivery",
        "department": "Dispatch",
        "checklist": [
            "Hand off shipment",
            "Capture tracking or proof of delivery",
            "Update customer milestone",
        ],
        "customer_visible": 1,
        "approval_required": 1,
    },
    {
        "key": "post-delivery-issues",
        "name": "Post-Delivery Issue Log",
        "department": "Support",
        "checklist": [
            "Capture delivery feedback",
            "Log complaints or rework needs",
            "Close order after support review",
        ],
        "customer_visible": 1,
        "approval_required": 0,
    },
]

OPS_ROLE_ALIASES = {
    "admin": "operations_manager",
    "owner": "operations_manager",
    "manager": "operations_manager",
    "operations_manager": "operations_manager",
    "ops_manager": "operations_manager",
    "operations": "operations_manager",
    "team_leader": "team_leader",
    "leader": "team_leader",
    "member": "team_leader",
    "qc": "qc",
    "quality": "qc",
    "marketing": "marketing_sales",
    "sales": "marketing_sales",
    "marketing_sales": "marketing_sales",
    "customer": "customer",
}

MAX_ORDER_PLANNING_DAYS = 60


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True)


def _json_loads(value, default):
    raw = clean_text(value)
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except Exception:
        return default
    return parsed if isinstance(parsed, type(default)) else default


def ops_role_for_user(user: dict) -> str:
    raw = clean_text((user or {}).get("role") or (user or {}).get("team_role")).lower()
    return OPS_ROLE_ALIASES.get(raw, "operations_manager")


def can_manage_workflow(user: dict) -> bool:
    return ops_role_for_user(user) == "operations_manager"


def can_edit_order(user: dict) -> bool:
    return ops_role_for_user(user) in {"operations_manager", "team_leader", "qc", "marketing_sales"}


def can_view_internal_workspace(user: dict) -> bool:
    return ops_role_for_user(user) != "customer"


def get_ops_conn() -> sqlite3.Connection:
    conn = get_connection()
    ensure_ops_tables(conn)
    return conn


def _slug_stage_key(name: str) -> str:
    raw = clean_text(name).lower()
    out = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return ("".join(out)).strip("-") or "stage"


def _parse_iso_date(value: str):
    raw = clean_text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except Exception:
        return None


def _new_access_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def ensure_ops_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            crm_contact_id INTEGER,
            name TEXT NOT NULL,
            company_name TEXT,
            primary_contact TEXT,
            email TEXT,
            phone TEXT,
            channel TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_workflow_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            is_default INTEGER DEFAULT 0,
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_workflow_template_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL,
            stage_name TEXT NOT NULL,
            stage_order INTEGER NOT NULL,
            department TEXT,
            default_checklist_json TEXT,
            customer_visible INTEGER DEFAULT 0,
            approval_required INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            client_id INTEGER,
            crm_contact_id INTEGER,
            workflow_template_id INTEGER,
            order_number TEXT,
            title TEXT NOT NULL,
            client_name TEXT,
            customer_name TEXT,
            customer_email TEXT,
            crm_connection TEXT,
            product_type TEXT,
            quantity INTEGER,
            order_summary TEXT,
            priority TEXT DEFAULT 'normal',
            planned_start_date TEXT,
            planned_ship_date TEXT,
            requested_delivery_date TEXT,
            revised_ship_date TEXT,
            delay_reason TEXT,
            current_stage_id INTEGER,
            current_stage_key TEXT,
            current_stage_name TEXT,
            last_update_summary TEXT,
            last_update_at TEXT,
            planned_duration_days INTEGER DEFAULT 0,
            today_schedule_label TEXT,
            overdue_day_count INTEGER DEFAULT 0,
            buffer_day_count INTEGER DEFAULT 0,
            schedule_health TEXT DEFAULT 'on_track',
            customer_access_code TEXT,
            customer_portal_active INTEGER DEFAULT 1,
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_order_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            template_stage_id INTEGER,
            stage_key TEXT NOT NULL,
            stage_name TEXT NOT NULL,
            stage_order INTEGER NOT NULL,
            department TEXT,
            customer_visible INTEGER DEFAULT 0,
            approval_required INTEGER DEFAULT 0,
            planned_start_date TEXT,
            planned_end_date TEXT,
            actual_start_date TEXT,
            actual_end_date TEXT,
            revised_end_date TEXT,
            responsible_person TEXT,
            checklist_json TEXT,
            status TEXT DEFAULT 'pending',
            rework_flag INTEGER DEFAULT 0,
            delay_reason TEXT,
            signature_name TEXT,
            signature_at TEXT,
            completion_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_daily_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            order_stage_id INTEGER,
            update_date TEXT,
            summary TEXT NOT NULL,
            completed_today TEXT,
            next_step TEXT,
            visibility TEXT DEFAULT 'internal',
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_order_schedule_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            order_stage_id INTEGER,
            day_index INTEGER NOT NULL,
            schedule_date TEXT NOT NULL,
            stage_key TEXT,
            stage_name TEXT,
            department TEXT,
            is_buffer_day INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(order_id, day_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_workflow_schedule_defaults (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            workflow_template_id INTEGER,
            duration_days INTEGER NOT NULL,
            name TEXT,
            schedule_json TEXT NOT NULL,
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, workflow_template_id, duration_days)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            order_stage_id INTEGER,
            issue_type TEXT DEFAULT 'delay',
            severity TEXT DEFAULT 'medium',
            summary TEXT NOT NULL,
            reason TEXT,
            reported_by TEXT,
            original_due_date TEXT,
            revised_due_date TEXT,
            status TEXT DEFAULT 'open',
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            order_stage_id INTEGER,
            file_name TEXT NOT NULL,
            file_path TEXT,
            file_type TEXT,
            visible_to_customer INTEGER DEFAULT 0,
            uploaded_by_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            order_stage_id INTEGER,
            approval_type TEXT,
            requested_from_role TEXT,
            status TEXT DEFAULT 'pending',
            approved_by_name TEXT,
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            acted_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            order_stage_id INTEGER,
            author_role TEXT,
            author_name TEXT,
            message TEXT NOT NULL,
            is_customer_visible INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_automation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            order_id INTEGER,
            event_type TEXT NOT NULL,
            event_title TEXT NOT NULL,
            event_detail TEXT,
            status TEXT DEFAULT 'logged',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_sample_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            client_id INTEGER,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            due_date TEXT,
            notes TEXT,
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_order_brand_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            manufacturer_workspace_id TEXT NOT NULL,
            brand_owner_workspace_id TEXT NOT NULL,
            granted_by_user_id INTEGER,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(order_id, brand_owner_workspace_id)
        )
        """
    )
    order_columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_orders)").fetchall()}
    if "customer_access_code" not in order_columns:
        conn.execute("ALTER TABLE ops_orders ADD COLUMN customer_access_code TEXT")
    if "customer_portal_active" not in order_columns:
        conn.execute("ALTER TABLE ops_orders ADD COLUMN customer_portal_active INTEGER DEFAULT 1")
    if "planned_duration_days" not in order_columns:
        conn.execute("ALTER TABLE ops_orders ADD COLUMN planned_duration_days INTEGER DEFAULT 0")
    if "today_schedule_label" not in order_columns:
        conn.execute("ALTER TABLE ops_orders ADD COLUMN today_schedule_label TEXT")
    if "overdue_day_count" not in order_columns:
        conn.execute("ALTER TABLE ops_orders ADD COLUMN overdue_day_count INTEGER DEFAULT 0")
    if "buffer_day_count" not in order_columns:
        conn.execute("ALTER TABLE ops_orders ADD COLUMN buffer_day_count INTEGER DEFAULT 0")
    if "schedule_health" not in order_columns:
        conn.execute("ALTER TABLE ops_orders ADD COLUMN schedule_health TEXT DEFAULT 'on_track'")
    client_columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_clients)").fetchall()}
    if "crm_contact_id" not in client_columns:
        conn.execute("ALTER TABLE ops_clients ADD COLUMN crm_contact_id INTEGER")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_clients_workspace ON ops_clients(workspace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_orders_workspace ON ops_orders(workspace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_orders_stage ON ops_orders(workspace_id, current_stage_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_orders_customer_code ON ops_orders(customer_access_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_order_stages_order ON ops_order_stages(order_id, stage_order)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_schedule_order_day ON ops_order_schedule_days(order_id, day_index)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_schedule_order_date ON ops_order_schedule_days(order_id, schedule_date)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_schedule_defaults_workspace_duration ON ops_workflow_schedule_defaults(workspace_id, workflow_template_id, duration_days)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_updates_order ON ops_daily_updates(order_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_issues_order ON ops_issues(order_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_sample_requests_workspace ON ops_sample_requests(workspace_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_automation_workspace ON ops_automation_events(workspace_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_order_brand_access_bw ON ops_order_brand_access(brand_owner_workspace_id, status)")


def ensure_default_workflow_template(conn, workspace_id: str, created_by_user_id: int = 0) -> int:
    workspace_id = clean_text(workspace_id)
    existing = conn.execute(
        """
        SELECT id
        FROM ops_workflow_templates
        WHERE workspace_id=? AND is_default=1
        ORDER BY id ASC
        LIMIT 1
        """,
        (workspace_id,),
    ).fetchone()
    if existing:
        template_id = int(existing["id"])
    else:
        cur = conn.execute(
            """
            INSERT INTO ops_workflow_templates(workspace_id, name, description, is_default, created_by_user_id)
            VALUES(?, ?, ?, 1, ?)
            """,
            (
                workspace_id,
                "Default Production Workflow",
                "Editable production workflow seeded for order tracking MVP.",
                int(created_by_user_id or 0) or None,
            ),
        )
        template_id = int(cur.lastrowid)

    stage_count = conn.execute(
        "SELECT COUNT(*) AS c FROM ops_workflow_template_stages WHERE template_id=?",
        (template_id,),
    ).fetchone()
    if int(stage_count["c"] or 0) == 0:
        for index, stage in enumerate(DEFAULT_WORKFLOW_STAGES, start=1):
            conn.execute(
                """
                INSERT INTO ops_workflow_template_stages(
                    template_id, stage_key, stage_name, stage_order, department,
                    default_checklist_json, customer_visible, approval_required, active
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    template_id,
                    stage["key"],
                    stage["name"],
                    index,
                    stage.get("department"),
                    _json_dumps(stage.get("checklist") or []),
                    int(stage.get("customer_visible") or 0),
                    int(stage.get("approval_required") or 0),
                ),
            )
    return template_id


def _stage_rows_for_order(conn, order_id: int):
    return conn.execute(
        """
        SELECT *
        FROM ops_order_stages
        WHERE order_id=?
        ORDER BY stage_order ASC, id ASC
        """,
        (int(order_id),),
    ).fetchall()


def _order_row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def _order_schedule_rows(conn, order_id: int):
    return conn.execute(
        """
        SELECT *
        FROM ops_order_schedule_days
        WHERE order_id=?
        ORDER BY day_index ASC, id ASC
        """,
        (int(order_id),),
    ).fetchall()


def _saved_schedule_default(conn, workspace_id: str, workflow_template_id: int, duration_days: int):
    if int(duration_days or 0) <= 0:
        return None
    return conn.execute(
        """
        SELECT *
        FROM ops_workflow_schedule_defaults
        WHERE workspace_id=?
          AND workflow_template_id=?
          AND duration_days=?
        LIMIT 1
        """,
        (clean_text(workspace_id), int(workflow_template_id or 0), int(duration_days)),
    ).fetchone()


def _normalize_schedule_assignments(stage_rows, duration_days: int, saved_pattern: list[dict] | None = None) -> list[dict]:
    stages = [row for row in stage_rows]
    total_days = max(int(duration_days or 0), 0)
    if total_days <= 0:
        return []

    if saved_pattern and len(saved_pattern) == total_days:
        by_key = {clean_text(row["stage_key"]): row for row in stages}
        assignments = []
        for index in range(total_days):
            item = saved_pattern[index] if index < len(saved_pattern) else {}
            stage_key = clean_text((item or {}).get("stage_key"))
            stage = by_key.get(stage_key)
            is_buffer = 1 if str((item or {}).get("is_buffer_day")).lower() in {"1", "true", "yes", "on"} or stage is None else 0
            assignments.append(
                {
                    "day_index": index + 1,
                    "order_stage_id": int(stage["id"]) if stage is not None and not is_buffer else None,
                    "stage_key": clean_text(stage["stage_key"]) if stage is not None and not is_buffer else "",
                    "stage_name": clean_text(stage["stage_name"]) if stage is not None and not is_buffer else "",
                    "department": clean_text(stage["department"]) if stage is not None and not is_buffer else "",
                    "is_buffer_day": is_buffer,
                    "notes": clean_text((item or {}).get("notes")),
                }
            )
        return assignments

    if not stages:
        return [
            {
                "day_index": idx + 1,
                "order_stage_id": None,
                "stage_key": "",
                "stage_name": "",
                "department": "",
                "is_buffer_day": 1,
                "notes": "",
            }
            for idx in range(total_days)
        ]

    assignments: list[dict] = []
    if total_days < len(stages):
        for idx in range(total_days):
            stage = stages[idx]
            assignments.append(
                {
                    "day_index": idx + 1,
                    "order_stage_id": int(stage["id"]),
                    "stage_key": clean_text(stage["stage_key"]),
                    "stage_name": clean_text(stage["stage_name"]),
                    "department": clean_text(stage["department"]),
                    "is_buffer_day": 0,
                    "notes": "",
                }
            )
        return assignments

    base_days = total_days // len(stages)
    remaining_days = total_days - (base_days * len(stages))
    for stage in stages:
        for _ in range(base_days):
            assignments.append(
                {
                    "day_index": len(assignments) + 1,
                    "order_stage_id": int(stage["id"]),
                    "stage_key": clean_text(stage["stage_key"]),
                    "stage_name": clean_text(stage["stage_name"]),
                    "department": clean_text(stage["department"]),
                    "is_buffer_day": 0,
                    "notes": "",
                }
            )
    for _ in range(remaining_days):
        assignments.append(
            {
                "day_index": len(assignments) + 1,
                "order_stage_id": None,
                "stage_key": "",
                "stage_name": "",
                "department": "",
                "is_buffer_day": 1,
                "notes": "",
            }
        )
    return assignments[:total_days]


def _write_order_schedule(conn, order_id: int, start_date: str, assignments: list[dict]) -> None:
    start_dt = _parse_iso_date(start_date)
    if start_dt is None:
        raise ValueError("Planned start date is required before building the production calendar.")
    conn.execute("DELETE FROM ops_order_schedule_days WHERE order_id=?", (int(order_id),))
    for index, item in enumerate(assignments):
        schedule_date = (start_dt + timedelta(days=index)).isoformat()
        is_buffer = 1 if int(item.get("is_buffer_day") or 0) == 1 else 0
        conn.execute(
            """
            INSERT INTO ops_order_schedule_days(
                order_id, order_stage_id, day_index, schedule_date, stage_key, stage_name, department, is_buffer_day, notes, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                int(order_id),
                int(item.get("order_stage_id") or 0) or None,
                index + 1,
                schedule_date,
                clean_text(item.get("stage_key")) if not is_buffer else "",
                clean_text(item.get("stage_name")) if not is_buffer else "",
                clean_text(item.get("department")) if not is_buffer else "",
                is_buffer,
                clean_text(item.get("notes")),
            ),
        )


def _recompute_stage_plan_from_schedule(conn, order_id: int) -> None:
    stage_rows = _stage_rows_for_order(conn, order_id)
    schedule_rows = _order_schedule_rows(conn, order_id)
    by_stage: dict[int, list[str]] = {}
    for row in schedule_rows:
        stage_id = int(row["order_stage_id"] or 0)
        if stage_id <= 0 or int(row["is_buffer_day"] or 0) == 1:
            continue
        by_stage.setdefault(stage_id, []).append(clean_text(row["schedule_date"]))

    for stage in stage_rows:
        dates = sorted(d for d in by_stage.get(int(stage["id"]), []) if d)
        planned_start = dates[0] if dates else ""
        planned_end = dates[-1] if dates else ""
        conn.execute(
            """
            UPDATE ops_order_stages
            SET planned_start_date=?,
                planned_end_date=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (planned_start, planned_end, int(stage["id"])),
        )

    final_row = schedule_rows[-1] if schedule_rows else None
    final_date = clean_text(final_row["schedule_date"]) if final_row else ""
    duration_days = len(schedule_rows)
    conn.execute(
        """
        UPDATE ops_orders
        SET planned_duration_days=?,
            requested_delivery_date=CASE WHEN ?<>'' THEN ? ELSE requested_delivery_date END,
            planned_ship_date=CASE WHEN ?<>'' THEN ? ELSE planned_ship_date END,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            int(duration_days),
            final_date,
            final_date,
            final_date,
            final_date,
            int(order_id),
        ),
    )


def auto_build_order_schedule(conn, workspace_id: str, order_id: int, user_id: int, *, duration_days: int = 0, use_saved_default: bool = True) -> int:
    order = conn.execute(
        """
        SELECT id, workspace_id, workflow_template_id, planned_start_date, planned_duration_days
        FROM ops_orders
        WHERE id=? AND workspace_id=?
        LIMIT 1
        """,
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    start_date = clean_text(order["planned_start_date"])
    if not start_date:
        raise ValueError("Order needs a planned start date before scheduling.")
    duration = int(duration_days or order["planned_duration_days"] or 0)
    if duration < 1:
        raise ValueError("Order needs completion days before scheduling.")
    if duration > MAX_ORDER_PLANNING_DAYS:
        raise ValueError(f"Order planning currently supports up to {MAX_ORDER_PLANNING_DAYS} days.")
    stage_rows = _stage_rows_for_order(conn, order_id)
    saved_pattern = None
    if use_saved_default:
        default_row = _saved_schedule_default(conn, workspace_id, int(order["workflow_template_id"] or 0), duration)
        if default_row is not None:
            saved_pattern = _json_loads(default_row["schedule_json"], [])
    assignments = _normalize_schedule_assignments(stage_rows, duration, saved_pattern)
    _write_order_schedule(conn, order_id, start_date, assignments)
    _recompute_stage_plan_from_schedule(conn, order_id)
    _log_automation(
        conn,
        workspace_id,
        order_id,
        "schedule_built",
        "Production calendar ready",
        f"Auto-distributed {duration} planning days across the workflow.",
    )
    refresh_order_rollup(conn, order_id)
    return len(assignments)


def _schedule_rows_with_state(conn, order_id: int) -> list[dict]:
    today_iso = date.today().isoformat()
    stages = {int(row["id"]): row for row in _stage_rows_for_order(conn, order_id)}
    out = []
    for row in _order_schedule_rows(conn, order_id):
        item = {k: row[k] for k in row.keys()}
        stage = stages.get(int(row["order_stage_id"] or 0))
        stage_status = clean_text(stage["status"] if stage is not None else "").lower()
        schedule_date = clean_text(row["schedule_date"])
        is_buffer = int(row["is_buffer_day"] or 0) == 1
        is_completed = (not is_buffer) and stage_status == "completed"
        is_overdue = (not is_buffer) and bool(schedule_date) and schedule_date < today_iso and not is_completed
        is_today = bool(schedule_date) and schedule_date == today_iso
        state = "buffer" if is_buffer else "completed" if is_completed else "overdue" if is_overdue else "today" if is_today else "planned"
        item.update(
            {
                "id": int(row["id"]),
                "order_stage_id": int(row["order_stage_id"] or 0),
                "is_buffer_day": 1 if is_buffer else 0,
                "is_completed": 1 if is_completed else 0,
                "is_overdue": 1 if is_overdue else 0,
                "is_today": 1 if is_today else 0,
                "state": state,
            }
        )
        out.append(item)
    return out


def _schedule_metrics(conn, order_id: int) -> dict:
    rows = _schedule_rows_with_state(conn, order_id)
    today_row = next((row for row in rows if int(row["is_today"] or 0) == 1), None)
    overdue_days = sum(1 for row in rows if int(row.get("is_overdue") or 0) == 1)
    buffer_days = sum(1 for row in rows if int(row.get("is_buffer_day") or 0) == 1)
    label = ""
    if today_row is not None:
        label = clean_text(today_row.get("stage_name")) or "Buffer / Free Day"
    elif overdue_days > 0:
        latest_missed = next((row for row in reversed(rows) if int(row.get("is_overdue") or 0) == 1), None)
        label = f"Missed: {clean_text((latest_missed or {}).get('stage_name')) or 'Planned process'}" if latest_missed else ""
    return {
        "rows": rows,
        "planned_duration_days": len(rows),
        "buffer_day_count": buffer_days,
        "overdue_day_count": overdue_days,
        "today_schedule_label": label,
        "schedule_health": "overdue" if overdue_days > 0 else "on_track",
    }


def save_order_schedule(conn, workspace_id: str, order_id: int, user_id: int, payload: dict) -> int:
    order = conn.execute(
        """
        SELECT id, planned_start_date, planned_duration_days
        FROM ops_orders
        WHERE id=? AND workspace_id=?
        LIMIT 1
        """,
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    start_date = clean_text(order["planned_start_date"])
    if not start_date:
        raise ValueError("Order needs a planned start date before saving the calendar.")
    raw_rows = payload.get("schedule_days") or []
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("At least one planning day is required.")
    if len(raw_rows) > MAX_ORDER_PLANNING_DAYS:
        raise ValueError(f"Order planning currently supports up to {MAX_ORDER_PLANNING_DAYS} days.")
    stage_rows = _stage_rows_for_order(conn, order_id)
    stage_map = {int(row["id"]): row for row in stage_rows}
    assignments = []
    for index, item in enumerate(raw_rows):
        stage_id = int((item or {}).get("order_stage_id") or 0)
        is_buffer = 1 if str((item or {}).get("is_buffer_day")).lower() in {"1", "true", "yes", "on"} else 0
        stage = stage_map.get(stage_id)
        if stage is None:
            is_buffer = 1
        assignments.append(
            {
                "day_index": index + 1,
                "order_stage_id": int(stage["id"]) if stage is not None and not is_buffer else None,
                "stage_key": clean_text(stage["stage_key"]) if stage is not None and not is_buffer else "",
                "stage_name": clean_text(stage["stage_name"]) if stage is not None and not is_buffer else "",
                "department": clean_text(stage["department"]) if stage is not None and not is_buffer else "",
                "is_buffer_day": is_buffer,
                "notes": clean_text((item or {}).get("notes")),
            }
        )
    _write_order_schedule(conn, order_id, start_date, assignments)
    _recompute_stage_plan_from_schedule(conn, order_id)
    _log_automation(conn, workspace_id, order_id, "schedule_saved", "Production calendar updated", f"{len(assignments)} planning days saved.")
    refresh_order_rollup(conn, order_id)
    return len(assignments)


def _sync_schedule_stage_metadata(conn, order_id: int) -> None:
    stage_rows = {int(row["id"]): row for row in _stage_rows_for_order(conn, order_id)}
    for row in _order_schedule_rows(conn, order_id):
        stage = stage_rows.get(int(row["order_stage_id"] or 0))
        is_buffer = int(row["is_buffer_day"] or 0) == 1 or stage is None
        conn.execute(
            """
            UPDATE ops_order_schedule_days
            SET stage_key=?,
                stage_name=?,
                department=?,
                is_buffer_day=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                clean_text(stage["stage_key"]) if stage is not None and not is_buffer else "",
                clean_text(stage["stage_name"]) if stage is not None and not is_buffer else "",
                clean_text(stage["department"]) if stage is not None and not is_buffer else "",
                1 if is_buffer else 0,
                int(row["id"]),
            ),
        )


def save_order_processes(conn, workspace_id: str, order_id: int, user_id: int, payload: dict) -> list[dict]:
    ws = clean_text(workspace_id)
    order = conn.execute(
        "SELECT id, order_number FROM ops_orders WHERE id=? AND workspace_id=? LIMIT 1",
        (int(order_id), ws),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    stages = payload.get("stages") or []
    if not isinstance(stages, list) or not stages:
        raise ValueError("At least one process is required.")

    existing_rows = {int(row["id"]): row for row in _stage_rows_for_order(conn, order_id)}
    seen_stage_ids: set[int] = set()
    order_index = 1
    for item in stages:
        stage_name = clean_text((item or {}).get("stage_name"))
        if not stage_name:
            continue
        department = clean_text((item or {}).get("department"))
        stage_id_raw = clean_text((item or {}).get("id"))
        if stage_id_raw.isdigit() and int(stage_id_raw) in existing_rows:
            stage_id = int(stage_id_raw)
            seen_stage_ids.add(stage_id)
            conn.execute(
                """
                UPDATE ops_order_stages
                SET stage_name=?,
                    department=?,
                    stage_order=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND order_id=?
                """,
                (stage_name, department, order_index, stage_id, int(order_id)),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO ops_order_stages(
                    order_id, template_stage_id, stage_key, stage_name, stage_order, department,
                    customer_visible, approval_required, checklist_json, status
                )
                VALUES(?, NULL, ?, ?, ?, ?, 0, 0, '[]', 'pending')
                """,
                (
                    int(order_id),
                    _slug_stage_key(stage_name),
                    stage_name,
                    order_index,
                    department,
                ),
            )
            seen_stage_ids.add(int(cur.lastrowid))
        order_index += 1

    for row in _stage_rows_for_order(conn, order_id):
        row_id = int(row["id"])
        if row_id in seen_stage_ids:
            continue
        conn.execute(
            """
            UPDATE ops_order_stages
            SET stage_order=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (order_index, row_id),
        )
        order_index += 1

    _sync_schedule_stage_metadata(conn, order_id)
    _recompute_stage_plan_from_schedule(conn, order_id)
    _log_automation(conn, ws, order_id, "order_processes_updated", "Order process list updated", f"Processes updated for {clean_text(order['order_number'])}.")
    refresh_order_rollup(conn, order_id)
    return [
        {**_order_row_to_dict(stage), "id": int(stage["id"]), "checklist": _json_loads(stage["checklist_json"], [])}
        for stage in _stage_rows_for_order(conn, order_id)
    ]


def save_schedule_default(conn, workspace_id: str, order_id: int, user_id: int, name: str = "") -> dict:
    order = conn.execute(
        """
        SELECT id, workflow_template_id, planned_duration_days
        FROM ops_orders
        WHERE id=? AND workspace_id=?
        LIMIT 1
        """,
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    schedule_rows = _order_schedule_rows(conn, order_id)
    if not schedule_rows:
        raise ValueError("Build the production calendar before saving a default.")
    pattern = [
        {
            "day_index": int(row["day_index"]),
            "stage_key": clean_text(row["stage_key"]),
            "is_buffer_day": int(row["is_buffer_day"] or 0),
            "notes": clean_text(row["notes"]),
        }
        for row in schedule_rows
    ]
    duration_days = len(pattern)
    conn.execute(
        """
        INSERT INTO ops_workflow_schedule_defaults(
            workspace_id, workflow_template_id, duration_days, name, schedule_json, created_by_user_id, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(workspace_id, workflow_template_id, duration_days)
        DO UPDATE SET
            name=excluded.name,
            schedule_json=excluded.schedule_json,
            created_by_user_id=excluded.created_by_user_id,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            clean_text(workspace_id),
            int(order["workflow_template_id"] or 0) or None,
            int(duration_days),
            clean_text(name) or f"{duration_days}-day default",
            _json_dumps(pattern),
            int(user_id or 0) or None,
        ),
    )
    _log_automation(conn, workspace_id, order_id, "schedule_default_saved", "Scheduling default saved", f"Saved the {duration_days}-day calendar as the workspace default.")
    return {"duration_days": duration_days, "name": clean_text(name) or f"{duration_days}-day default"}


def order_planner_payload(conn, workspace_id: str, order_id: int) -> dict | None:
    detail = get_order_detail(conn, workspace_id, order_id)
    if detail is None:
        return None
    order = detail["order"]
    schedule = _schedule_metrics(conn, order_id)
    default_row = _saved_schedule_default(conn, clean_text(workspace_id), int(order.get("workflow_template_id") or 0), int(order.get("planned_duration_days") or 0))
    return {
        **detail,
        "schedule_days": schedule["rows"],
        "schedule_summary": {
            "planned_duration_days": schedule["planned_duration_days"],
            "buffer_day_count": schedule["buffer_day_count"],
            "overdue_day_count": schedule["overdue_day_count"],
            "today_schedule_label": schedule["today_schedule_label"],
            "schedule_health": schedule["schedule_health"],
            "max_duration_days": MAX_ORDER_PLANNING_DAYS,
            "default_available": 1 if default_row is not None else 0,
        },
    }


def refresh_order_rollup(conn, order_id: int) -> None:
    stages = _stage_rows_for_order(conn, order_id)
    if not stages:
        return
    current = None
    for row in stages:
        if clean_text(row["status"]).lower() != "completed":
            current = row
            break
    if current is None:
        current = stages[-1]

    latest_update = _latest_daily_update(conn, order_id)
    latest_issue = _latest_open_issue(conn, order_id)
    schedule = _schedule_metrics(conn, order_id)
    revised_ship_date = clean_text(latest_issue["revised_due_date"]) if latest_issue else ""
    delay_reason = clean_text(latest_issue["reason"] or latest_issue["summary"]) if latest_issue else ""
    if not delay_reason and int(schedule["overdue_day_count"] or 0) > 0:
        latest_missed = next((row for row in reversed(schedule["rows"]) if int(row.get("is_overdue") or 0) == 1), None)
        missed_date = clean_text((latest_missed or {}).get("schedule_date"))
        delay_reason = f"Missed planned day on {missed_date}" if missed_date else "Planned production day missed."

    conn.execute(
        """
        UPDATE ops_orders
        SET current_stage_id=?,
            current_stage_key=?,
            current_stage_name=?,
            today_schedule_label=?,
            overdue_day_count=?,
            buffer_day_count=?,
            schedule_health=?,
            planned_duration_days=?,
            revised_ship_date=?,
            delay_reason=?,
            last_update_summary=?,
            last_update_at=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            int(current["id"]),
            clean_text(current["stage_key"]),
            clean_text(current["stage_name"]),
            clean_text(schedule["today_schedule_label"]),
            int(schedule["overdue_day_count"] or 0),
            int(schedule["buffer_day_count"] or 0),
            clean_text(schedule["schedule_health"]) or "on_track",
            int(schedule["planned_duration_days"] or 0),
            revised_ship_date,
            delay_reason,
            clean_text(latest_update["summary"]) if latest_update else "",
            clean_text(latest_update["created_at"]) if latest_update else "",
            int(order_id),
        ),
    )


def _log_automation(conn, workspace_id: str, order_id: int | None, event_type: str, title: str, detail: str = "") -> None:
    conn.execute(
        """
        INSERT INTO ops_automation_events(workspace_id, order_id, event_type, event_title, event_detail)
        VALUES(?, ?, ?, ?, ?)
        """,
        (clean_text(workspace_id), int(order_id or 0) or None, clean_text(event_type), clean_text(title), clean_text(detail)),
    )


def create_or_get_client(
    conn,
    workspace_id: str,
    *,
    crm_contact_id: int = 0,
    name: str,
    company_name: str = "",
    primary_contact: str = "",
    email: str = "",
    phone: str = "",
    channel: str = "",
    notes: str = "",
    created_by_user_id: int = 0,
) -> int:
    ws = clean_text(workspace_id)
    client_name = clean_text(name)
    crm_id = int(crm_contact_id or 0)
    if crm_id > 0:
        existing = conn.execute(
            """
            SELECT id
            FROM ops_clients
            WHERE workspace_id=? AND crm_contact_id=?
            ORDER BY id ASC
            LIMIT 1
            """,
            (ws, crm_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE ops_clients
                SET name=CASE WHEN ?<>'' THEN ? ELSE name END,
                    company_name=CASE WHEN ?<>'' THEN ? ELSE company_name END,
                    primary_contact=CASE WHEN ?<>'' THEN ? ELSE primary_contact END,
                    email=CASE WHEN ?<>'' THEN ? ELSE email END,
                    phone=CASE WHEN ?<>'' THEN ? ELSE phone END,
                    channel=CASE WHEN ?<>'' THEN ? ELSE channel END,
                    notes=CASE WHEN ?<>'' THEN ? ELSE notes END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    client_name,
                    client_name,
                    clean_text(company_name),
                    clean_text(company_name),
                    clean_text(primary_contact),
                    clean_text(primary_contact),
                    clean_text(email),
                    clean_text(email),
                    clean_text(phone),
                    clean_text(phone),
                    clean_text(channel),
                    clean_text(channel),
                    clean_text(notes),
                    clean_text(notes),
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])

    existing = conn.execute(
        """
        SELECT id
        FROM ops_clients
        WHERE workspace_id=? AND lower(name)=lower(?)
        ORDER BY id ASC
        LIMIT 1
        """,
        (ws, client_name),
    ).fetchone()
    if existing:
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO ops_clients(workspace_id, crm_contact_id, name, company_name, primary_contact, email, phone, channel, notes, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            ws,
            int(crm_id) or None,
            client_name,
            clean_text(company_name),
            clean_text(primary_contact),
            clean_text(email),
            clean_text(phone),
            clean_text(channel),
            clean_text(notes),
        ),
    )
    return int(cur.lastrowid)


def _next_order_number(conn, workspace_id: str) -> str:
    rows = conn.execute(
        """
        SELECT order_number
        FROM ops_orders
        WHERE workspace_id=? AND order_number LIKE 'ORD-%'
        ORDER BY id DESC
        LIMIT 50
        """,
        (clean_text(workspace_id),),
    ).fetchall()
    max_seq = 0
    for row in rows:
        raw = clean_text(row["order_number"])
        if raw.upper().startswith("ORD-"):
            suffix = raw.split("ORD-", 1)[-1]
            if suffix.isdigit():
                max_seq = max(max_seq, int(suffix))
    return f"ORD-{max_seq + 1:05d}"


def create_order(conn, workspace_id: str, user_id: int, payload: dict) -> int:
    ws = clean_text(workspace_id)
    ensure_ops_tables(conn)
    template_id = ensure_default_workflow_template(conn, ws, user_id)
    title = clean_text(payload.get("title"))
    if not title:
        raise ValueError("Order title is required.")

    crm_contact_id = int(clean_text(payload.get("crm_contact_id") or 0) or 0)
    client_name = clean_text(payload.get("client_name"))
    crm_connection = ""
    client_notes = ""
    if crm_contact_id > 0:
        crm_row = conn.execute(
            "SELECT name, connection, notes FROM crm_contacts WHERE id=? AND workspace_id=?",
            (crm_contact_id, ws),
        ).fetchone()
        if crm_row:
            if not client_name:
                client_name = clean_text(crm_row["name"])
            crm_connection = clean_text(crm_row["connection"])
            client_notes = clean_text(crm_row["notes"])

    if not client_name:
        client_name = clean_text(payload.get("customer_name")) or "Client"

    client_id = create_or_get_client(
        conn,
        ws,
        crm_contact_id=crm_contact_id,
        name=client_name,
        notes=client_notes,
        created_by_user_id=user_id,
    )

    order_number = clean_text(payload.get("order_number")) or _next_order_number(conn, ws)
    access_code = _new_access_code()
    planned_start_date = clean_text(payload.get("planned_start_date"))
    planned_duration_days = int(clean_text(payload.get("planned_duration_days") or 0) or 0)
    if planned_duration_days > MAX_ORDER_PLANNING_DAYS:
        raise ValueError(f"Order planning currently supports up to {MAX_ORDER_PLANNING_DAYS} days.")

    cur = conn.execute(
        """
        INSERT INTO ops_orders(
            workspace_id, client_id, crm_contact_id, workflow_template_id, order_number,
            title, client_name, customer_name, customer_email, crm_connection,
            product_type, quantity, order_summary, priority,
            planned_start_date, requested_delivery_date, planned_duration_days,
            customer_access_code, customer_portal_active, created_by_user_id, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            ws,
            int(client_id),
            int(crm_contact_id) or None,
            int(template_id),
            order_number,
            title,
            client_name,
            clean_text(payload.get("customer_name")),
            clean_text(payload.get("customer_email")),
            crm_connection,
            clean_text(payload.get("product_type")),
            int(clean_text(payload.get("quantity") or 0) or 0) or None,
            clean_text(payload.get("order_summary")),
            clean_text(payload.get("priority")) or "normal",
            planned_start_date,
            clean_text(payload.get("requested_delivery_date")),
            planned_duration_days,
            access_code,
            int(user_id or 0) or None,
        ),
    )
    order_id = int(cur.lastrowid)

    template_stages = conn.execute(
        """
        SELECT *
        FROM ops_workflow_template_stages
        WHERE template_id=? AND active=1
        ORDER BY stage_order ASC, id ASC
        """,
        (template_id,),
    ).fetchall()
    for index, stage in enumerate(template_stages, start=1):
        conn.execute(
            """
            INSERT INTO ops_order_stages(
                order_id, template_stage_id, stage_key, stage_name, stage_order, department,
                customer_visible, approval_required, checklist_json, status
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                order_id,
                int(stage["id"]),
                clean_text(stage["stage_key"]),
                clean_text(stage["stage_name"]),
                index,
                clean_text(stage["department"]),
                int(stage["customer_visible"] or 0),
                int(stage["approval_required"] or 0),
                clean_text(stage["default_checklist_json"]) or "[]",
            ),
        )

    first_stage = conn.execute(
        "SELECT id, stage_key, stage_name FROM ops_order_stages WHERE order_id=? ORDER BY stage_order ASC, id ASC LIMIT 1",
        (order_id,),
    ).fetchone()
    if first_stage:
        conn.execute(
            """
            UPDATE ops_orders
            SET current_stage_id=?,
                current_stage_key=?,
                current_stage_name=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                int(first_stage["id"]),
                clean_text(first_stage["stage_key"]),
                clean_text(first_stage["stage_name"]),
                order_id,
            ),
        )

    if planned_start_date and planned_duration_days > 0:
        auto_build_order_schedule(conn, ws, order_id, user_id, duration_days=planned_duration_days, use_saved_default=True)

    refresh_order_rollup(conn, order_id)
    return order_id


def _latest_daily_update(conn, order_id: int):
    return conn.execute(
        """
        SELECT summary, created_at
        FROM ops_daily_updates
        WHERE order_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (int(order_id),),
    ).fetchone()


def _latest_open_issue(conn, order_id: int):
    return conn.execute(
        """
        SELECT summary, reason, revised_due_date
        FROM ops_issues
        WHERE order_id=?
          AND lower(coalesce(status,''))='open'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (int(order_id),),
    ).fetchone()


def _ensure_access_code(conn, order_id: int):
    row = conn.execute(
        "SELECT customer_access_code, customer_portal_active FROM ops_orders WHERE id=?",
        (int(order_id),),
    ).fetchone()
    if row is None:
        return None
    if clean_text(row["customer_access_code"]):
        return row
    new_code = _new_access_code()
    conn.execute(
        """
        UPDATE ops_orders
        SET customer_access_code=?,
            customer_portal_active=1,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (new_code, int(order_id)),
    )
    return conn.execute(
        "SELECT customer_access_code, customer_portal_active FROM ops_orders WHERE id=?",
        (int(order_id),),
    ).fetchone()


def get_order_detail(conn, workspace_id: str, order_id: int) -> dict | None:
    ws = clean_text(workspace_id)
    order = conn.execute(
        "SELECT * FROM ops_orders WHERE id=? AND workspace_id=?",
        (int(order_id), ws),
    ).fetchone()
    if order is None:
        return None
    _ensure_access_code(conn, order_id)
    stages = _stage_rows_for_order(conn, order_id)
    updates = conn.execute(
        "SELECT * FROM ops_daily_updates WHERE order_id=? ORDER BY created_at DESC, id DESC",
        (int(order_id),),
    ).fetchall()
    issues = conn.execute(
        "SELECT * FROM ops_issues WHERE order_id=? ORDER BY created_at DESC, id DESC",
        (int(order_id),),
    ).fetchall()
    comments = conn.execute(
        "SELECT * FROM ops_comments WHERE order_id=? ORDER BY created_at ASC, id ASC",
        (int(order_id),),
    ).fetchall()
    files = conn.execute(
        "SELECT * FROM ops_files WHERE order_id=? ORDER BY created_at DESC, id DESC",
        (int(order_id),),
    ).fetchall()
    approvals = conn.execute(
        "SELECT * FROM ops_approvals WHERE order_id=? ORDER BY created_at DESC, id DESC",
        (int(order_id),),
    ).fetchall()

    schedule = _schedule_metrics(conn, order_id)
    order_dict = _order_row_to_dict(order)
    order_dict["today_schedule_label"] = clean_text(order_dict.get("today_schedule_label")) or clean_text(schedule["today_schedule_label"])
    order_dict["overdue_day_count"] = int(order_dict.get("overdue_day_count") or schedule["overdue_day_count"] or 0)
    order_dict["buffer_day_count"] = int(order_dict.get("buffer_day_count") or schedule["buffer_day_count"] or 0)
    order_dict["schedule_health"] = clean_text(order_dict.get("schedule_health")) or clean_text(schedule["schedule_health"]) or "on_track"

    return {
        "order": order_dict,
        "stages": [{**_order_row_to_dict(row), "checklist": _json_loads(row["checklist_json"], [])} for row in stages],
        "updates": [_order_row_to_dict(row) for row in updates],
        "issues": [_order_row_to_dict(row) for row in issues],
        "comments": [_order_row_to_dict(row) for row in comments],
        "files": [_order_row_to_dict(row) for row in files],
        "approvals": [_order_row_to_dict(row) for row in approvals],
        "schedule_days": schedule["rows"],
    }


def update_stage(conn, workspace_id: str, order_id: int, stage_id: int, payload: dict) -> None:
    order = conn.execute(
        "SELECT id FROM ops_orders WHERE id=? AND workspace_id=?",
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    conn.execute(
        """
        UPDATE ops_order_stages
        SET status=?,
            actual_start_date=?,
            actual_end_date=?,
            revised_end_date=?,
            responsible_person=?,
            delay_reason=?,
            completion_note=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND order_id=?
        """,
        (
            clean_text(payload.get("status")) or "pending",
            clean_text(payload.get("actual_start_date")),
            clean_text(payload.get("actual_end_date")),
            clean_text(payload.get("revised_end_date")),
            clean_text(payload.get("responsible_person")),
            clean_text(payload.get("delay_reason")),
            clean_text(payload.get("completion_note")),
            int(stage_id),
            int(order_id),
        ),
    )
    refresh_order_rollup(conn, order_id)


def add_daily_update(conn, workspace_id: str, order_id: int, user_id: int, payload: dict) -> int:
    summary = clean_text(payload.get("summary"))
    if not summary:
        raise ValueError("Summary is required.")
    order = conn.execute(
        "SELECT id FROM ops_orders WHERE id=? AND workspace_id=?",
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    update_date = clean_text(payload.get("update_date")) or date.today().isoformat()
    cur = conn.execute(
        """
        INSERT INTO ops_daily_updates(order_id, order_stage_id, update_date, summary, completed_today, next_step, visibility, created_by_user_id)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(order_id),
            int(clean_text(payload.get("order_stage_id") or 0) or 0) or None,
            update_date,
            summary,
            clean_text(payload.get("completed_today")),
            clean_text(payload.get("next_step")),
            clean_text(payload.get("visibility")) or "internal",
            int(user_id or 0) or None,
        ),
    )
    refresh_order_rollup(conn, order_id)
    return int(cur.lastrowid)


def add_issue(conn, workspace_id: str, order_id: int, user_id: int, payload: dict) -> int:
    summary = clean_text(payload.get("summary"))
    if not summary:
        raise ValueError("Issue summary is required.")
    order = conn.execute(
        "SELECT id, requested_delivery_date FROM ops_orders WHERE id=? AND workspace_id=?",
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    cur = conn.execute(
        """
        INSERT INTO ops_issues(order_id, order_stage_id, issue_type, severity, summary, reason, reported_by, original_due_date, revised_due_date, status, created_by_user_id)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            int(order_id),
            int(clean_text(payload.get("order_stage_id") or 0) or 0) or None,
            clean_text(payload.get("issue_type")) or "delay",
            clean_text(payload.get("severity")) or "medium",
            summary,
            clean_text(payload.get("reason")),
            clean_text(payload.get("reported_by")),
            clean_text(payload.get("original_due_date")) or clean_text(order["requested_delivery_date"]),
            clean_text(payload.get("revised_due_date")),
            int(user_id or 0) or None,
        ),
    )
    refresh_order_rollup(conn, order_id)
    return int(cur.lastrowid)


def resolve_issue(conn, workspace_id: str, order_id: int, issue_id: int, payload: dict) -> None:
    order = conn.execute(
        "SELECT id FROM ops_orders WHERE id=? AND workspace_id=?",
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    conn.execute(
        """
        UPDATE ops_issues
        SET status='resolved',
            resolved_at=CURRENT_TIMESTAMP
        WHERE id=? AND order_id=?
        """,
        (int(issue_id), int(order_id)),
    )
    refresh_order_rollup(conn, order_id)


def advance_order_stage(conn, workspace_id: str, order_id: int, user_id: int) -> int:
    order = conn.execute(
        "SELECT id, current_stage_id FROM ops_orders WHERE id=? AND workspace_id=?",
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    stages = _stage_rows_for_order(conn, order_id)
    if not stages:
        raise ValueError("No stages found.")
    current_id = int(order["current_stage_id"] or stages[0]["id"])
    current_index = next((idx for idx, row in enumerate(stages) if int(row["id"]) == current_id), 0)
    current_stage = stages[current_index]
    conn.execute(
        """
        UPDATE ops_order_stages
        SET status='completed',
            actual_end_date=COALESCE(actual_end_date, ?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND order_id=?
        """,
        (date.today().isoformat(), int(current_stage["id"]), int(order_id)),
    )
    next_stage = stages[current_index + 1] if current_index + 1 < len(stages) else stages[-1]
    conn.execute(
        """
        UPDATE ops_order_stages
        SET status=CASE WHEN status='pending' THEN 'in_progress' ELSE status END,
            actual_start_date=COALESCE(actual_start_date, ?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND order_id=?
        """,
        (date.today().isoformat(), int(next_stage["id"]), int(order_id)),
    )
    conn.execute(
        """
        UPDATE ops_orders
        SET current_stage_id=?,
            current_stage_key=?,
            current_stage_name=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            int(next_stage["id"]),
            clean_text(next_stage["stage_key"]),
            clean_text(next_stage["stage_name"]),
            int(order_id),
        ),
    )
    refresh_order_rollup(conn, order_id)
    return int(next_stage["id"])


def add_order_message(
    conn,
    workspace_id: str,
    order_id: int,
    *,
    author_role: str,
    author_name: str,
    message: str,
    is_customer_visible: int = 1,
) -> int:
    msg = clean_text(message)
    if not msg:
        raise ValueError("Message is required.")
    order = conn.execute(
        "SELECT id FROM ops_orders WHERE id=? AND workspace_id=?",
        (int(order_id), clean_text(workspace_id)),
    ).fetchone()
    if order is None:
        raise ValueError("Order not found.")
    cur = conn.execute(
        """
        INSERT INTO ops_comments(order_id, order_stage_id, author_role, author_name, message, is_customer_visible)
        VALUES(?, NULL, ?, ?, ?, ?)
        """,
        (int(order_id), clean_text(author_role), clean_text(author_name), msg, int(is_customer_visible or 0)),
    )
    return int(cur.lastrowid)


def customer_order_by_code(conn, access_code: str):
    return conn.execute(
        """
        SELECT *
        FROM ops_orders
        WHERE upper(customer_access_code)=upper(?)
          AND customer_portal_active=1
        LIMIT 1
        """,
        (clean_text(access_code).upper(),),
    ).fetchone()


def customer_portal_payload(conn, order_id: int) -> dict | None:
    order = conn.execute(
        "SELECT * FROM ops_orders WHERE id=?",
        (int(order_id),),
    ).fetchone()
    if order is None:
        return None
    schedule = _schedule_metrics(conn, order_id)
    stages = _stage_rows_for_order(conn, order_id)
    comments = conn.execute(
        "SELECT * FROM ops_comments WHERE order_id=? AND is_customer_visible=1 ORDER BY created_at ASC, id ASC",
        (int(order_id),),
    ).fetchall()
    updates = conn.execute(
        "SELECT * FROM ops_daily_updates WHERE order_id=? AND lower(coalesce(visibility,''))='customer' ORDER BY created_at DESC, id DESC",
        (int(order_id),),
    ).fetchall()
    issues = conn.execute(
        "SELECT * FROM ops_issues WHERE order_id=? ORDER BY created_at DESC, id DESC",
        (int(order_id),),
    ).fetchall()
    return {
        "order": _order_row_to_dict(order),
        "stages": [{**_order_row_to_dict(row), "checklist": _json_loads(row["checklist_json"], [])} for row in stages],
        "comments": [_order_row_to_dict(row) for row in comments],
        "updates": [_order_row_to_dict(row) for row in updates],
        "issues": [_order_row_to_dict(row) for row in issues],
        "schedule_days": schedule["rows"],
    }


def dashboard_payload(conn, workspace_id: str, user_id: int) -> dict:
    ws = clean_text(workspace_id)
    orders = conn.execute(
        """
        SELECT *
        FROM ops_orders
        WHERE workspace_id=?
        ORDER BY created_at DESC, id DESC
        """,
        (ws,),
    ).fetchall()
    orders_out = []
    for row in orders:
        schedule = _schedule_metrics(conn, int(row["id"]))
        order_dict = _order_row_to_dict(row)
        order_dict["today_schedule_label"] = clean_text(order_dict.get("today_schedule_label")) or clean_text(schedule["today_schedule_label"])
        order_dict["overdue_day_count"] = int(order_dict.get("overdue_day_count") or schedule["overdue_day_count"] or 0)
        order_dict["buffer_day_count"] = int(order_dict.get("buffer_day_count") or schedule["buffer_day_count"] or 0)
        order_dict["schedule_health"] = clean_text(order_dict.get("schedule_health")) or clean_text(schedule["schedule_health"]) or "on_track"
        orders_out.append(order_dict)

    crm_contacts = conn.execute(
        """
        SELECT id, name, status, connection, notes
        FROM crm_contacts
        WHERE workspace_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 250
        """,
        (ws,),
    ).fetchall()
    linked_brand_owners = conn.execute(
        """
        SELECT brand_owner_workspace_id, brand_owner_name
        FROM manufacturer_brand_links
        WHERE manufacturer_workspace_id=?
        ORDER BY created_at DESC, id DESC
        """,
        (ws,),
    ).fetchall()
    return {
        "orders": [_order_row_to_dict(row) for row in orders_out],
        "crm_contacts": [_order_row_to_dict(row) for row in crm_contacts],
        "linked_brand_owners": [_order_row_to_dict(row) for row in linked_brand_owners],
        "kpis": {
            "orders": len(orders_out),
            "overdue_orders": sum(1 for row in orders_out if int(row.get("overdue_day_count") or 0) > 0),
            "delayed_orders": sum(1 for row in orders_out if int(row.get("overdue_day_count") or 0) > 0 or clean_text(row.get("delay_reason"))),
        },
    }


def internal_workspace_payload(conn, workspace_id: str) -> dict:
    ws = clean_text(workspace_id)
    clients = conn.execute(
        "SELECT * FROM ops_clients WHERE workspace_id=? ORDER BY created_at DESC, id DESC",
        (ws,),
    ).fetchall()
    sample_requests = conn.execute(
        "SELECT * FROM ops_sample_requests WHERE workspace_id=? ORDER BY created_at DESC, id DESC",
        (ws,),
    ).fetchall()
    return {
        "clients": [_order_row_to_dict(row) for row in clients],
        "sample_requests": [_order_row_to_dict(row) for row in sample_requests],
        "kpis": {
            "clients": len(clients),
            "sample_requests_open": sum(1 for row in sample_requests if clean_text(row["status"]) != "closed"),
        },
    }


def add_sample_request(conn, workspace_id: str, user_id: int, payload: dict) -> int:
    title = clean_text(payload.get("title"))
    if not title:
        raise ValueError("Sample title is required.")
    cur = conn.execute(
        """
        INSERT INTO ops_sample_requests(workspace_id, client_id, title, status, due_date, notes, created_by_user_id, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            clean_text(workspace_id),
            int(clean_text(payload.get("client_id") or 0) or 0) or None,
            title,
            clean_text(payload.get("status")) or "open",
            clean_text(payload.get("due_date")),
            clean_text(payload.get("notes")),
            int(user_id or 0) or None,
        ),
    )
    return int(cur.lastrowid)


def link_brand_owner_workspace(conn, manufacturer_workspace_id: str, brand_owner_workspace_id: str, user_id: int) -> dict:
    mw = clean_text(manufacturer_workspace_id)
    bw = clean_text(brand_owner_workspace_id)
    if not bw:
        raise ValueError("Brand owner workspace id is required.")
    row = conn.execute(
        "SELECT name FROM brand_owners WHERE workspace_id=?",
        (bw,),
    ).fetchone()
    brand_owner_name = clean_text(row["name"]) if row else bw
    conn.execute(
        """
        INSERT INTO manufacturer_brand_links(manufacturer_workspace_id, brand_owner_workspace_id, brand_owner_name, linked_by_user_id)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(manufacturer_workspace_id, brand_owner_workspace_id)
        DO UPDATE SET brand_owner_name=excluded.brand_owner_name
        """,
        (mw, bw, brand_owner_name, int(user_id or 0) or None),
    )
    return {"brand_owner_workspace_id": bw, "brand_owner_name": brand_owner_name}


def share_order_with_brand_owner(conn, workspace_id: str, order_id: int, brand_owner_workspace_id: str, user_id: int) -> dict:
    bw = clean_text(brand_owner_workspace_id)
    if not bw:
        raise ValueError("Brand owner workspace id is required.")
    conn.execute(
        """
        INSERT INTO ops_order_brand_access(order_id, manufacturer_workspace_id, brand_owner_workspace_id, granted_by_user_id, status)
        VALUES(?, ?, ?, ?, 'active')
        ON CONFLICT(order_id, brand_owner_workspace_id)
        DO UPDATE SET status='active'
        """,
        (int(order_id), clean_text(workspace_id), bw, int(user_id or 0) or None),
    )
    return {"order_id": int(order_id), "brand_owner_workspace_id": bw, "status": "active"}


def get_workflow_template(conn, workspace_id: str, user_id: int = 0) -> dict:
    template_id = ensure_default_workflow_template(conn, clean_text(workspace_id), int(user_id or 0))
    template = conn.execute(
        "SELECT * FROM ops_workflow_templates WHERE id=?",
        (int(template_id),),
    ).fetchone()
    stages = conn.execute(
        """
        SELECT *
        FROM ops_workflow_template_stages
        WHERE template_id=?
        ORDER BY stage_order ASC, id ASC
        """,
        (int(template_id),),
    ).fetchall()
    return {
        "template": _order_row_to_dict(template),
        "stages": [{**_order_row_to_dict(row), "checklist": _json_loads(row["default_checklist_json"], [])} for row in stages],
    }


def update_workflow_template(conn, workspace_id: str, user_id: int, payload: dict) -> None:
    template = get_workflow_template(conn, workspace_id, user_id)
    template_id = int(template["template"]["id"])
    stages = payload.get("stages") or []
    if not isinstance(stages, list):
        raise ValueError("Stages list is required.")
    order_index = 1
    for item in stages:
        stage_name = clean_text((item or {}).get("stage_name"))
        if not stage_name:
            continue
        stage_key = clean_text((item or {}).get("stage_key")) or _slug_stage_key(stage_name)
        department = clean_text((item or {}).get("department"))
        checklist = _json_dumps((item or {}).get("checklist") or [])
        active = 1 if str((item or {}).get("active", 1)).lower() not in {"0", "false", "no", "off"} else 0
        if clean_text((item or {}).get("id")).isdigit():
            conn.execute(
                """
                UPDATE ops_workflow_template_stages
                SET stage_key=?, stage_name=?, stage_order=?, department=?, default_checklist_json=?, customer_visible=?, approval_required=?, active=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND template_id=?
                """,
                (
                    stage_key,
                    stage_name,
                    order_index,
                    department,
                    checklist,
                    int((item or {}).get("customer_visible") or 0),
                    int((item or {}).get("approval_required") or 0),
                    active,
                    int(item["id"]),
                    int(template_id),
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO ops_workflow_template_stages(
                    template_id, stage_key, stage_name, stage_order, department,
                    default_checklist_json, customer_visible, approval_required, active
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(template_id),
                    stage_key,
                    stage_name,
                    order_index,
                    department,
                    checklist,
                    int((item or {}).get("customer_visible") or 0),
                    int((item or {}).get("approval_required") or 0),
                    active,
                ),
            )
        order_index += 1


def customer_portal_active(conn, order_id: int) -> bool:
    row = conn.execute("SELECT customer_portal_active FROM ops_orders WHERE id=?", (int(order_id),)).fetchone()
    return int(row["customer_portal_active"] or 0) == 1 if row else False


def customer_access_code(conn, order_id: int) -> str:
    row = conn.execute("SELECT customer_access_code FROM ops_orders WHERE id=?", (int(order_id),)).fetchone()
    return clean_text(row["customer_access_code"]) if row else ""


def brand_owner_orders_payload(conn, brand_owner_workspace_id: str) -> dict:
    bw = clean_text(brand_owner_workspace_id)
    rows = conn.execute(
        """
        SELECT o.*, a.brand_owner_workspace_id
        FROM ops_orders o
        JOIN ops_order_brand_access a ON a.order_id=o.id
        WHERE a.brand_owner_workspace_id=?
          AND lower(coalesce(a.status,''))='active'
        ORDER BY o.created_at DESC, o.id DESC
        """,
        (bw,),
    ).fetchall()
    manufacturer_lookup = {
        clean_text(row["workspace_id"]): clean_text(row["account_name"])
        for row in conn.execute(
            """
            SELECT workspace_id, account_name
            FROM users
            WHERE trim(coalesce(workspace_id,''))<>''
              AND lower(coalesce(account_type,''))='manufacturer'
            ORDER BY id ASC
            """
        ).fetchall()
        if clean_text(row["workspace_id"])
    }
    orders_out = []
    for row in rows:
        schedule = _schedule_metrics(conn, int(row["id"]))
        order_dict = _order_row_to_dict(row)
        order_dict["manufacturer_name"] = clean_text(
            order_dict.get("manufacturer_name")
        ) or manufacturer_lookup.get(clean_text(order_dict.get("workspace_id")), "")
        order_dict["today_schedule_label"] = clean_text(order_dict.get("today_schedule_label")) or clean_text(schedule["today_schedule_label"])
        order_dict["overdue_day_count"] = int(order_dict.get("overdue_day_count") or schedule["overdue_day_count"] or 0)
        order_dict["buffer_day_count"] = int(order_dict.get("buffer_day_count") or schedule["buffer_day_count"] or 0)
        order_dict["schedule_health"] = clean_text(order_dict.get("schedule_health")) or clean_text(schedule["schedule_health"]) or "on_track"
        orders_out.append(order_dict)
    return {
        "orders": orders_out,
        "kpis": {
            "active_orders": len(orders_out),
            "delayed_orders": sum(1 for row in orders_out if int(row.get("overdue_day_count") or 0) > 0 or clean_text(row.get("delay_reason"))),
        },
    }


def brand_owner_order_detail(conn, brand_owner_workspace_id: str, order_id: int) -> dict | None:
    bw = clean_text(brand_owner_workspace_id)
    access = conn.execute(
        """
        SELECT 1
        FROM ops_order_brand_access
        WHERE order_id=? AND brand_owner_workspace_id=? AND lower(coalesce(status,''))='active'
        """,
        (int(order_id), bw),
    ).fetchone()
    if access is None:
        return None
    order_row = conn.execute("SELECT workspace_id FROM ops_orders WHERE id=?", (int(order_id),)).fetchone()
    if order_row is None:
        return None
    return get_order_detail(conn, clean_text(order_row["workspace_id"]), int(order_id))


def brand_owner_manufacturers_payload(conn, brand_owner_workspace_id: str) -> list[dict]:
    bw = clean_text(brand_owner_workspace_id)
    rows = conn.execute(
        """
        SELECT o.workspace_id, COUNT(*) AS order_count
        FROM ops_orders o
        JOIN ops_order_brand_access a ON a.order_id=o.id
        WHERE a.brand_owner_workspace_id=? AND lower(coalesce(a.status,''))='active'
        GROUP BY o.workspace_id
        ORDER BY order_count DESC
        """,
        (bw,),
    ).fetchall()
    manufacturer_lookup = {
        clean_text(row["workspace_id"]): clean_text(row["account_name"])
        for row in conn.execute(
            """
            SELECT workspace_id, account_name
            FROM users
            WHERE trim(coalesce(workspace_id,''))<>''
              AND lower(coalesce(account_type,''))='manufacturer'
            ORDER BY id ASC
            """
        ).fetchall()
        if clean_text(row["workspace_id"])
    }
    return [
        {
            "workspace_id": clean_text(row["workspace_id"]),
            "manufacturer_name": manufacturer_lookup.get(clean_text(row["workspace_id"]), ""),
            "order_count": int(row["order_count"] or 0),
        }
        for row in rows
    ]


def redeem_brand_owner_tracking_code(conn, brand_owner_workspace_id: str, access_code: str, user_id: int) -> dict:
    code = clean_text(access_code).upper()
    if not code:
        raise ValueError("Access code is required.")
    order = customer_order_by_code(conn, code)
    if order is None:
        raise ValueError("That access code was not found.")
    conn.execute(
        """
        INSERT INTO ops_order_brand_access(order_id, manufacturer_workspace_id, brand_owner_workspace_id, granted_by_user_id, status)
        VALUES(?, ?, ?, ?, 'active')
        ON CONFLICT(order_id, brand_owner_workspace_id)
        DO UPDATE SET status='active'
        """,
        (
            int(order["id"]),
            clean_text(order["workspace_id"]),
            clean_text(brand_owner_workspace_id),
            int(user_id or 0) or None,
        ),
    )
    return {"order_id": int(order["id"]), "order_number": clean_text(order["order_number"])}
