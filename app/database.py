from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "oilkam.db"

ROLE_LABELS = {
    "employe": "Employé",
    "manager": "Manager",
    "admin": "Administrateur",
}

LOSS_MOTIVES = ["Péremption", "Casse", "Vol", "Autre"]
TRAINING_STATUSES = ["Non commencé", "En cours", "Terminé"]
TRAINING_PASSING_SCORE = 70


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def get_connection(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored_hash.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    expected = hash_password(password, bytes.fromhex(salt_hex)).split("$", 2)[2]
    return hmac.compare_digest(expected, digest_hex)


def init_db(path: Path | str = DB_PATH) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('employe', 'manager', 'admin')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                frequency TEXT NOT NULL DEFAULT 'Quotidienne',
                responsible_user_id INTEGER REFERENCES users(id),
                due_time TEXT NOT NULL DEFAULT '18:00',
                created_by INTEGER REFERENCES users(id),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                completion_date TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                UNIQUE(task_id, user_id, completion_date)
            );

            CREATE TABLE IF NOT EXISTS attendance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                attendance_date TEXT NOT NULL,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN ('arrivee', 'depart')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                unit_price REAL NOT NULL CHECK (unit_price >= 0),
                unit TEXT NOT NULL DEFAULT 'unité',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS losses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity REAL NOT NULL CHECK (quantity > 0),
                motive TEXT NOT NULL,
                loss_date TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id),
                value REAL NOT NULL CHECK (value >= 0),
                comment TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS training_modules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS training_quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id INTEGER NOT NULL REFERENCES training_modules(id) ON DELETE CASCADE,
                question TEXT NOT NULL,
                option_a TEXT NOT NULL,
                option_b TEXT NOT NULL,
                option_c TEXT NOT NULL,
                correct_option TEXT NOT NULL CHECK (correct_option IN ('A', 'B', 'C')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS training_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                module_id INTEGER NOT NULL REFERENCES training_modules(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'Non commencé',
                progress_percent INTEGER NOT NULL DEFAULT 0 CHECK (progress_percent >= 0 AND progress_percent <= 100),
                score INTEGER,
                validated_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, module_id)
            );

            CREATE TABLE IF NOT EXISTS training_certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                module_id INTEGER NOT NULL REFERENCES training_modules(id) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                validated_at TEXT NOT NULL,
                UNIQUE(user_id, module_id)
            );
            """
        )
        migrate_schema(conn)
        seed_demo_data(conn)


def migrate_schema(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "products", "unit", "TEXT NOT NULL DEFAULT 'unité'")
    ensure_column(conn, "training_modules", "content", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "training_progress", "score", "INTEGER")
    ensure_column(conn, "training_progress", "validated_at", "TEXT")
    normalize_demo_user_names(conn)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def normalize_demo_user_names(conn: sqlite3.Connection) -> None:
    demo_names = {
        "employe@oilkam.demo": "Employé Démo",
        "manager@oilkam.demo": "Manager Démo",
        "admin@oilkam.demo": "Admin Démo",
    }
    for email, name in demo_names.items():
        conn.execute("UPDATE users SET name = ? WHERE email = ?", (name, email))


def seed_demo_data(conn: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    seed_users(conn, now)
    seed_tasks(conn, now)
    seed_products(conn, now)
    seed_training(conn, now)


def seed_users(conn: sqlite3.Connection, now: str) -> None:
    users = [
        ("Employé Démo", "employe@oilkam.demo", "employe"),
        ("Manager Démo", "manager@oilkam.demo", "manager"),
        ("Admin Démo", "admin@oilkam.demo", "admin"),
    ]
    for name, email, role in users:
        conn.execute(
            """
            INSERT OR IGNORE INTO users (name, email, password_hash, role, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (name, email, hash_password("oilkam123"), role, now),
        )


def seed_tasks(conn: sqlite3.Connection, now: str) -> None:
    employee_id = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("employe@oilkam.demo",)
    ).fetchone()["id"]
    manager_id = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("manager@oilkam.demo",)
    ).fetchone()["id"]

    tasks = [
        (
            "Vérifier la propreté de la boutique",
            "Contrôler les rayons, l'entrée et la zone caisse.",
            "Quotidienne",
            employee_id,
            "09:30",
        ),
        (
            "Contrôler le stock boissons",
            "Noter les ruptures visibles avant le réassort.",
            "Quotidienne",
            employee_id,
            "11:00",
        ),
        (
            "Nettoyer la zone caisse",
            "Désinfecter le comptoir et organiser les tickets.",
            "Quotidienne",
            employee_id,
            "14:00",
        ),
        (
            "Vérifier les extincteurs",
            "Contrôler l'accessibilité et signaler toute anomalie.",
            "Hebdomadaire",
            employee_id,
            "16:00",
        ),
        (
            "Relever les anomalies carburant",
            "Reporter tout incident visible sur les pistes.",
            "Quotidienne",
            None,
            "18:00",
        ),
    ]

    for title, description, frequency, responsible_user_id, due_time in tasks:
        conn.execute(
            """
            INSERT INTO tasks (title, description, frequency, responsible_user_id, due_time, created_by, active, created_at)
            SELECT ?, ?, ?, ?, ?, ?, 1, ?
            WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE title = ?)
            """,
            (
                title,
                description,
                frequency,
                responsible_user_id,
                due_time,
                manager_id,
                now,
                title,
            ),
        )


def seed_products(conn: sqlite3.Connection, now: str) -> None:
    products = [
        ("Eau minérale 1,5L", "Boutique", 6.0, "bouteille"),
        ("Boisson gazeuse 33cl", "Boutique", 8.0, "canette"),
        ("Sandwich froid", "Boutique", 24.0, "pièce"),
        ("Huile moteur 5W40", "Lubrifiants", 95.0, "bidon"),
        ("Lave-glace 5L", "Accessoires", 28.0, "bidon"),
        ("Café", "Boutique", 10.0, "gobelet"),
    ]
    for name, category, unit_price, unit in products:
        conn.execute(
            """
            INSERT INTO products (name, category, unit_price, unit, active, created_at)
            SELECT ?, ?, ?, ?, 1, ?
            WHERE NOT EXISTS (SELECT 1 FROM products WHERE name = ?)
            """,
            (name, category, unit_price, unit, now, name),
        )


def seed_training(conn: sqlite3.Connection, now: str) -> None:
    modules = [
        (
            "Sécurité carburant",
            "Gestes essentiels autour des pistes, extincteurs et incidents carburant.",
            "Toujours sécuriser la zone avant toute intervention. En cas de fuite, prévenir le manager, isoler la pompe concernée et utiliser le matériel de sécurité prévu.",
            "Que faut-il faire en priorité en cas de fuite carburant ?",
            "Continuer le service normalement",
            "Sécuriser la zone et prévenir le manager",
            "Nettoyer sans signaler l'incident",
            "B",
            1,
        ),
        (
            "Procédure caisse",
            "Contrôles de caisse, tickets, clôture et règles de base.",
            "La caisse doit être tenue avec rigueur : vérifier les tickets, signaler les écarts et effectuer la clôture selon la procédure validée.",
            "Que faire en cas d'écart de caisse ?",
            "Le signaler au manager",
            "L'ignorer si le montant est faible",
            "Modifier les tickets",
            "A",
            2,
        ),
        (
            "Gestion boutique",
            "Réassort, propreté, dates limites et pertes marchandises.",
            "La boutique doit rester propre, lisible et correctement approvisionnée. Les produits périmés, cassés ou volés doivent être déclarés dans le module pertes.",
            "Que faire avec un produit périmé ?",
            "Le vendre rapidement",
            "Le remettre en rayon",
            "Le retirer et déclarer une perte",
            "C",
            3,
        ),
    ]
    for title, description, content, question, option_a, option_b, option_c, correct_option, sort_order in modules:
        conn.execute(
            """
            INSERT INTO training_modules (title, description, content, sort_order, active, created_at)
            SELECT ?, ?, ?, ?, 1, ?
            WHERE NOT EXISTS (SELECT 1 FROM training_modules WHERE title = ?)
            """,
            (title, description, content, sort_order, now, title),
        )
        module_id = conn.execute("SELECT id FROM training_modules WHERE title = ?", (title,)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO training_quizzes (module_id, question, option_a, option_b, option_c, correct_option, active, created_at)
            SELECT ?, ?, ?, ?, ?, ?, 1, ?
            WHERE NOT EXISTS (SELECT 1 FROM training_quizzes WHERE module_id = ?)
            """,
            (module_id, question, option_a, option_b, option_c, correct_option, now, module_id),
        )

    employees = conn.execute("SELECT id FROM users WHERE role = 'employe'").fetchall()
    module_rows = conn.execute("SELECT id, sort_order FROM training_modules").fetchall()
    for employee in employees:
        for module in module_rows:
            progress = 80 if module["sort_order"] == 1 else 35 if module["sort_order"] == 2 else 0
            status = "Terminé" if progress == 100 else "En cours" if progress > 0 else "Non commencé"
            conn.execute(
                """
                INSERT INTO training_progress (user_id, module_id, status, progress_percent, updated_at)
                SELECT ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM training_progress WHERE user_id = ? AND module_id = ?
                )
                """,
                (
                    employee["id"],
                    module["id"],
                    status,
                    progress,
                    now,
                    employee["id"],
                    module["id"],
                ),
            )


def create_loss(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    quantity: float,
    motive: str,
    loss_date: str | None,
    user_id: int,
    comment: str = "",
) -> float:
    product = conn.execute(
        "SELECT unit_price FROM products WHERE id = ? AND active = 1", (product_id,)
    ).fetchone()
    if product is None:
        raise ValueError("Produit introuvable.")
    if quantity <= 0:
        raise ValueError("La quantité doit être positive.")

    value = round(float(quantity) * float(product["unit_price"]), 2)
    conn.execute(
        """
        INSERT INTO losses (product_id, quantity, motive, loss_date, user_id, value, comment, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            quantity,
            motive,
            loss_date or date.today().isoformat(),
            user_id,
            value,
            comment,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    return value


def record_attendance(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    event_type: str,
    moment: datetime | None = None,
) -> int:
    if event_type not in {"arrivee", "depart"}:
        raise ValueError("Type de pointage invalide.")
    current = moment or datetime.now()
    cursor = conn.execute(
        """
        INSERT INTO attendance_records (user_id, attendance_date, event_time, event_type, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            current.date().isoformat(),
            current.strftime("%H:%M:%S"),
            event_type,
            current.isoformat(timespec="seconds"),
        ),
    )
    return int(cursor.lastrowid)


def active_admin_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND active = 1"
    ).fetchone()
    return int(row["total"])


def create_user(
    conn: sqlite3.Connection,
    *,
    name: str,
    email: str,
    password: str,
    role: str,
    active: int = 1,
) -> int:
    if role not in ROLE_LABELS:
        raise ValueError("Rôle invalide.")
    cursor = conn.execute(
        """
        INSERT INTO users (name, email, password_hash, role, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            email.lower(),
            hash_password(password),
            role,
            1 if active else 0,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    return int(cursor.lastrowid)


def update_user(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    name: str,
    email: str,
    role: str,
    active: int,
) -> None:
    if role not in ROLE_LABELS:
        raise ValueError("Rôle invalide.")
    current = conn.execute("SELECT role, active FROM users WHERE id = ?", (user_id,)).fetchone()
    if current is None:
        raise ValueError("Utilisateur introuvable.")
    removes_last_admin = (
        current["role"] == "admin"
        and current["active"] == 1
        and (role != "admin" or not active)
        and active_admin_count(conn) <= 1
    )
    if removes_last_admin:
        raise ValueError("Impossible de désactiver ou rétrograder le dernier administrateur actif.")
    conn.execute(
        "UPDATE users SET name = ?, email = ?, role = ?, active = ? WHERE id = ?",
        (name, email.lower(), role, 1 if active else 0, user_id),
    )


def reset_user_password(conn: sqlite3.Connection, *, user_id: int, password: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(password), user_id),
    )


def upsert_product(
    conn: sqlite3.Connection,
    *,
    name: str,
    category: str,
    unit_price: float,
    unit: str,
    active: int = 1,
    product_id: int | None = None,
) -> int:
    if unit_price < 0:
        raise ValueError("Le prix unitaire doit être positif.")
    if product_id:
        conn.execute(
            """
            UPDATE products
            SET name = ?, category = ?, unit_price = ?, unit = ?, active = ?
            WHERE id = ?
            """,
            (name, category, unit_price, unit, 1 if active else 0, product_id),
        )
        return product_id
    cursor = conn.execute(
        """
        INSERT INTO products (name, category, unit_price, unit, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, category, unit_price, unit, 1 if active else 0, datetime.now().isoformat(timespec="seconds")),
    )
    return int(cursor.lastrowid)


def validate_training_quiz(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    module_id: int,
    selected_option: str,
) -> int:
    quiz = conn.execute(
        "SELECT correct_option FROM training_quizzes WHERE module_id = ? AND active = 1",
        (module_id,),
    ).fetchone()
    if quiz is None:
        raise ValueError("Quiz introuvable.")
    score = 100 if selected_option == quiz["correct_option"] else 0
    status = "Terminé" if score >= TRAINING_PASSING_SCORE else "En cours"
    progress = 100 if score >= TRAINING_PASSING_SCORE else 50
    validated_at = datetime.now().isoformat(timespec="seconds") if score >= TRAINING_PASSING_SCORE else None
    conn.execute(
        """
        INSERT INTO training_progress (user_id, module_id, status, progress_percent, score, validated_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, module_id) DO UPDATE SET
            status = excluded.status,
            progress_percent = excluded.progress_percent,
            score = excluded.score,
            validated_at = excluded.validated_at,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            module_id,
            status,
            progress,
            score,
            validated_at,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    if score >= TRAINING_PASSING_SCORE:
        conn.execute(
            """
            INSERT INTO training_certificates (user_id, module_id, score, validated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, module_id) DO UPDATE SET
                score = excluded.score,
                validated_at = excluded.validated_at
            """,
            (user_id, module_id, score, validated_at),
        )
    return score
