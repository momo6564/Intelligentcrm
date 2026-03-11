import csv
import os
import sqlite3
from collections import Counter
from typing import Dict, Tuple


CSV_PATH = os.path.join("data", "ab_vendordata.csv")
DB_PATH = os.path.join("data", "ab_vendordata.db")
TABLE_NAME = "collegiate_vendors"


def clean(value: str) -> str:
    return (value or "").strip()


def split_city_state(raw: str) -> Tuple[str, str]:
    raw = clean(raw)
    if not raw:
        return "", ""
    if "," in raw:
        city, state = raw.rsplit(",", 1)
        return city.strip(), state.strip()
    return raw, ""


def load_rows(path: str) -> Tuple[int, int, Counter, Dict[str, int]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found.")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    cur.execute(
        f"""
        CREATE TABLE {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT,
            city_state TEXT,
            city TEXT,
            state TEXT,
            phone TEXT,
            website_text TEXT,
            website_url TEXT,
            email TEXT,
            email_url TEXT,
            notes TEXT
        )
        """
    )
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_state ON {TABLE_NAME}(state)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_city ON {TABLE_NAME}(city)")

    total_rows = 0
    inserted = 0
    missing_state = 0
    state_counts: Counter = Counter()

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            total_rows += 1
            vendor_name = clean(row.get("nobull"))
            city_state_raw = clean(row.get("nobull 2"))
            phone = clean(row.get("nobull 3"))
            website_text = clean(row.get("nobull 4"))
            website_url = clean(row.get("nobull href"))
            email = clean(row.get("nobull 5"))
            email_url = clean(row.get("nobull href 2"))
            notes = clean(row.get("nobull 6"))

            if not any([vendor_name, city_state_raw, phone, website_text, website_url, email, email_url, notes]):
                continue

            city, state = split_city_state(city_state_raw)
            if state:
                state_counts[state] += 1
            else:
                missing_state += 1

            batch.append(
                (
                    vendor_name,
                    city_state_raw,
                    city,
                    state,
                    phone,
                    website_text,
                    website_url,
                    email,
                    email_url,
                    notes,
                )
            )
            inserted += 1

    if batch:
        cur.executemany(
            f"""
            INSERT INTO {TABLE_NAME}
            (vendor_name, city_state, city, state, phone, website_text, website_url, email, email_url, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )

    conn.commit()
    conn.close()
    return total_rows, inserted, state_counts, {"missing_state": missing_state}


def main() -> None:
    total_rows, inserted, state_counts, extras = load_rows(CSV_PATH)
    unique_states = len(state_counts)
    top_states = state_counts.most_common(10)

    print("Collegiate vendor import complete.")
    print(f"CSV rows scanned: {total_rows}")
    print(f"Rows inserted: {inserted}")
    print(f"Rows missing state: {extras['missing_state']}")
    print(f"Unique states: {unique_states}")
    print("Top states:")
    for state, count in top_states:
        print(f"  {state}: {count}")
    print(f"Database: {DB_PATH}")


if __name__ == "__main__":
    main()
