import os
import unittest
import uuid

from app import create_app
from app.config import Config
from app.database import get_connection, ensure_crm_tables


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_path = Config.DB_PATH
        cls._orig_vendor_csv = Config.VENDOR_CSV_PATH
        cls._tmp_dir = os.path.join(os.getcwd(), ".test_tmp")
        os.makedirs(cls._tmp_dir, exist_ok=True)
        Config.DB_PATH = os.path.join(cls._tmp_dir, "test.db")
        Config.VENDOR_CSV_PATH = os.path.join(cls._tmp_dir, "vendors.csv")
        cls.app = create_app()
        cls.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        Config.DB_PATH = cls._orig_db_path
        Config.VENDOR_CSV_PATH = cls._orig_vendor_csv
        try:
            if os.path.exists(os.path.join(cls._tmp_dir, "test.db")):
                os.remove(os.path.join(cls._tmp_dir, "test.db"))
        except OSError:
            pass

    def setUp(self):
        if os.path.exists(Config.DB_PATH):
            os.remove(Config.DB_PATH)
        self.client = self.app.test_client()
        with self.app.app_context():
            conn = get_connection()
            ensure_crm_tables(conn)
            conn.commit()

    def _signup(self, username: str, manufacturer_name: str, password: str = "pass123") -> None:
        resp = self.client.post(
            "/signup",
            data={
                "username": username,
                "password": password,
                "manufacturer_name": manufacturer_name,
                "contact_email": f"{username}@example.com",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard", resp.headers.get("Location", ""))

    def _login(self, username: str, password: str = "pass123", next_path: str = "/dashboard"):
        return self.client.post(
            "/login",
            data={"username": username, "password": password, "next": next_path},
            follow_redirects=False,
        )

    def _me(self) -> dict:
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload.get("ok"))
        return payload["user"]

    def test_login_page_loads(self):
        anon = self.app.test_client()
        resp = anon.get("/login")
        self.assertEqual(resp.status_code, 200)

    def test_api_requires_login(self):
        anon = self.app.test_client()
        resp = anon.get("/api/m/chapters")
        self.assertEqual(resp.status_code, 401)

    def test_login_blocks_open_redirect(self):
        resp = self._login("demo", "demo123", next_path="https://evil.example/path")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard", resp.headers.get("Location", ""))
        self.assertNotIn("evil.example", resp.headers.get("Location", ""))

    def test_signup_and_login_flow(self):
        username = f"user_{uuid.uuid4().hex[:8]}"
        self._signup(username=username, manufacturer_name="Acme Manufacturing", password="secret123")
        self.client.get("/logout")
        resp = self._login(username=username, password="secret123")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard", resp.headers.get("Location", ""))

    def test_closed_actions_create_served_orders(self):
        self._login("demo", "demo123")
        me = self._me()
        workspace_id = me["workspace_id"]

        chapter_payload = {
            "chapter_id": f"chapter::{uuid.uuid4().hex[:8]}",
            "chapter_name": "Test Chapter",
            "license_type": "Alpha Phi Alpha",
            "action": "served",
        }
        vendor_payload = {
            "vendor_name": f"Vendor {uuid.uuid4().hex[:6]}",
            "license_type": "Alpha Phi Alpha",
            "action": "served",
        }

        resp1 = self.client.post("/api/m/crm/add-chapter", json=chapter_payload)
        self.assertEqual(resp1.status_code, 200)
        self.assertTrue(resp1.get_json().get("ok"))

        resp2 = self.client.post("/api/m/crm/add-vendor", json=vendor_payload)
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.get_json().get("ok"))

        with self.app.app_context():
            conn = get_connection()
            chapter_order = conn.execute(
                "SELECT id FROM vendor_orders WHERE chapter_id=? AND order_type='Served' AND workspace_id=?",
                (chapter_payload["chapter_id"], workspace_id),
            ).fetchone()
            vendor_order = conn.execute(
                "SELECT id FROM vendor_orders WHERE lower(vendor)=lower(?) AND order_type='Served' AND workspace_id=?",
                (vendor_payload["vendor_name"], workspace_id),
            ).fetchone()
        self.assertIsNotNone(chapter_order)
        self.assertIsNotNone(vendor_order)

    def test_crm_is_workspace_scoped(self):
        user1 = f"u1_{uuid.uuid4().hex[:8]}"
        user2 = f"u2_{uuid.uuid4().hex[:8]}"
        self._signup(user1, "Maker One")
        vendor_name = f"Scoped Vendor {uuid.uuid4().hex[:6]}"
        add_resp = self.client.post(
            "/api/m/crm/add-vendor",
            json={"vendor_name": vendor_name, "license_type": "Alpha Phi Alpha", "action": "lead"},
        )
        self.assertEqual(add_resp.status_code, 200)
        self.client.get("/logout")

        self._signup(user2, "Maker Two")
        crm_resp = self.client.get("/api/m/crm")
        self.assertEqual(crm_resp.status_code, 200)
        rows = crm_resp.get_json().get("rows", [])
        names = {r.get("name") for r in rows}
        self.assertNotIn(vendor_name, names)

    def test_crm_data_persists_after_logout_login(self):
        self._login("demo", "demo123")
        first_me = self._me()
        first_ws = first_me["workspace_id"]
        vendor_name = f"Persist Vendor {uuid.uuid4().hex[:6]}"
        add_resp = self.client.post(
            "/api/m/crm/add-vendor",
            json={"vendor_name": vendor_name, "license_type": "Alpha Phi Alpha", "action": "prospect"},
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertTrue(add_resp.get_json().get("ok"))

        crm_resp = self.client.get("/api/m/crm")
        self.assertEqual(crm_resp.status_code, 200)
        rows = crm_resp.get_json().get("rows", [])
        row = next((r for r in rows if r.get("name") == vendor_name), None)
        self.assertIsNotNone(row)
        contact_id = int(row["id"])

        note_resp = self.client.post("/api/m/crm/note", json={"contact_id": contact_id, "note": "Initial outreach"})
        self.assertEqual(note_resp.status_code, 200)
        self.assertTrue(note_resp.get_json().get("ok"))
        task_resp = self.client.post(
            "/api/m/crm/task",
            json={"contact_id": contact_id, "title": "Follow up next week", "due_date": "2026-03-15", "priority": "high"},
        )
        self.assertEqual(task_resp.status_code, 200)
        self.assertTrue(task_resp.get_json().get("ok"))

        self.client.get("/logout")
        self._login("demo", "demo123")
        second_me = self._me()
        self.assertEqual(first_ws, second_me["workspace_id"])

        detail_resp = self.client.get(f"/api/m/crm/contact?contact_id={contact_id}")
        self.assertEqual(detail_resp.status_code, 200)
        payload = detail_resp.get_json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("contact", {}).get("name"), vendor_name)
        self.assertGreaterEqual(len(payload.get("notes", [])), 1)
        self.assertGreaterEqual(len(payload.get("tasks", [])), 1)

    def test_crm_board_and_calendar_endpoints(self):
        self._login("demo", "demo123")
        chapter_id = f"chapter::{uuid.uuid4().hex[:8]}"
        add_resp = self.client.post(
            "/api/m/crm/add-chapter",
            json={
                "chapter_id": chapter_id,
                "chapter_name": "Board Test Chapter",
                "license_type": "Delta Sigma Theta",
                "action": "prospect",
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertTrue(add_resp.get_json().get("ok"))

        crm_rows = self.client.get("/api/m/crm").get_json().get("rows", [])
        chapter_row = next((r for r in crm_rows if r.get("chapter_id") == chapter_id), None)
        self.assertIsNotNone(chapter_row)
        contact_id = int(chapter_row["id"])
        task_resp = self.client.post(
            "/api/m/crm/task",
            json={"contact_id": contact_id, "title": "Call chapter", "due_date": "2026-03-10", "priority": "normal"},
        )
        self.assertEqual(task_resp.status_code, 200)
        self.assertTrue(task_resp.get_json().get("ok"))

        board_resp = self.client.get("/api/m/crm/board")
        self.assertEqual(board_resp.status_code, 200)
        board_payload = board_resp.get_json()
        self.assertTrue(board_payload.get("ok"))
        self.assertIn("kpis", board_payload)
        self.assertIn("stages", board_payload)

        calendar_resp = self.client.get("/api/m/crm/calendar")
        self.assertEqual(calendar_resp.status_code, 200)
        cal_payload = calendar_resp.get_json()
        self.assertTrue(cal_payload.get("ok"))
        self.assertGreaterEqual(len(cal_payload.get("events", [])), 1)

    def test_crm_bulk_update_endpoint(self):
        self._login("demo", "demo123")
        v1 = f"Bulk Vendor {uuid.uuid4().hex[:6]}"
        v2 = f"Bulk Vendor {uuid.uuid4().hex[:6]}"
        for name in (v1, v2):
            add_resp = self.client.post(
                "/api/m/crm/add-vendor",
                json={"vendor_name": name, "license_type": "Alpha Phi Alpha", "action": "prospect"},
            )
            self.assertEqual(add_resp.status_code, 200)
            self.assertTrue(add_resp.get_json().get("ok"))

        crm_rows = self.client.get("/api/m/crm").get_json().get("rows", [])
        ids = [int(r["id"]) for r in crm_rows if r.get("name") in {v1, v2}]
        self.assertEqual(len(ids), 2)

        bulk_resp = self.client.post(
            "/api/m/crm/bulk-update",
            json={
                "contact_ids": ids,
                "status": "contacted",
                "follow_up_date": "2026-03-20",
                "priority": "high",
            },
        )
        self.assertEqual(bulk_resp.status_code, 200)
        bulk_payload = bulk_resp.get_json()
        self.assertTrue(bulk_payload.get("ok"))
        self.assertEqual(int(bulk_payload.get("updated_count") or 0), 2)

        updated_rows = self.client.get("/api/m/crm").get_json().get("rows", [])
        for row in updated_rows:
            if int(row.get("id") or 0) in ids:
                self.assertEqual((row.get("status") or "").lower(), "contacted")
                self.assertEqual((row.get("priority") or "").lower(), "high")
                self.assertEqual(row.get("follow_up_date"), "2026-03-20")

    def test_explorer_apis_include_crm_stage_metadata(self):
        self._login("demo", "demo123")
        chapters_seed = self.client.get("/api/chapters").get_json()
        self.assertTrue(chapters_seed.get("ok"))
        seed_rows = chapters_seed.get("rows", [])
        chapter_id = seed_rows[0]["id"] if seed_rows else ""
        chapter_name = (seed_rows[0].get("chapterName") if seed_rows else "") or "Meta Chapter"
        org_name = (seed_rows[0].get("orgName") if seed_rows else "") or "Alpha Phi Alpha"
        vendor_name = f"Meta Vendor {uuid.uuid4().hex[:6]}"
        if chapter_id:
            self.client.post(
                "/api/m/crm/add-chapter",
                json={
                    "chapter_id": chapter_id,
                    "chapter_name": chapter_name,
                    "license_type": org_name,
                    "action": "prospect",
                },
            )
        self.client.post(
            "/api/m/crm/add-vendor",
            json={
                "vendor_name": vendor_name,
                "license_type": "Alpha Phi Alpha",
                "action": "prospect",
            },
        )

        chapters_payload = self.client.get("/api/chapters").get_json()
        self.assertTrue(chapters_payload.get("ok"))
        if chapter_id:
            chapter_row = next((r for r in chapters_payload.get("rows", []) if r.get("id") == chapter_id), None)
            self.assertIsNotNone(chapter_row)
            self.assertIn("crm_stage", chapter_row)
            self.assertIn("open_task_count", chapter_row)

        vendors_payload = self.client.get("/api/vendors").get_json()
        self.assertTrue(vendors_payload.get("ok"))
        vendor_row = next((r for r in vendors_payload.get("rows", []) if (r.get("vendor") or "").lower() == vendor_name.lower()), None)
        if vendor_row is not None:
            self.assertIn("crm_stage", vendor_row)
            self.assertIn("open_task_count", vendor_row)


if __name__ == "__main__":
    unittest.main()
