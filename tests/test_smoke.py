import tempfile
import unittest
from pathlib import Path

from app.database import (
    create_loss,
    create_user,
    get_connection,
    init_db,
    reset_user_password,
    update_user,
    upsert_product,
    validate_training_quiz,
    verify_password,
)
from app.server import can_access_admin


class OilKamSmokeTests(unittest.TestCase):
    def build_db(self, tmp: str) -> Path:
        db_path = Path(tmp) / "oilkam-test.db"
        init_db(db_path)
        return db_path

    def test_demo_users_are_seeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                users = conn.execute("SELECT email, role, password_hash FROM users").fetchall()

            emails = {row["email"]: row for row in users}
            self.assertEqual(emails["employe@oilkam.demo"]["role"], "employe")
            self.assertEqual(emails["manager@oilkam.demo"]["role"], "manager")
            self.assertEqual(emails["admin@oilkam.demo"]["role"], "admin")
            self.assertTrue(verify_password("oilkam123", emails["admin@oilkam.demo"]["password_hash"]))

    def test_initial_tasks_are_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                total = conn.execute("SELECT COUNT(*) AS total FROM tasks").fetchone()["total"]
            self.assertGreaterEqual(total, 5)

    def test_manager_can_create_task_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                manager_id = conn.execute(
                    "SELECT id FROM users WHERE email = ?", ("manager@oilkam.demo",)
                ).fetchone()["id"]
                before = conn.execute("SELECT COUNT(*) AS total FROM tasks").fetchone()["total"]
                conn.execute(
                    """
                    INSERT INTO tasks (title, description, frequency, due_time, created_by, active, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        "Contrôle test",
                        "Tâche créée par le test.",
                        "Unique",
                        "17:30",
                        manager_id,
                        "2026-06-24T10:00:00",
                    ),
                )
                after = conn.execute("SELECT COUNT(*) AS total FROM tasks").fetchone()["total"]
            self.assertEqual(after, before + 1)

    def test_products_and_training_are_seeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                products = conn.execute("SELECT COUNT(*) AS total FROM products").fetchone()["total"]
                modules = conn.execute("SELECT COUNT(*) AS total FROM training_modules").fetchone()["total"]
            self.assertGreaterEqual(products, 3)
            self.assertGreaterEqual(modules, 3)

    def test_loss_declaration_calculates_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                product = conn.execute(
                    "SELECT id, unit_price FROM products WHERE name = ?", ("Eau minérale 1,5L",)
                ).fetchone()
                employee_id = conn.execute(
                    "SELECT id FROM users WHERE email = ?", ("employe@oilkam.demo",)
                ).fetchone()["id"]
                value = create_loss(
                    conn,
                    product_id=product["id"],
                    quantity=3,
                    motive="Casse",
                    loss_date="2026-06-24",
                    user_id=employee_id,
                    comment="Test casse",
                )
                loss = conn.execute("SELECT quantity, value FROM losses ORDER BY id DESC LIMIT 1").fetchone()

            self.assertEqual(value, 18.0)
            self.assertEqual(loss["quantity"], 3)
            self.assertEqual(loss["value"], product["unit_price"] * 3)

    def test_user_creation_update_and_password_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                user_id = create_user(
                    conn,
                    name="Test Employé",
                    email="test@oilkam.demo",
                    password="initial123",
                    role="employe",
                )
                update_user(
                    conn,
                    user_id=user_id,
                    name="Test Manager",
                    email="test.manager@oilkam.demo",
                    role="manager",
                    active=1,
                )
                reset_user_password(conn, user_id=user_id, password="nouveau123")
                row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

            self.assertEqual(row["role"], "manager")
            self.assertTrue(verify_password("nouveau123", row["password_hash"]))

    def test_last_admin_cannot_be_deactivated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                admin_id = conn.execute(
                    "SELECT id FROM users WHERE email = ?", ("admin@oilkam.demo",)
                ).fetchone()["id"]
                with self.assertRaises(ValueError):
                    update_user(
                        conn,
                        user_id=admin_id,
                        name="Nadia Admin",
                        email="admin@oilkam.demo",
                        role="admin",
                        active=0,
                    )

    def test_product_creation_and_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                product_id = upsert_product(
                    conn,
                    name="Produit test",
                    category="Boutique",
                    unit_price=12.5,
                    unit="pièce",
                )
                upsert_product(
                    conn,
                    product_id=product_id,
                    name="Produit test modifié",
                    category="Accessoires",
                    unit_price=15.0,
                    unit="boîte",
                    active=0,
                )
                product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()

            self.assertEqual(product["name"], "Produit test modifié")
            self.assertEqual(product["unit_price"], 15.0)
            self.assertEqual(product["active"], 0)

    def test_task_validation_records_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                employee_id = conn.execute(
                    "SELECT id FROM users WHERE email = ?", ("employe@oilkam.demo",)
                ).fetchone()["id"]
                task_id = conn.execute("SELECT id FROM tasks LIMIT 1").fetchone()["id"]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO task_completions (task_id, user_id, completion_date, completed_at, comment)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (task_id, employee_id, "2026-06-24", "2026-06-24T09:00:00", "OK"),
                )
                completion = conn.execute(
                    "SELECT * FROM task_completions WHERE task_id = ? AND user_id = ?",
                    (task_id, employee_id),
                ).fetchone()

            self.assertEqual(completion["comment"], "OK")

    def test_training_quiz_validation_creates_certificate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self.build_db(tmp)
            with get_connection(db_path) as conn:
                employee_id = conn.execute(
                    "SELECT id FROM users WHERE email = ?", ("employe@oilkam.demo",)
                ).fetchone()["id"]
                module = conn.execute(
                    """
                    SELECT m.id, q.correct_option
                    FROM training_modules m
                    JOIN training_quizzes q ON q.module_id = m.id
                    ORDER BY m.sort_order
                    LIMIT 1
                    """
                ).fetchone()
                score = validate_training_quiz(
                    conn,
                    user_id=employee_id,
                    module_id=module["id"],
                    selected_option=module["correct_option"],
                )
                cert = conn.execute(
                    "SELECT * FROM training_certificates WHERE user_id = ? AND module_id = ?",
                    (employee_id, module["id"]),
                ).fetchone()

            self.assertEqual(score, 100)
            self.assertIsNotNone(cert)

    def test_employee_cannot_access_admin_area(self):
        self.assertFalse(can_access_admin("employe"))
        self.assertFalse(can_access_admin("manager"))
        self.assertTrue(can_access_admin("admin"))


if __name__ == "__main__":
    unittest.main()
