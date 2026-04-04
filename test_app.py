import os
import unittest
import uuid
from datetime import date, timedelta

from app import create_app
from app.config import Config
from app.database import get_connection, ensure_crm_tables, ensure_institutions_table, ensure_chapters_table, ensure_vendor_table
from app.order_ops import ensure_ops_tables
from app.routes import main as main_routes
from app.services.chapters import fetch_normalized_rows


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
            ensure_ops_tables(conn)
            conn.commit()

    def _signup(self, username: str, manufacturer_name: str, password: str = "pass123", account_type: str = "manufacturer", expected_path: str = "/dashboard") -> None:
        resp = self.client.post(
            "/signup",
            data={
                "username": username,
                "password": password,
                "account_type": account_type,
                "account_name": manufacturer_name,
                "contact_email": f"{username}@example.com",
                "agree_terms": "1",
                "security_question": Config.SECURITY_QUESTIONS[0],
                "security_answer": "TestAnswer123",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(expected_path, resp.headers.get("Location", ""))

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

    def test_root_landing_page_loads_without_login(self):
        anon = self.app.test_client()
        resp = anon.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('<div id="root"></div>', body)
        self.assertIn("/static/ops_hub/assets/", body)
        self.assertIn('"/login"', body)
        self.assertIn('"/signup"', body)
        self.assertIn('"/ops/track"', body)

    def test_root_landing_ctas_point_logged_in_user_to_dashboard(self):
        self._login("demo", "demo123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('"/dashboard"', body)

    def test_dashboard_page_skips_eager_institutions_bootstrap(self):
        self._login("demo", "demo123")
        original_ensure = main_routes.ensure_institutions_table

        def fail_if_called(conn):
            raise AssertionError("dashboard should not eagerly bootstrap institutions")

        main_routes.ensure_institutions_table = fail_if_called
        try:
            resp = self.client.get("/dashboard")
            self.assertEqual(resp.status_code, 200)
            body = resp.get_data(as_text=True)
            self.assertIn("Load Served Map", body)
        finally:
            main_routes.ensure_institutions_table = original_ensure

    def test_dashboard_served_map_api_bootstraps_institutions_on_demand(self):
        self._login("demo", "demo123")
        calls = {"count": 0}
        original_ensure = main_routes.ensure_institutions_table

        def tracking_ensure(conn):
            calls["count"] += 1
            return original_ensure(conn)

        main_routes.ensure_institutions_table = tracking_ensure
        try:
            resp = self.client.get("/api/dashboard/served-map")
            self.assertEqual(resp.status_code, 200)
            payload = resp.get_json()
            self.assertTrue(payload.get("ok"))
            self.assertIn("points", payload)
            self.assertIn("stats", payload)
            self.assertGreaterEqual(calls["count"], 1)
        finally:
            main_routes.ensure_institutions_table = original_ensure

    def test_signup_page_shows_account_type_choices(self):
        anon = self.app.test_client()
        resp = anon.get("/signup")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('name="account_type"', body)
        self.assertIn("Vendor / Brand Owner", body)
        self.assertIn("Manufacturer", body)
        self.assertIn("/terms", body)
        self.assertIn("/privacy", body)
        self.assertNotIn("Create your Atlas account", body)

    def test_terms_and_privacy_pages_load(self):
        anon = self.app.test_client()
        terms = anon.get("/terms")
        privacy = anon.get("/privacy")
        self.assertEqual(terms.status_code, 200)
        self.assertEqual(privacy.status_code, 200)
        self.assertIn("Terms of Service", terms.get_data(as_text=True))
        self.assertIn("Privacy Policy", privacy.get_data(as_text=True))

    def test_signup_requires_terms_agreement(self):
        resp = self.client.post(
            "/signup",
            data={
                "first_name": "No",
                "last_name": "Terms",
                "email": "noterms@example.com",
                "password": "secret123",
                "account_type": "manufacturer",
                "company": "No Terms Co",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Please agree to the Terms of Service and Privacy Policy.", resp.get_data(as_text=True))

    def test_google_onboarding_requires_pending_session(self):
        anon = self.app.test_client()
        resp = anon.get("/google/onboarding", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers.get("Location", ""))

    def test_google_onboarding_creates_brand_owner_workspace_after_google_auth(self):
        with self.client.session_transaction() as sess:
            sess["google_onboarding"] = {
                "google_id": f"google-{uuid.uuid4().hex[:8]}",
                "email": "brand-google@example.com",
                "full_name": "Brand Google",
                "existing_user_id": 0,
            }
            sess["google_next"] = "/"
        resp = self.client.post(
            "/google/onboarding",
            data={"account_type": "brand_owner", "account_name": "Brand Google Co"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/brand/dashboard", resp.headers.get("Location", ""))
        me = self._me()
        self.assertEqual(me.get("account_type"), "brand_owner")
        self.assertEqual(me.get("account_name"), "Brand Google Co")

    def test_google_onboarding_creates_manufacturer_workspace_after_google_auth(self):
        with self.client.session_transaction() as sess:
            sess["google_onboarding"] = {
                "google_id": f"google-{uuid.uuid4().hex[:8]}",
                "email": "maker-google@example.com",
                "full_name": "Maker Google",
                "existing_user_id": 0,
            }
            sess["google_next"] = "/"
        resp = self.client.post(
            "/google/onboarding",
            data={"account_type": "manufacturer", "account_name": "Maker Google Co"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard", resp.headers.get("Location", ""))
        me = self._me()
        self.assertEqual(me.get("account_type"), "manufacturer")
        self.assertEqual(me.get("account_name"), "Maker Google Co")

    def test_google_callback_uses_token_userinfo_without_extra_fetch(self):
        self._signup("googlefast", "Google Fast Co")

        class FakeGoogle:
            def __init__(self):
                self.userinfo_fetches = []

            def authorize_access_token(self):
                return {
                    "access_token": "test-token",
                    "userinfo": {
                        "sub": f"google-{uuid.uuid4().hex[:8]}",
                        "email": "googlefast@example.com",
                        "name": "Google Fast",
                    },
                }

            def get(self, endpoint, token=None):
                self.userinfo_fetches.append(endpoint)
                raise AssertionError("Google userinfo fetch should not run when token already includes userinfo.")

        original_google = getattr(self.app, "google_oauth", None)
        self.app.google_oauth = FakeGoogle()
        try:
            with self.client.session_transaction() as sess:
                sess["google_next"] = "/"
                sess["google_nonce"] = "nonce"

            resp = self.client.get("/google/callback", follow_redirects=False)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/dashboard", resp.headers.get("Location", ""))
            self.assertEqual(self.app.google_oauth.userinfo_fetches, [])

            me = self._me()
            self.assertEqual(me.get("email"), "googlefast@example.com")
        finally:
            self.app.google_oauth = original_google

    def test_api_requires_login(self):
        anon = self.app.test_client()
        resp = anon.get("/api/m/chapters")
        self.assertEqual(resp.status_code, 401)

    def test_institutions_map_api_and_detail_endpoint(self):
        with self.app.app_context():
            conn = get_connection()
            ensure_institutions_table(conn)
            ensure_chapters_table(conn)
            cur = conn.execute(
                """
                INSERT INTO institutions (
                    location_name, city, state, control, institution_level,
                    latitude, longitude, website, students_total, acceptance_rate
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Atlas University",
                    "Atlanta",
                    "GA",
                    "Public Institution",
                    "4 year",
                    "33.7490",
                    "-84.3880",
                    "atlas.example.edu",
                    12000,
                    0.42,
                ),
            )
            institution_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO chapters (
                    chapter_uid, institution_id, chapter_name, organization, school, city, state, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "atlas-alpha",
                    institution_id,
                    "Alpha Chapter",
                    "Alpha Phi Alpha",
                    "Atlas University",
                    "Atlanta",
                    "GA",
                    "Active",
                ),
            )
            conn.commit()

        self._login("demo", "demo123")

        resp = self.client.get("/api/institutions?all=1&include_filters=1&q=Atlas%20University")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload.get("ok"))
        self.assertGreaterEqual(payload.get("total"), 1)
        atlas_row = next((row for row in payload.get("results", []) if row.get("id") == institution_id), None)
        self.assertIsNotNone(atlas_row)
        self.assertEqual(atlas_row["location_name"], "Atlas University")
        self.assertAlmostEqual(float(atlas_row["latitude"]), 33.7490, places=4)
        self.assertIn("states", payload.get("filters", {}))

        detail_resp = self.client.get(f"/api/institutions/{institution_id}")
        self.assertEqual(detail_resp.status_code, 200)
        detail_payload = detail_resp.get_json()
        self.assertTrue(detail_payload.get("ok"))
        self.assertEqual(detail_payload["institution"]["chapter_count"], 1)
        self.assertEqual(len(detail_payload.get("chapters", [])), 1)
        self.assertEqual(detail_payload["chapters"][0]["chapter_uid"], "atlas-alpha")

    def test_research_memory_prompts_are_user_private_and_category_scoped(self):
        self._login("demo", "demo123")

        get_defaults = self.client.get("/api/research-prompts?category=chapter")
        self.assertEqual(get_defaults.status_code, 200)
        defaults_payload = get_defaults.get_json()
        self.assertTrue(defaults_payload.get("ok"))
        self.assertEqual(len(defaults_payload.get("prompts", [])), 5)
        self.assertIn("{chapter_name}", "".join(defaults_payload.get("placeholders", [])))

        save_resp = self.client.post(
            "/api/research-prompts",
            json={
                "category": "chapter",
                "prompts": [
                    {"label": "Rush Fit", "prompt_text": 'Research "{chapter_name}" rush fit ideas at "{school}"'},
                    {"label": "Decision Makers", "prompt_text": 'Find leadership for "{organization}" at "{school}"'},
                ],
            },
        )
        self.assertEqual(save_resp.status_code, 200)
        saved_payload = save_resp.get_json()
        self.assertTrue(saved_payload.get("ok"))
        self.assertEqual(saved_payload["prompts"][0]["label"], "Rush Fit")
        self.assertEqual(saved_payload["prompts"][1]["label"], "Decision Makers")
        self.assertEqual(saved_payload["prompts"][2]["prompt_text"], "")

        get_saved = self.client.get("/api/research-prompts?category=chapter")
        self.assertEqual(get_saved.status_code, 200)
        self.assertEqual(get_saved.get_json()["prompts"][0]["label"], "Rush Fit")

        vendor_prompts = self.client.get("/api/research-prompts?category=vendor")
        self.assertEqual(vendor_prompts.status_code, 200)
        self.assertEqual(vendor_prompts.get_json()["prompts"][0]["label"], "Owner")

        self.client.get("/logout")
        self._signup("memoryuser", "Memory User Co", password="memory123")
        self._login("memoryuser", "memory123")

        other_user_prompts = self.client.get("/api/research-prompts?category=chapter")
        self.assertEqual(other_user_prompts.status_code, 200)
        self.assertNotEqual(other_user_prompts.get_json()["prompts"][0]["label"], "Rush Fit")

    def test_research_memory_panel_renders_on_detail_pages(self):
        with self.app.app_context():
            conn = get_connection()
            ensure_institutions_table(conn)
            ensure_chapters_table(conn)
            ensure_vendor_table(conn)
            inst_cur = conn.execute(
                """
                INSERT INTO institutions (location_name, city, state, website)
                VALUES (?, ?, ?, ?)
                """,
                ("Memory University", "Austin", "TX", "memory.edu"),
            )
            institution_id = int(inst_cur.lastrowid)
            conn.execute(
                """
                INSERT INTO chapters (chapter_uid, institution_id, chapter_name, organization, school, city, state, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("memory-alpha", institution_id, "Alpha Eta", "Delta Sigma Theta", "Memory University", "Austin", "TX", "Active"),
            )
            conn.execute(
                """
                INSERT INTO vendors (vendor, organization, category, city, state, website)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("Memory Apparel", "DST", "Apparel", "Austin", "TX", "https://memory-apparel.example.com"),
            )
            conn.commit()

        chapter_route_id = "memory-alpha"
        self._login("demo", "demo123")
        with self.app.app_context():
            rows = fetch_normalized_rows(force_refresh=True)
            matched = next((row for row in rows if row.get("school") == "Memory University"), None)
            if matched and matched.get("id"):
                chapter_route_id = matched["id"]

        chapter_page = self.client.get(f"/chapters/{chapter_route_id}")
        self.assertEqual(chapter_page.status_code, 200)
        self.assertIn("Chapter Research Memory", chapter_page.get_data(as_text=True))

        vendor_page = self.client.get("/vendors/detail?vendor_name=Memory%20Apparel")
        self.assertEqual(vendor_page.status_code, 200)
        self.assertIn("Vendor Research Memory", vendor_page.get_data(as_text=True))

        institution_page = self.client.get(f"/institutions/detail?institution_id={institution_id}")
        self.assertEqual(institution_page.status_code, 200)
        self.assertIn("Institution Research Memory", institution_page.get_data(as_text=True))

    def test_chapter_linking_attaches_alias_and_near_name_matches(self):
        with self.app.app_context():
            conn = get_connection()
            ensure_institutions_table(conn)
            ensure_chapters_table(conn)

            cur = conn.execute(
                """
                INSERT INTO institutions (location_name, alias, city, state)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "Prairie State University and Agricultural & Mechanical College",
                    "Prairie State University|PSU",
                    "Baton Rouge",
                    "LA",
                ),
            )
            prairie_id = int(cur.lastrowid)

            cur = conn.execute(
                """
                INSERT INTO institutions (location_name, city, state)
                VALUES (?, ?, ?)
                """,
                (
                    "Saint Meridian University",
                    "Raleigh",
                    "NC",
                ),
            )
            meridian_id = int(cur.lastrowid)

            cur = conn.execute(
                """
                INSERT INTO institutions (location_name, city, state)
                VALUES (?, ?, ?)
                """,
                (
                    "The University of Harbor-Chattanooga",
                    "Chattanooga",
                    "TN",
                ),
            )
            harbor_id = int(cur.lastrowid)

            conn.executemany(
                """
                INSERT INTO institutions (location_name, city, state)
                VALUES (?, ?, ?)
                """,
                [
                    ("Summit University-North", "North City", "PA"),
                    ("Summit University-South", "South City", "PA"),
                ],
            )

            conn.executemany(
                """
                INSERT INTO chapters (chapter_uid, chapter_name, school, city, state)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("prairie-alpha", "Alpha", "Prairie State University", "Baton Rouge", "LA"),
                    ("meridian-beta", "Beta", "St. Meridian University", "Raleigh", "Inactive North Carolina"),
                    ("harbor-gamma", "Gamma", "University of Harbor at Chattanooga", "Chattanooga", "TN"),
                    ("summit-delta", "Delta", "Summit University", "", "PA"),
                ],
            )
            conn.commit()

            ensure_institutions_table(conn)

            linked = {
                row["chapter_uid"]: row["institution_id"]
                for row in conn.execute(
                    "SELECT chapter_uid, institution_id FROM chapters WHERE chapter_uid IN (?, ?, ?, ?)",
                    ("prairie-alpha", "meridian-beta", "harbor-gamma", "summit-delta"),
                ).fetchall()
            }

        self.assertEqual(linked["prairie-alpha"], prairie_id)
        self.assertEqual(linked["meridian-beta"], meridian_id)
        self.assertEqual(linked["harbor-gamma"], harbor_id)
        self.assertIsNone(linked["summit-delta"])

    def test_dashboard_renders_served_map_for_mappable_institutions(self):
        with self.app.app_context():
            conn = get_connection()
            ensure_institutions_table(conn)
            cur = conn.execute(
                """
                INSERT INTO institutions (
                    location_name, city, state, control, institution_level,
                    latitude, longitude, website
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Beacon University",
                    "Boston",
                    "MA",
                    "Private Institution",
                    "4 year",
                    "42.3601",
                    "-71.0589",
                    "beacon.example.edu",
                ),
            )
            institution_id = int(cur.lastrowid)
            conn.commit()

        self._login("demo", "demo123")
        me = self._me()

        with self.app.app_context():
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO crm_contacts (workspace_id, name, type, status, connection)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    me["workspace_id"],
                    "Beacon University",
                    "school",
                    "closed",
                    f"institution:{institution_id}",
                ),
            )
            conn.commit()

        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("dashboard-served-map", body)
        self.assertIn("Beacon University", body)
        self.assertIn("/institutions/detail", body)

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

    def test_simplified_signup_form_flow(self):
        email = f"user_{uuid.uuid4().hex[:8]}@example.com"
        resp = self.client.post(
            "/signup",
            data={
                "first_name": "Ava",
                "last_name": "Stone",
                "company": "Greenline Manufacturing",
                "email": email,
                "password": "secret123",
                "account_type": "manufacturer",
                "agree_terms": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard", resp.headers.get("Location", ""))
        self.client.get("/logout")
        login_resp = self.client.post(
            "/login",
            data={"login": email, "password": "secret123", "next": "/dashboard"},
            follow_redirects=False,
        )
        self.assertEqual(login_resp.status_code, 302)
        self.assertIn("/dashboard", login_resp.headers.get("Location", ""))

    def test_brand_owner_signup_redirects_to_brand_workspace(self):
        username = f"brand_{uuid.uuid4().hex[:8]}"
        self._signup(
            username=username,
            manufacturer_name="Northwind Brand",
            password="secret123",
            account_type="brand_owner",
            expected_path="/brand/dashboard",
        )
        me = self._me()
        self.assertEqual(me.get("account_type"), "brand_owner")
        self.client.get("/logout")
        resp = self._login(username=username, password="secret123", next_path="/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/brand/dashboard", resp.headers.get("Location", ""))
        landing = self.client.get("/brand/dashboard")
        self.assertEqual(landing.status_code, 200)
        orders_page = self.client.get("/brand/orders")
        self.assertEqual(orders_page.status_code, 200)

    def test_brand_owner_sees_shared_orders_from_multiple_manufacturers(self):
        brand_client = self.app.test_client()
        brand_username = f"brand_{uuid.uuid4().hex[:8]}"
        brand_signup = brand_client.post(
            "/signup",
            data={
                "username": brand_username,
                "password": "secret123",
                "account_type": "brand_owner",
                "account_name": "Atlas Brand Group",
                "contact_email": f"{brand_username}@example.com",
                "agree_terms": "1",
                "security_question": Config.SECURITY_QUESTIONS[0],
                "security_answer": "TestAnswer123",
            },
            follow_redirects=False,
        )
        self.assertEqual(brand_signup.status_code, 302)
        self.assertIn("/brand/dashboard", brand_signup.headers.get("Location", ""))
        brand_me = brand_client.get("/api/me").get_json()["user"]
        brand_ws = brand_me["workspace_id"]

        maker_one = self.app.test_client()
        maker_one.post(
            "/signup",
            data={
                "username": f"maker_{uuid.uuid4().hex[:6]}",
                "password": "secret123",
                "account_type": "manufacturer",
                "account_name": "Maker One",
                "contact_email": "maker1@example.com",
                "agree_terms": "1",
                "security_question": Config.SECURITY_QUESTIONS[0],
                "security_answer": "TestAnswer123",
            },
            follow_redirects=False,
        )
        maker_one.post("/api/ops/brand-links", json={"brand_owner_workspace_id": brand_ws})
        order_one = maker_one.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Maker One Order",
                "client_name": "Atlas Brand Group",
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-18",
                "brand_owner_workspace_id": brand_ws,
            },
        )
        self.assertEqual(order_one.status_code, 200)

        maker_two = self.app.test_client()
        maker_two.post(
            "/signup",
            data={
                "username": f"maker_{uuid.uuid4().hex[:6]}",
                "password": "secret123",
                "account_type": "manufacturer",
                "account_name": "Maker Two",
                "contact_email": "maker2@example.com",
                "agree_terms": "1",
                "security_question": Config.SECURITY_QUESTIONS[0],
                "security_answer": "TestAnswer123",
            },
            follow_redirects=False,
        )
        maker_two.post("/api/ops/brand-links", json={"brand_owner_workspace_id": brand_ws})
        order_two = maker_two.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Maker Two Order",
                "client_name": "Atlas Brand Group",
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-24",
                "brand_owner_workspace_id": brand_ws,
            },
        )
        self.assertEqual(order_two.status_code, 200)

        orders_resp = brand_client.get("/api/brand/orders")
        self.assertEqual(orders_resp.status_code, 200)
        orders_payload = orders_resp.get_json()
        self.assertTrue(orders_payload.get("ok"))
        self.assertEqual(len(orders_payload.get("orders", [])), 2)
        manufacturers = {row.get("manufacturer_name") for row in orders_payload.get("orders", [])}
        self.assertIn("Maker One", manufacturers)
        self.assertIn("Maker Two", manufacturers)

    def test_brand_owner_can_add_order_by_tracking_code(self):
        brand_client = self.app.test_client()
        brand_username = f"brand_{uuid.uuid4().hex[:8]}"
        brand_client.post(
            "/signup",
            data={
                "username": brand_username,
                "password": "secret123",
                "account_type": "brand_owner",
                "account_name": "Code Brand Group",
                "contact_email": f"{brand_username}@example.com",
                "agree_terms": "1",
                "security_question": Config.SECURITY_QUESTIONS[0],
                "security_answer": "TestAnswer123",
            },
            follow_redirects=False,
        )

        maker = self.app.test_client()
        maker.post(
            "/signup",
            data={
                "username": f"maker_{uuid.uuid4().hex[:6]}",
                "password": "secret123",
                "account_type": "manufacturer",
                "account_name": "Code Maker",
                "contact_email": "codemaker@example.com",
                "agree_terms": "1",
                "security_question": Config.SECURITY_QUESTIONS[0],
                "security_answer": "TestAnswer123",
            },
            follow_redirects=False,
        )
        create_resp = maker.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Code Redeem Order",
                "client_name": "Code Brand Group",
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-18",
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        order_id = int(create_resp.get_json()["order_id"])
        order_detail = maker.get(f"/api/ops/orders/{order_id}").get_json()
        access_code = order_detail["order"]["customer_access_code"]

        redeem_resp = brand_client.post("/api/brand/orders/redeem-code", json={"access_code": access_code})
        self.assertEqual(redeem_resp.status_code, 200)
        redeem_payload = redeem_resp.get_json()
        self.assertTrue(redeem_payload.get("ok"))
        self.assertEqual(int(redeem_payload.get("order_id") or 0), order_id)

        orders_resp = brand_client.get("/api/brand/orders")
        self.assertEqual(orders_resp.status_code, 200)
        orders_payload = orders_resp.get_json()
        self.assertTrue(any(int(row.get("id") or 0) == order_id for row in orders_payload.get("orders", [])))

    def test_manufacturer_is_redirected_away_from_brand_pages(self):
        username = f"maker_{uuid.uuid4().hex[:8]}"
        self._signup(username=username, manufacturer_name="Redirect Maker", password="secret123")
        resp = self.client.get("/brand/dashboard", follow_redirects=False)
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
        seed_rows = chapters_seed.get("results", [])
        chapter_id = seed_rows[0]["id"] if seed_rows else ""
        chapter_name = (seed_rows[0].get("chapter_name") if seed_rows else "") or "Meta Chapter"
        org_name = (seed_rows[0].get("organization") if seed_rows else "") or "Alpha Phi Alpha"
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
            chapter_row = next((r for r in chapters_payload.get("results", []) if r.get("id") == chapter_id), None)
            self.assertIsNotNone(chapter_row)
            self.assertIn("crm_stage", chapter_row)
            self.assertIn("open_task_count", chapter_row)

        vendors_payload = self.client.get(f"/api/vendors?q={vendor_name}").get_json()
        self.assertTrue(vendors_payload.get("ok"))
        vendor_row = next((r for r in vendors_payload.get("results", []) if (r.get("vendor") or "").lower() == vendor_name.lower()), None)
        if vendor_row is not None:
            self.assertIn("crm_stage", vendor_row)
            self.assertIn("open_task_count", vendor_row)

    def test_ops_workspace_order_creation_and_detail(self):
        self._login("demo", "demo123")
        me = self._me()
        with self.app.app_context():
            conn = get_connection()
            cur = conn.execute(
                """
                INSERT INTO crm_contacts(workspace_id, name, type, status, connection, notes)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    me["workspace_id"],
                    "Atlas Apparel",
                    "organization",
                    "negotiating",
                    "Retail buyer account",
                    "Prefers weekly visibility",
                ),
            )
            crm_contact_id = int(cur.lastrowid)
            conn.commit()
        resp = self.client.post(
            "/api/ops/orders",
            json={
                "crm_contact_id": crm_contact_id,
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Spring Line Run",
                "product_type": "Hoodie",
                "quantity": 240,
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-18",
                "order_summary": "Launch order for chapter apparel",
            },
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload.get("ok"))
        order_id = int(payload["order_id"])

        detail_resp = self.client.get(f"/api/ops/orders/{order_id}")
        self.assertEqual(detail_resp.status_code, 200)
        detail = detail_resp.get_json()
        self.assertTrue(detail.get("ok"))
        self.assertEqual(detail["order"]["title"], "Spring Line Run")
        self.assertEqual(detail["order"]["client_name"], "Atlas Apparel")
        self.assertEqual(int(detail["order"]["crm_contact_id"] or 0), crm_contact_id)
        self.assertEqual(len(detail["order"]["customer_access_code"] or ""), 8)
        self.assertEqual(len(detail.get("stages", [])), 16)
        self.assertEqual((detail["order"]["current_stage_name"] or "").lower(), "planning / internal tech pack")

        dashboard_resp = self.client.get("/api/ops/dashboard")
        self.assertEqual(dashboard_resp.status_code, 200)
        dashboard = dashboard_resp.get_json()
        self.assertTrue(dashboard.get("ok"))
        rows = dashboard.get("orders", [])
        self.assertTrue(any(int(row.get("id") or 0) == order_id for row in rows))

    def test_ops_updates_issues_and_stage_rollup(self):
        self._login("demo", "demo123")
        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Summer Uniform Batch",
                "client_name": "Beacon Stitch",
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-20",
            },
        )
        order_id = int(create_resp.get_json()["order_id"])
        detail = self.client.get(f"/api/ops/orders/{order_id}").get_json()
        first_stage_id = int(detail["stages"][0]["id"])

        update_resp = self.client.post(
            f"/api/ops/orders/{order_id}/updates",
            json={"summary": "Tech pack approved", "completed_today": "Internal planning complete", "next_step": "Start pattern making"},
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertTrue(update_resp.get_json().get("ok"))

        issue_resp = self.client.post(
            f"/api/ops/orders/{order_id}/issues",
            json={"summary": "Fabric mill pushed dye lot", "reason": "Pantone-matched lot slipped by three days", "revised_due_date": "2026-04-23"},
        )
        self.assertEqual(issue_resp.status_code, 200)
        self.assertTrue(issue_resp.get_json().get("ok"))

        stage_resp = self.client.post(
            f"/api/ops/orders/{order_id}/stages/{first_stage_id}",
            json={"status": "completed", "actual_start_date": "2026-03-30", "actual_end_date": "2026-03-31", "responsible_person": "Ops Lead"},
        )
        self.assertEqual(stage_resp.status_code, 200)
        self.assertTrue(stage_resp.get_json().get("ok"))

        updated_detail = self.client.get(f"/api/ops/orders/{order_id}").get_json()
        self.assertEqual(updated_detail["order"]["last_update_summary"], "Tech pack approved")
        self.assertEqual(updated_detail["order"]["delay_reason"], "Pantone-matched lot slipped by three days")
        self.assertEqual(updated_detail["order"]["revised_ship_date"], "2026-04-23")
        self.assertGreaterEqual(len(updated_detail.get("issues", [])), 1)

        issue_id = int(updated_detail["issues"][0]["id"])
        resolve_resp = self.client.post(f"/api/ops/orders/{order_id}/issues/{issue_id}/resolve", json={})
        self.assertEqual(resolve_resp.status_code, 200)
        self.assertTrue(resolve_resp.get_json().get("ok"))

        cleared_detail = self.client.get(f"/api/ops/orders/{order_id}").get_json()
        self.assertEqual(cleared_detail["order"]["delay_reason"], "")
        self.assertEqual(cleared_detail["order"]["revised_ship_date"], "")
        self.assertEqual((cleared_detail["issues"][0]["status"] or "").lower(), "resolved")

    def test_ops_advance_stage_and_customer_portal_messaging(self):
        self._login("demo", "demo123")
        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Customer Portal Order",
                "client_name": "Riverview Client",
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-20",
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        order_id = int(create_resp.get_json()["order_id"])

        before = self.client.get(f"/api/ops/orders/{order_id}").get_json()
        first_stage_name = before["stages"][0]["stage_name"]
        access_code = before["order"]["customer_access_code"]

        advance_resp = self.client.post(f"/api/ops/orders/{order_id}/advance", json={})
        self.assertEqual(advance_resp.status_code, 200)
        self.assertTrue(advance_resp.get_json().get("ok"))

        after = self.client.get(f"/api/ops/orders/{order_id}").get_json()
        self.assertEqual((after["stages"][0]["status"] or "").lower(), "completed")
        self.assertNotEqual(after["order"]["current_stage_name"], first_stage_name)
        self.assertEqual((after["stages"][1]["status"] or "").lower(), "in_progress")

        customer = self.app.test_client()
        track_resp = customer.post("/ops/track", data={"access_code": access_code}, follow_redirects=False)
        self.assertEqual(track_resp.status_code, 302)
        self.assertIn("/ops/track/view", track_resp.headers.get("Location", ""))

        portal_resp = customer.get("/api/ops/customer/order")
        self.assertEqual(portal_resp.status_code, 200)
        portal_payload = portal_resp.get_json()
        self.assertTrue(portal_payload.get("ok"))
        self.assertEqual(portal_payload["order"]["order_number"], after["order"]["order_number"])

        customer_msg = customer.post("/api/ops/customer/message", json={"message": "Can you confirm the latest production step?"})
        self.assertEqual(customer_msg.status_code, 200)
        self.assertTrue(customer_msg.get_json().get("ok"))

        reply_resp = self.client.post(f"/api/ops/orders/{order_id}/messages", json={"message": "Yes, we have moved into the next production stage."})
        self.assertEqual(reply_resp.status_code, 200)
        self.assertTrue(reply_resp.get_json().get("ok"))

        threaded = self.client.get(f"/api/ops/orders/{order_id}").get_json()
        bodies = [row.get("message") for row in threaded.get("comments", [])]
        self.assertIn("Can you confirm the latest production step?", bodies)
        self.assertIn("Yes, we have moved into the next production stage.", bodies)

    def test_ops_backfills_customer_tracking_code_for_existing_order(self):
        self._login("demo", "demo123")
        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Legacy Tracking Order",
                "client_name": "Legacy Client",
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-12",
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        order_id = int(create_resp.get_json()["order_id"])

        with self.app.app_context():
            conn = get_connection()
            conn.execute(
                """
                UPDATE ops_orders
                SET customer_access_code=NULL,
                    customer_portal_active=0
                WHERE id=?
                """,
                (order_id,),
            )
            conn.commit()

        detail_resp = self.client.get(f"/api/ops/orders/{order_id}")
        self.assertEqual(detail_resp.status_code, 200)
        detail = detail_resp.get_json()
        self.assertTrue(detail.get("ok"))
        self.assertEqual(len(detail["order"]["customer_access_code"] or ""), 8)

        with self.app.app_context():
            conn = get_connection()
            row = conn.execute(
                "SELECT customer_access_code, customer_portal_active FROM ops_orders WHERE id=?",
                (order_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(len(row["customer_access_code"] or ""), 8)
            self.assertEqual(int(row["customer_portal_active"] or 0), 1)

    def test_ops_duration_planner_supports_up_to_60_days(self):
        self._login("demo", "demo123")
        page_resp = self.client.get("/ops")
        self.assertEqual(page_resp.status_code, 200)
        page_body = page_resp.get_data(as_text=True)
        self.assertIn("Ops Studio", page_body)
        self.assertIn("New Order", page_body)

        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Long Range Planner Order",
                "client_name": "Calendar Client",
                "planned_start_date": "2026-03-30",
                "planned_duration_days": 60,
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        payload = create_resp.get_json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("/ops/orders/", payload.get("planner_url", ""))
        order_id = int(payload["order_id"])

        planner_resp = self.client.get(f"/api/ops/orders/{order_id}/planner")
        self.assertEqual(planner_resp.status_code, 200)
        planner = planner_resp.get_json()
        self.assertTrue(planner.get("ok"))
        self.assertEqual(int(planner["schedule_summary"]["planned_duration_days"] or 0), 60)
        self.assertEqual(len(planner.get("schedule_days", [])), 60)
        self.assertEqual(planner["schedule_days"][0]["schedule_date"], "2026-03-30")
        self.assertEqual(planner["schedule_days"][-1]["schedule_date"], "2026-05-28")
        self.assertEqual(int(planner["schedule_summary"]["buffer_day_count"] or 0), 12)

    def test_ops_duration_planner_rejects_more_than_60_days(self):
        self._login("demo", "demo123")
        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Too Long Order",
                "client_name": "Calendar Client",
                "planned_start_date": "2026-03-30",
                "planned_duration_days": 61,
            },
        )
        self.assertEqual(create_resp.status_code, 400)
        payload = create_resp.get_json()
        self.assertFalse(payload.get("ok"))
        self.assertIn("60 days", payload.get("error", ""))

    def test_ops_order_number_auto_assigns_sequentially(self):
        self._login("demo", "demo123")
        first = self.client.post(
            "/api/ops/orders",
            json={
                "title": "Auto Number One",
                "client_name": "Calendar Client",
                "planned_start_date": "2026-03-30",
                "planned_duration_days": 10,
            },
        )
        self.assertEqual(first.status_code, 200)
        first_id = int(first.get_json()["order_id"])
        first_detail = self.client.get(f"/api/ops/orders/{first_id}").get_json()
        self.assertEqual(first_detail["order"]["order_number"], "ORD-00001")

        second = self.client.post(
            "/api/ops/orders",
            json={
                "title": "Auto Number Two",
                "client_name": "Calendar Client",
                "planned_start_date": "2026-04-02",
                "planned_duration_days": 10,
            },
        )
        self.assertEqual(second.status_code, 200)
        second_id = int(second.get_json()["order_id"])
        second_detail = self.client.get(f"/api/ops/orders/{second_id}").get_json()
        self.assertEqual(second_detail["order"]["order_number"], "ORD-00002")

    def test_ops_planner_save_recomputes_stage_dates_and_can_save_default(self):
        self._login("demo", "demo123")
        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Editable Planner Order",
                "client_name": "Calendar Client",
                "planned_start_date": "2026-03-30",
                "planned_duration_days": 18,
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        order_id = int(create_resp.get_json()["order_id"])

        planner = self.client.get(f"/api/ops/orders/{order_id}/planner").get_json()
        rows = planner.get("schedule_days", [])
        self.assertGreaterEqual(len(rows), 3)
        first_stage_id = int(planner["stages"][0]["id"])
        second_stage_id = int(planner["stages"][1]["id"])
        edited_rows = [
            {"order_stage_id": None, "is_buffer_day": 1, "notes": "Hold for kickoff"},
            {"order_stage_id": first_stage_id, "is_buffer_day": 0, "notes": "Start planning"},
            {"order_stage_id": second_stage_id, "is_buffer_day": 0, "notes": ""},
        ] + [
            {"order_stage_id": row.get("order_stage_id"), "is_buffer_day": row.get("is_buffer_day"), "notes": row.get("notes") or ""}
            for row in rows[3:]
        ]

        save_resp = self.client.post(
            f"/api/ops/orders/{order_id}/planner",
            json={"schedule_days": edited_rows},
        )
        self.assertEqual(save_resp.status_code, 200)
        self.assertTrue(save_resp.get_json().get("ok"))

        detail = self.client.get(f"/api/ops/orders/{order_id}").get_json()
        self.assertEqual(detail["stages"][0]["planned_start_date"], "2026-03-31")
        self.assertEqual(detail["stages"][1]["planned_start_date"], "2026-04-01")

        default_resp = self.client.post(f"/api/ops/orders/{order_id}/planner/default", json={})
        self.assertEqual(default_resp.status_code, 200)
        self.assertTrue(default_resp.get_json().get("ok"))

    def test_ops_custom_processes_stay_on_one_order(self):
        self._login("demo", "demo123")
        create_one = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Order With Custom Process",
                "client_name": "Calendar Client",
                "planned_start_date": "2026-03-30",
                "planned_duration_days": 20,
            },
        )
        self.assertEqual(create_one.status_code, 200)
        order_one_id = int(create_one.get_json()["order_id"])

        planner_one = self.client.get(f"/api/ops/orders/{order_one_id}/planner").get_json()
        stages = planner_one.get("stages", [])
        self.assertEqual(len(stages), 16)
        custom_payload = [
            {"id": row.get("id"), "stage_name": row.get("stage_name"), "department": row.get("department")}
            for row in stages
        ]
        custom_payload.append({"stage_name": "Special Trim Review", "department": "Operations"})

        process_resp = self.client.post(
            f"/api/ops/orders/{order_one_id}/processes",
            json={"stages": custom_payload},
        )
        self.assertEqual(process_resp.status_code, 200)
        self.assertTrue(process_resp.get_json().get("ok"))

        updated_one = self.client.get(f"/api/ops/orders/{order_one_id}/planner").get_json()
        self.assertTrue(any((row.get("stage_name") or "") == "Special Trim Review" for row in updated_one.get("stages", [])))

        create_two = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Later Order",
                "client_name": "Calendar Client",
                "planned_start_date": "2026-04-05",
                "planned_duration_days": 20,
            },
        )
        self.assertEqual(create_two.status_code, 200)
        order_two_id = int(create_two.get_json()["order_id"])
        planner_two = self.client.get(f"/api/ops/orders/{order_two_id}/planner").get_json()
        self.assertEqual(len(planner_two.get("stages", [])), 16)
        self.assertFalse(any((row.get("stage_name") or "") == "Special Trim Review" for row in planner_two.get("stages", [])))

    def test_ops_dashboard_marks_orders_red_when_scheduled_day_is_missed(self):
        self._login("demo", "demo123")
        start_date = (date.today() - timedelta(days=3)).isoformat()
        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Overdue Planner Order",
                "client_name": "Calendar Client",
                "planned_start_date": start_date,
                "planned_duration_days": 3,
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        order_id = int(create_resp.get_json()["order_id"])

        dashboard = self.client.get("/api/ops/dashboard").get_json()
        self.assertTrue(dashboard.get("ok"))
        row = next((item for item in dashboard.get("orders", []) if int(item.get("id") or 0) == order_id), None)
        self.assertIsNotNone(row)
        self.assertGreaterEqual(int(row.get("overdue_day_count") or 0), 1)
        self.assertEqual((row.get("schedule_health") or "").lower(), "overdue")
        self.assertIn("Missed planned day", row.get("delay_reason") or "")
        self.assertGreaterEqual(int(dashboard.get("kpis", {}).get("overdue_orders") or 0), 1)

    def test_ops_internal_workspace_summary_and_sample_requests(self):
        self._login("demo", "demo123")
        create_resp = self.client.post(
            "/api/ops/orders",
            json={
                "order_number": f"ORD-{uuid.uuid4().hex[:6].upper()}",
                "title": "Sample Linked Order",
                "client_name": "Northwind Apparel",
                "planned_start_date": "2026-03-30",
                "requested_delivery_date": "2026-04-10",
            },
        )
        self.assertEqual(create_resp.status_code, 200)

        with self.app.app_context():
            conn = get_connection()
            client_row = conn.execute("SELECT id FROM ops_clients WHERE lower(name)=lower(?)", ("Northwind Apparel",)).fetchone()
            self.assertIsNotNone(client_row)
            client_id = int(client_row["id"])

        sample_resp = self.client.post(
            "/api/ops/sample-requests",
            json={"client_id": client_id, "title": "Need strike-off sample", "due_date": "2026-04-02", "notes": "Customer requested early visual confirmation"},
        )
        self.assertEqual(sample_resp.status_code, 200)
        self.assertTrue(sample_resp.get_json().get("ok"))

        internal_resp = self.client.get("/api/ops/internal")
        self.assertEqual(internal_resp.status_code, 200)
        internal = internal_resp.get_json()
        self.assertTrue(internal.get("ok"))
        self.assertGreaterEqual(int(internal["kpis"]["clients"] or 0), 1)
        self.assertGreaterEqual(int(internal["kpis"]["sample_requests_open"] or 0), 1)
        self.assertTrue(any((row.get("title") or "") == "Need strike-off sample" for row in internal.get("sample_requests", [])))


if __name__ == "__main__":
    unittest.main()
