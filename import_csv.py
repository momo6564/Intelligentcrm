import csv
import os
import re
import sqlite3
from typing import List, Tuple

DB_PATH = "data/greek_chapters.db"
CSV_PATH = "data/greek_chapters.csv"
TABLE_NAME = "chapters"


def sanitize_column(name: str, index: int) -> str:
    cleaned = re.sub(r"\W+", "_", (name or "").strip().lower())
    if not cleaned:
        cleaned = f"col_{index + 1}"
    if cleaned[0].isdigit():
        cleaned = f"col_{cleaned}"
    return cleaned


def infer_type(values: List[str]) -> str:
    non_empty = [v.strip() for v in values if v is not None and v.strip() != ""]
    if not non_empty:
        return "TEXT"
    if all(re.fullmatch(r"[-+]?\d+", v) for v in non_empty):
        return "INTEGER"
    return "TEXT"


def load_csv(path: str) -> Tuple[List[str], List[List[str]]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        raise ValueError("CSV is empty.")

    headers = rows[0]
    data = rows[1:]
    width = len(headers)

    normalized_data = []
    for row in data:
        if len(row) < width:
            row = row + [""] * (width - len(row))
        elif len(row) > width:
            row = row[:width]
        normalized_data.append(row)

    return headers, normalized_data


def main() -> None:
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"{CSV_PATH} not found. Place it in the project root.")

    raw_headers, rows = load_csv(CSV_PATH)
    sanitized_headers = [sanitize_column(h, i) for i, h in enumerate(raw_headers)]

    seen = {}
    deduped_headers = []
    for h in sanitized_headers:
        if h in seen:
            seen[h] += 1
            deduped_headers.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            deduped_headers.append(h)

    columns_values = list(zip(*rows)) if rows else [[] for _ in deduped_headers]
    sql_types = [infer_type(list(col)) for col in columns_values]

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")

    col_defs = [f'"{name}" {ctype}' for name, ctype in zip(deduped_headers, sql_types)]
    create_sql = (
        f"CREATE TABLE {TABLE_NAME} ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        + ", ".join(col_defs)
        + ")"
    )
    cur.execute(create_sql)

    if rows:
        placeholders = ", ".join(["?"] * len(deduped_headers))
        quoted_cols = ", ".join([f'"{c}"' for c in deduped_headers])
        insert_sql = f"INSERT INTO {TABLE_NAME} ({quoted_cols}) VALUES ({placeholders})"
        cur.executemany(insert_sql, rows)

    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    row_count = cur.fetchone()[0]
    conn.close()

    print("Import successful! Database created.")
    print(f"Rows imported: {row_count}")


if __name__ == "__main__":
    main()
