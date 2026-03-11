import sqlite3
from pathlib import Path


SRC_DB = Path(r"c:\Users\Mohid\Downloads\chapters\greek-chapters-app\data\greek_chapters.db")
DEST_DB = Path(r"c:\Users\Mohid\Downloads\chapters\greek-chapters-app\data\users_data.db")

USER_TABLES = [
    "users",
    "crm_contacts",
    "crm_tasks",
    "crm_notes",
    "crm_activities",
    "crm_contact_tags",
    "crm_tags",
    "messages",
    "activities",
    "leads",
    "lead_activities",
    "vendor_orders",
    "chapter_contacts",
    "saved_views",
    "competitors_followed",
    "vendor_org_licenses",
    "manufacturers",
    "manufacturer_orders",
]


def main() -> None:
    if not SRC_DB.exists():
        raise FileNotFoundError(f"Source DB not found: {SRC_DB}")

    if DEST_DB.exists():
        DEST_DB.unlink()

    src = sqlite3.connect(SRC_DB)
    dst = sqlite3.connect(DEST_DB)
    src.row_factory = sqlite3.Row

    src_tables = {r["name"] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in USER_TABLES:
        if table not in src_tables:
            continue
        ddl_row = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if ddl_row and ddl_row["sql"]:
            dst.execute(ddl_row["sql"])

        rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
        if rows:
            cols = rows[0].keys()
            col_list = ", ".join([f'"{c}"' for c in cols])
            placeholders = ", ".join(["?"] * len(cols))
            insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'
            dst.executemany(insert_sql, [tuple(r[c] for c in cols) for r in rows])

        idx_rows = src.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        ).fetchall()
        for idx in idx_rows:
            dst.execute(idx["sql"])

    dst.commit()
    dst.close()
    src.close()
    print(f"User data exported to {DEST_DB}")


if __name__ == "__main__":
    main()
