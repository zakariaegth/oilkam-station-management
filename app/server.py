from __future__ import annotations

import argparse
import csv
import html
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, unquote, urlparse

from .database import (
    DB_PATH,
    LOSS_MOTIVES,
    ROLE_LABELS,
    TRAINING_PASSING_SCORE,
    create_loss,
    create_user,
    get_connection,
    hash_password,
    init_db,
    reset_user_password,
    update_user,
    upsert_product,
    validate_training_quiz,
    verify_password,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "app" / "static"
SESSIONS: dict[str, int] = {}


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def today_iso() -> str:
    return date.today().isoformat()


def current_time_label() -> str:
    return datetime.now().strftime("%H:%M")


def parse_form(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0].strip() for key, values in parsed.items()}


def redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(HTTPStatus.SEE_OTHER)
    handler.send_header("Location", location)
    handler.end_headers()


def can_access_admin(role: str) -> bool:
    return role == "admin"


class OilKamHandler(BaseHTTPRequestHandler):
    server_version = "OilKamDemo/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/static/"):
            self.serve_static(path)
            return

        user = self.current_user()
        if path == "/":
            redirect(self, "/dashboard" if user else "/login")
        elif path == "/login":
            self.respond_html(login_page(self.query_param("erreur")))
        elif path == "/dashboard":
            self.require_user(user, lambda: self.respond_html(dashboard_page(user, self.query_param("erreur"))))
        elif path == "/tasks/history":
            self.require_roles(
                user,
                {"manager", "admin"},
                lambda: self.respond_html(task_history_page(user, self.query_params())),
            )
        elif path == "/losses":
            self.require_user(user, lambda: self.respond_html(losses_page(user, self.query_params())))
        elif path == "/losses/export":
            self.require_roles(user, {"manager", "admin"}, lambda: self.export_losses(user, self.query_params()))
        elif path == "/training":
            self.require_user(user, lambda: self.respond_html(training_page_v3(user, self.query_params())))
        elif path == "/training/certificate":
            self.require_user(user, lambda: self.respond_html(certificate_page(user, self.query_params())))
        elif path == "/reports":
            self.require_roles(user, {"manager", "admin"}, lambda: self.respond_html(reports_page(user, self.query_params())))
        elif path == "/admin/users":
            self.require_roles(user, {"admin"}, lambda: self.respond_html(admin_users_page(user, self.query_params())))
        elif path == "/admin/products":
            self.require_roles(user, {"admin"}, lambda: self.respond_html(admin_products_page(user, self.query_params())))
        elif path == "/admin/training":
            self.require_roles(user, {"admin"}, lambda: self.respond_html(admin_training_page(user, self.query_params())))
        elif path == "/logout":
            self.logout()
        else:
            self.not_found()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        form = self.read_form()
        user = self.current_user()

        if path == "/login":
            self.login(form)
        elif path == "/logout":
            self.logout()
        elif path == "/tasks/complete":
            self.require_user(user, lambda: self.complete_task(user, form))
        elif path == "/tasks/create":
            self.require_roles(user, {"manager", "admin"}, lambda: self.create_task(user, form))
        elif path == "/tasks/update":
            self.require_roles(user, {"manager", "admin"}, lambda: self.update_task(form))
        elif path == "/tasks/deactivate":
            self.require_roles(user, {"manager", "admin"}, lambda: self.deactivate_task(form))
        elif path == "/losses/create":
            self.require_user(user, lambda: self.create_loss(user, form))
        elif path == "/admin/users/create":
            self.require_roles(user, {"admin"}, lambda: self.create_user_action(form))
        elif path == "/admin/users/update":
            self.require_roles(user, {"admin"}, lambda: self.update_user_action(form))
        elif path == "/admin/users/reset-password":
            self.require_roles(user, {"admin"}, lambda: self.reset_password_action(form))
        elif path == "/admin/products/save":
            self.require_roles(user, {"admin"}, lambda: self.save_product_action(form))
        elif path == "/admin/training/save":
            self.require_roles(user, {"admin"}, lambda: self.save_training_module_action(form))
        elif path == "/training/quiz":
            self.require_user(user, lambda: self.submit_training_quiz(user, form))
        else:
            self.not_found()

    def query_param(self, key: str) -> str:
        values = parse_qs(urlparse(self.path).query).get(key, [""])
        return unquote(values[0])

    def query_params(self) -> dict[str, str]:
        parsed = parse_qs(urlparse(self.path).query)
        return {key: values[0].strip() for key, values in parsed.items()}

    def read_form(self) -> dict[str, str]:
        size = int(self.headers.get("Content-Length", "0") or "0")
        return parse_form(self.rfile.read(size))

    def current_user(self) -> sqlite3.Row | None:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get("session")
        if not token or token.value not in SESSIONS:
            return None
        with get_connection() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE id = ? AND active = 1", (SESSIONS[token.value],)
            ).fetchone()

    def require_user(self, user: sqlite3.Row | None, action) -> None:
        if not user:
            redirect(self, "/login")
            return
        action()

    def require_roles(self, user: sqlite3.Row | None, roles: set[str], action) -> None:
        if not user:
            redirect(self, "/login")
            return
        if user["role"] not in roles:
            redirect(self, "/dashboard?erreur=" + quote("Accès non autorisé."))
            return
        action()

    def login(self, form: dict[str, str]) -> None:
        email = form.get("email", "").lower()
        password = form.get("password", "")
        with get_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE lower(email) = ? AND active = 1", (email,)
            ).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            redirect(self, "/login?erreur=" + quote("Email ou mot de passe incorrect."))
            return

        token = secrets.token_urlsafe(32)
        SESSIONS[token] = user["id"]
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/dashboard")
        self.send_header("Set-Cookie", f"session={token}; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def logout(self) -> None:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get("session")
        if token:
            SESSIONS.pop(token.value, None)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", "session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def complete_task(self, user: sqlite3.Row, form: dict[str, str]) -> None:
        task_id = form.get("task_id", "")
        comment = form.get("comment", "")
        now = datetime.now().isoformat(timespec="seconds")
        with get_connection() as conn:
            task = conn.execute(
                """
                SELECT * FROM tasks
                WHERE id = ? AND active = 1
                  AND (responsible_user_id IS NULL OR responsible_user_id = ? OR ? IN ('manager', 'admin'))
                """,
                (task_id, user["id"], user["role"]),
            ).fetchone()
            if task:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO task_completions (task_id, user_id, completion_date, completed_at, comment)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (task["id"], user["id"], today_iso(), now, comment),
                )
        redirect(self, "/dashboard")

    def create_task(self, user: sqlite3.Row, form: dict[str, str]) -> None:
        title = form.get("title", "")
        if not title:
            redirect(self, "/dashboard?erreur=" + quote("Le titre de la tâche est obligatoire."))
            return
        responsible = form.get("responsible_user_id") or None
        due_time = form.get("due_time") or "18:00"
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (title, description, frequency, responsible_user_id, due_time, created_by, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    title,
                    form.get("description", ""),
                    form.get("frequency", "Quotidienne"),
                    responsible,
                    due_time,
                    user["id"],
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        redirect(self, "/dashboard")

    def update_task(self, form: dict[str, str]) -> None:
        task_id = form.get("task_id", "")
        title = form.get("title", "")
        if not task_id or not title:
            redirect(self, "/dashboard?erreur=" + quote("Impossible de modifier la tâche."))
            return
        responsible = form.get("responsible_user_id") or None
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET title = ?, description = ?, frequency = ?, responsible_user_id = ?, due_time = ?
                WHERE id = ?
                """,
                (
                    title,
                    form.get("description", ""),
                    form.get("frequency", "Quotidienne"),
                    responsible,
                    form.get("due_time", "18:00") or "18:00",
                    task_id,
                ),
            )
        redirect(self, "/dashboard")

    def deactivate_task(self, form: dict[str, str]) -> None:
        task_id = form.get("task_id", "")
        if task_id:
            with get_connection() as conn:
                conn.execute("UPDATE tasks SET active = 0 WHERE id = ?", (task_id,))
        redirect(self, "/dashboard")

    def create_loss(self, user: sqlite3.Row, form: dict[str, str]) -> None:
        try:
            product_id = int(form.get("product_id", "0"))
            quantity = float(form.get("quantity", "0").replace(",", "."))
            with get_connection() as conn:
                create_loss(
                    conn,
                    product_id=product_id,
                    quantity=quantity,
                    motive=form.get("motive", "Autre"),
                    loss_date=form.get("loss_date") or today_iso(),
                    user_id=user["id"],
                    comment=form.get("comment", ""),
                )
        except (TypeError, ValueError) as exc:
            redirect(self, "/losses?erreur=" + quote(str(exc)))
            return
        redirect(self, "/losses")

    def export_losses(self, user: sqlite3.Row, filters: dict[str, str]) -> None:
        rows = fetch_losses(user, filters)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Date", "Produit", "Catégorie", "Quantité", "Motif", "Valeur", "Déclaré par", "Commentaire"])
        for row in rows:
            writer.writerow(
                [
                    row["loss_date"],
                    row["product_name"],
                    row["category"],
                    row["quantity"],
                    row["motive"],
                    f"{row['value']:.2f}",
                    row["user_name"],
                    row["comment"],
                ]
            )
        payload = "\ufeff" + output.getvalue()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="pertes_oilkam.csv"')
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def create_user_action(self, form: dict[str, str]) -> None:
        try:
            password = form.get("password") or "oilkam123"
            with get_connection() as conn:
                create_user(
                    conn,
                    name=form.get("name", ""),
                    email=form.get("email", ""),
                    password=password,
                    role=form.get("role", "employe"),
                    active=1 if form.get("active") == "1" else 0,
                )
        except (sqlite3.IntegrityError, ValueError) as exc:
            redirect(self, "/admin/users?erreur=" + quote(f"Création impossible : {exc}"))
            return
        redirect(self, "/admin/users")

    def update_user_action(self, form: dict[str, str]) -> None:
        try:
            with get_connection() as conn:
                update_user(
                    conn,
                    user_id=int(form.get("user_id", "0")),
                    name=form.get("name", ""),
                    email=form.get("email", ""),
                    role=form.get("role", "employe"),
                    active=1 if form.get("active") == "1" else 0,
                )
        except (sqlite3.IntegrityError, ValueError) as exc:
            redirect(self, "/admin/users?erreur=" + quote(str(exc)))
            return
        redirect(self, "/admin/users")

    def reset_password_action(self, form: dict[str, str]) -> None:
        password = form.get("password") or "oilkam123"
        try:
            with get_connection() as conn:
                reset_user_password(conn, user_id=int(form.get("user_id", "0")), password=password)
        except ValueError as exc:
            redirect(self, "/admin/users?erreur=" + quote(str(exc)))
            return
        redirect(self, "/admin/users?message=" + quote("Mot de passe réinitialisé."))

    def save_product_action(self, form: dict[str, str]) -> None:
        try:
            product_id = int(form["product_id"]) if form.get("product_id") else None
            unit_price = float(form.get("unit_price", "0").replace(",", "."))
            with get_connection() as conn:
                upsert_product(
                    conn,
                    product_id=product_id,
                    name=form.get("name", ""),
                    category=form.get("category", ""),
                    unit_price=unit_price,
                    unit=form.get("unit", "unité"),
                    active=1 if form.get("active") == "1" else 0,
                )
        except (sqlite3.IntegrityError, ValueError) as exc:
            redirect(self, "/admin/products?erreur=" + quote(str(exc)))
            return
        redirect(self, "/admin/products")

    def save_training_module_action(self, form: dict[str, str]) -> None:
        try:
            module_id = int(form["module_id"]) if form.get("module_id") else None
            active = 1 if form.get("active") == "1" else 0
            sort_order = int(form.get("sort_order") or 0)
            now = datetime.now().isoformat(timespec="seconds")
            with get_connection() as conn:
                if module_id:
                    conn.execute(
                        """
                        UPDATE training_modules
                        SET title = ?, description = ?, content = ?, sort_order = ?, active = ?
                        WHERE id = ?
                        """,
                        (
                            form.get("title", ""),
                            form.get("description", ""),
                            form.get("content", ""),
                            sort_order,
                            active,
                            module_id,
                        ),
                    )
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO training_modules (title, description, content, sort_order, active, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            form.get("title", ""),
                            form.get("description", ""),
                            form.get("content", ""),
                            sort_order,
                            active,
                            now,
                        ),
                    )
                    module_id = int(cursor.lastrowid)
                quiz = conn.execute(
                    "SELECT id FROM training_quizzes WHERE module_id = ?", (module_id,)
                ).fetchone()
                quiz_values = (
                    form.get("question", ""),
                    form.get("option_a", ""),
                    form.get("option_b", ""),
                    form.get("option_c", ""),
                    form.get("correct_option", "A"),
                    active,
                )
                if quiz:
                    conn.execute(
                        """
                        UPDATE training_quizzes
                        SET question = ?, option_a = ?, option_b = ?, option_c = ?, correct_option = ?, active = ?
                        WHERE module_id = ?
                        """,
                        (*quiz_values, module_id),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO training_quizzes (module_id, question, option_a, option_b, option_c, correct_option, active, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (module_id, *quiz_values, now),
                    )
        except (sqlite3.IntegrityError, ValueError) as exc:
            redirect(self, "/admin/training?erreur=" + quote(str(exc)))
            return
        redirect(self, "/admin/training")

    def submit_training_quiz(self, user: sqlite3.Row, form: dict[str, str]) -> None:
        module_id = int(form.get("module_id", "0"))
        selected = form.get("answer", "")
        try:
            with get_connection() as conn:
                score = validate_training_quiz(
                    conn,
                    user_id=user["id"],
                    module_id=module_id,
                    selected_option=selected,
                )
        except ValueError as exc:
            redirect(self, "/training?erreur=" + quote(str(exc)))
            return
        message = "Formation validée." if score >= TRAINING_PASSING_SCORE else "Score insuffisant, à revoir."
        redirect(self, "/training?message=" + quote(message))

    def serve_static(self, path: str) -> None:
        target = (STATIC_DIR / path.removeprefix("/static/")).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self.not_found()
            return
        content_type = "text/css" if target.suffix == ".css" else "application/javascript"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(target.read_bytes())

    def respond_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def not_found(self) -> None:
        self.respond_html(layout("Page introuvable", "<section><h1>Page introuvable</h1></section>"), HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {format % args}")


def layout(title: str, body: str, user: sqlite3.Row | None = None) -> str:
    name = esc(user["name"]) if user else ""
    role = ROLE_LABELS.get(user["role"], "") if user else ""
    nav = ""
    if user:
        history_link = '<a href="/tasks/history">Historique</a>' if user["role"] in {"manager", "admin"} else ""
        reports_link = '<a href="/reports">Rapports</a>' if user["role"] in {"manager", "admin"} else ""
        admin_links = ""
        if user["role"] == "admin":
            admin_links = '<a href="/admin/users">Utilisateurs</a><a href="/admin/products">Produits</a><a href="/admin/training">Modules</a>'
        nav = f"""
        <header class="topbar">
          <a class="brand" href="/dashboard">
            <span class="brand-mark">OK</span>
            <span class="brand-copy">
              <strong>Oil Kam</strong>
              <small>Gestion station-service</small>
            </span>
          </a>
          <nav class="nav-links" aria-label="Navigation principale">
            <a href="/dashboard">Tableau de bord</a>
            <a href="/losses">Pertes</a>
            <a href="/training">Formations</a>
            {history_link}
            {reports_link}
            {admin_links}
          </nav>
          <div class="user-strip">
            <span class="nav-time">{current_time_label()}</span>
            <span class="user-name">{name}</span>
            <span class="role-badge">{esc(role)}</span>
            <form method="post" action="/logout">
              <button class="ghost-button" type="submit">Déconnexion</button>
            </form>
          </div>
        </header>
        """
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} - Oil Kam</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script defer src="/static/app.js"></script>
</head>
<body>
  {nav}
  <main class="page">
    {body}
  </main>
</body>
</html>"""


def login_page(error: str = "") -> str:
    alert = f'<p class="alert">{esc(error)}</p>' if error else ""
    body = f"""
    <section class="login-shell">
      <div class="login-card">
        <div class="login-intro">
          <div class="brand-block">
            <span class="brand-mark large">OK</span>
            <div>
              <p class="eyebrow">Station-service</p>
              <h1>Oil Kam</h1>
            </div>
          </div>
          <p class="login-lead">Pilotage quotidien des tâches, des équipes et des opérations de station.</p>
          <div class="login-metrics">
            <div><strong>3</strong><span>Rôles</span></div>
            <div><strong>5</strong><span>Tâches test</span></div>
            <div><strong>V1</strong><span>Démo</span></div>
          </div>
        </div>
        <div class="login-panel">
          <div>
            <p class="eyebrow">Accès sécurisé</p>
            <h2>Connexion</h2>
          </div>
          {alert}
          <form class="form-stack" method="post" action="/login">
            <label>Email professionnel
              <input name="email" type="email" autocomplete="username" required autofocus>
            </label>
            <label>Mot de passe
              <input name="password" type="password" autocomplete="current-password" required>
            </label>
            <button class="primary-button" type="submit">Se connecter</button>
          </form>
          <div class="demo-accounts">
            <p>Comptes de démonstration</p>
            <button type="button" data-fill-login="employe@oilkam.demo">Employé</button>
            <button type="button" data-fill-login="manager@oilkam.demo">Manager</button>
            <button type="button" data-fill-login="admin@oilkam.demo">Admin</button>
            <span>Mot de passe : oilkam123</span>
          </div>
        </div>
      </div>
    </section>
    """
    return layout("Connexion", body)


def dashboard_page(user: sqlite3.Row, error: str = "") -> str:
    if user["role"] == "employe":
        content = employee_dashboard(user)
    elif user["role"] == "manager":
        content = manager_dashboard(user)
    else:
        content = admin_dashboard(user)
    if error:
        content = f'<p class="alert page-alert">{esc(error)}</p>' + content
    return layout("Tableau de bord", content, user)


def load_tasks_for_user(user_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT t.*, u.name AS responsible_name, c.completed_at, c.comment
            FROM tasks t
            LEFT JOIN users u ON u.id = t.responsible_user_id
            LEFT JOIN task_completions c
              ON c.task_id = t.id AND c.user_id = ? AND c.completion_date = ?
            WHERE t.active = 1 AND (t.responsible_user_id IS NULL OR t.responsible_user_id = ?)
            ORDER BY t.due_time, t.id
            """,
            (user_id, today_iso(), user_id),
        ).fetchall()


def employee_dashboard(user: sqlite3.Row) -> str:
    tasks = load_tasks_for_user(user["id"])
    completed = sum(1 for task in tasks if task["completed_at"])
    total = len(tasks)
    pending = max(total - completed, 0)
    progress = int((completed / total) * 100) if total else 0
    task_items = "\n".join(task_card(task, can_complete=True) for task in tasks)
    if not task_items:
        task_items = '<p class="empty-state">Aucune tâche prévue pour aujourd’hui.</p>'

    return f"""
    <section class="hero-band dashboard-hero">
      <div class="hero-copy">
        <p class="eyebrow">Tableau de bord employé</p>
        <h1>Bonjour {esc(user["name"])}</h1>
        <p>Priorité du jour : valider les tâches terrain dès qu’elles sont terminées.</p>
      </div>
      <div class="progress-tile">
        <span>{completed}/{total}</span>
        <small>Tâches effectuées</small>
      </div>
    </section>
    <section class="stat-grid">
      <article class="stat-card">
        <span class="stat-label">Progression</span>
        <strong>{progress}%</strong>
        <small>Avancement personnel</small>
      </article>
      <article class="stat-card">
        <span class="stat-label">Restantes</span>
        <strong>{pending}</strong>
        <small>Tâches à finaliser</small>
      </article>
      <article class="stat-card accent">
        <span class="stat-label">Heure actuelle</span>
        <strong>{current_time_label()}</strong>
        <small>Suivi opérationnel</small>
      </article>
    </section>
    <section class="content-grid">
      <div class="main-column">
        <div class="panel">
          <div class="section-head">
            <div>
              <p class="section-kicker">Aujourd’hui</p>
              <h2>Mes tâches du jour</h2>
            </div>
            <span class="meter"><span style="width:{progress}%"></span></span>
          </div>
          <div class="task-list">{task_items}</div>
        </div>
      </div>
      <aside class="side-panel">
        <p class="section-kicker">Raccourcis</p>
        <h2>Actions rapides</h2>
        <a class="quick-action" href="/losses"><span class="action-mark fuel"></span><strong>Déclarer une perte</strong><small>Formulaire rapide</small></a>
        <a class="quick-action" href="/training"><span class="action-mark safety"></span><strong>Mes formations</strong><small>Progression personnelle</small></a>
        <a class="quick-action disabled" href="#" aria-disabled="true"><span class="action-mark history"></span><strong>Historique</strong><small>Consultation à venir</small></a>
      </aside>
    </section>
    """


def manager_dashboard(user: sqlite3.Row) -> str:
    with get_connection() as conn:
        employees = conn.execute(
            "SELECT * FROM users WHERE role = 'employe' AND active = 1 ORDER BY name"
        ).fetchall()
        all_tasks = conn.execute(
            """
            SELECT t.*, u.name AS responsible_name, c.completed_at, c.comment
            FROM tasks t
            LEFT JOIN users u ON u.id = t.responsible_user_id
            LEFT JOIN task_completions c ON c.task_id = t.id AND c.completion_date = ?
            WHERE t.active = 1
            ORDER BY t.due_time, t.id
            """,
            (today_iso(),),
        ).fetchall()
    summary = employee_progress_rows(employees)
    alerts = manager_alerts(all_tasks)
    total_tasks = len(all_tasks)
    completed_tasks = sum(1 for task in all_tasks if task["completed_at"])
    pending_tasks = max(total_tasks - completed_tasks, 0)
    overdue_tasks = overdue_count(all_tasks)
    progress = int((completed_tasks / total_tasks) * 100) if total_tasks else 0
    task_items = "\n".join(task_card(task, can_complete=False) for task in all_tasks)
    return f"""
    <section class="hero-band dashboard-hero">
      <div class="hero-copy">
        <p class="eyebrow">Tableau de bord manager</p>
        <h1>Suivi opérationnel du jour</h1>
        <p>Vue synthétique de l’avancement équipe et des points à traiter.</p>
      </div>
      <div class="progress-tile">
        <span>{completed_tasks}/{total_tasks}</span>
        <small>Validations</small>
      </div>
    </section>
    <section class="stat-grid">
      <article class="stat-card">
        <span class="stat-label">Avancement</span>
        <strong>{progress}%</strong>
        <small>Tâches validées</small>
      </article>
      <article class="stat-card">
        <span class="stat-label">À suivre</span>
        <strong>{pending_tasks}</strong>
        <small>Tâches ouvertes</small>
      </article>
      <article class="stat-card warning">
        <span class="stat-label">Alertes</span>
        <strong>{overdue_tasks}</strong>
        <small>Retards détectés</small>
      </article>
    </section>
    <section class="content-grid manager-grid">
      <div class="main-column">
        <div class="panel">
          <div class="section-head">
            <div>
              <p class="section-kicker">Équipe</p>
              <h2>Avancement des employés</h2>
            </div>
          </div>
          <div class="progress-list">{summary}</div>
        </div>
        <div class="panel">
          <div class="section-head">
            <div>
              <p class="section-kicker">Opérations</p>
              <h2>Tâches suivies</h2>
            </div>
            <span class="meter"><span style="width:{progress}%"></span></span>
          </div>
          <div class="task-list">{task_items}</div>
        </div>
        <div class="panel">
          <div class="section-head">
            <div>
              <p class="section-kicker">Gestion</p>
              <h2>Modifier les tâches</h2>
            </div>
            <a class="text-link" href="/tasks/history">Voir l’historique</a>
          </div>
          {task_admin_list(all_tasks)}
        </div>
      </div>
      <aside class="side-panel">
        <p class="section-kicker">Contrôle</p>
        <h2>Alertes</h2>
        {alerts}
        <a class="quick-action" href="/losses"><span class="action-mark fuel"></span><strong>Suivi des pertes</strong><small>Consulter et exporter</small></a>
        {task_form()}
      </aside>
    </section>
    """


def admin_dashboard(user: sqlite3.Row) -> str:
    with get_connection() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY role, name").fetchall()
        counts = conn.execute(
            "SELECT role, COUNT(*) AS total FROM users WHERE active = 1 GROUP BY role"
        ).fetchall()
    rows = "\n".join(
        f"""
        <tr>
          <td><strong>{esc(row["name"])}</strong></td>
          <td>{esc(row["email"])}</td>
          <td><span class="role-badge">{esc(ROLE_LABELS[row["role"]])}</span></td>
          <td><span class="status-pill {'success' if row['active'] else 'muted'}">{'Actif' if row['active'] else 'Inactif'}</span></td>
        </tr>
        """
        for row in users
    )
    stats = "\n".join(
        f'<div class="stat"><strong>{row["total"]}</strong><span>{esc(ROLE_LABELS[row["role"]])}</span></div>'
        for row in counts
    )
    return f"""
    <section class="hero-band dashboard-hero">
      <div class="hero-copy">
        <p class="eyebrow">Administration</p>
        <h1>Comptes et configuration</h1>
        <p>Vue de contrôle des accès et des paramètres disponibles dans la V1.</p>
      </div>
      <div class="stats-row">{stats}</div>
    </section>
    <section class="stat-grid">
      <article class="stat-card">
        <span class="stat-label">Utilisateurs</span>
        <strong>{len(users)}</strong>
        <small>Comptes enregistrés</small>
      </article>
      <article class="stat-card">
        <span class="stat-label">Rôles</span>
        <strong>3</strong>
        <small>Employé, manager, admin</small>
      </article>
      <article class="stat-card accent">
        <span class="stat-label">Base</span>
        <strong>SQLite</strong>
        <small>Données locales</small>
      </article>
    </section>
    <section class="content-grid manager-grid">
      <div class="main-column">
        <div class="panel">
          <div class="section-head">
            <div>
              <p class="section-kicker">Accès</p>
              <h2>Utilisateurs</h2>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Nom</th><th>Email</th><th>Rôle</th><th>Statut</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
          </div>
        </div>
        <div class="panel">
          <div class="section-head">
            <div>
              <p class="section-kicker">Tâches</p>
              <h2>Gestion des tâches actives</h2>
            </div>
          </div>
          {task_admin_list(active_tasks_for_admin())}
        </div>
      </div>
      <aside class="side-panel">
        <p class="section-kicker">Configuration</p>
        <h2>Paramètres V1</h2>
        <div class="setting-row"><span>Rôles</span><strong>3 actifs</strong></div>
        <div class="setting-row"><span>Base de données</span><strong>SQLite</strong></div>
        <div class="setting-row"><span>Module tâches</span><strong>Actif</strong></div>
        <a class="quick-action" href="/admin/users"><span class="action-mark safety"></span><strong>Gérer les utilisateurs</strong><small>Comptes et rôles</small></a>
        <a class="quick-action" href="/admin/products"><span class="action-mark fuel"></span><strong>Gérer les produits</strong><small>Catalogue pertes</small></a>
        <a class="quick-action" href="/admin/training"><span class="action-mark history"></span><strong>Gérer les formations</strong><small>Modules et quiz</small></a>
        {task_form()}
      </aside>
    </section>
    """


def task_state(task: sqlite3.Row) -> tuple[str, str]:
    if task["completed_at"]:
        return "Complétée", "success"
    try:
        due = time.fromisoformat(task["due_time"])
    except ValueError:
        due = time(18, 0)
    if due < datetime.now().time():
        return "En retard", "danger"
    return "À faire", "pending"


def active_tasks_for_admin() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT t.*, u.name AS responsible_name, NULL AS completed_at, '' AS comment
            FROM tasks t
            LEFT JOIN users u ON u.id = t.responsible_user_id
            WHERE t.active = 1
            ORDER BY t.due_time, t.id
            """
        ).fetchall()


def employee_options(selected_id: int | None = None) -> str:
    with get_connection() as conn:
        employees = conn.execute(
            "SELECT id, name FROM users WHERE role = 'employe' AND active = 1 ORDER BY name"
        ).fetchall()
    options = [f'<option value="" {"selected" if selected_id is None else ""}>Équipe</option>']
    for employee in employees:
        selected = "selected" if selected_id == employee["id"] else ""
        options.append(f'<option value="{employee["id"]}" {selected}>{esc(employee["name"])}</option>')
    return "".join(options)


def task_admin_list(tasks: list[sqlite3.Row]) -> str:
    if not tasks:
        return '<p class="empty-state">Aucune tâche active à gérer.</p>'
    cards = []
    for task in tasks:
        cards.append(
            f"""
            <article class="management-card">
              <form class="management-form" method="post" action="/tasks/update">
                <input type="hidden" name="task_id" value="{task["id"]}">
                <label>Titre
                  <input name="title" value="{esc(task["title"])}" required>
                </label>
                <label>Description
                  <textarea name="description" rows="2">{esc(task["description"])}</textarea>
                </label>
                <div class="inline-fields">
                  <label>Fréquence
                    <select name="frequency">
                      <option {"selected" if task["frequency"] == "Quotidienne" else ""}>Quotidienne</option>
                      <option {"selected" if task["frequency"] == "Hebdomadaire" else ""}>Hebdomadaire</option>
                      <option {"selected" if task["frequency"] == "Unique" else ""}>Unique</option>
                    </select>
                  </label>
                  <label>Responsable
                    <select name="responsible_user_id">{employee_options(task["responsible_user_id"])}</select>
                  </label>
                  <label>Heure limite
                    <input name="due_time" type="time" value="{esc(task["due_time"])}">
                  </label>
                </div>
                <button class="primary-button small" type="submit">Modifier</button>
              </form>
              <form class="button-row" method="post" action="/tasks/deactivate">
                <input type="hidden" name="task_id" value="{task["id"]}">
                <button class="danger-button small" type="submit">Désactiver</button>
              </form>
            </article>
            """
        )
    return '<div class="management-list">' + "\n".join(cards) + "</div>"


def task_card(task: sqlite3.Row, can_complete: bool) -> str:
    done = bool(task["completed_at"])
    status, status_class = task_state(task)
    form = ""
    if can_complete and not done:
        form = f"""
        <form class="task-form" method="post" action="/tasks/complete">
          <input type="hidden" name="task_id" value="{task['id']}">
          <input name="comment" placeholder="Commentaire optionnel">
          <button class="primary-button small" type="submit">Tâche effectuée</button>
        </form>
        """
    elif done and task["comment"]:
        form = f'<p class="task-comment">Commentaire : {esc(task["comment"])}</p>'

    return f"""
    <article class="task-card {'done' if done else ''}">
      <div class="task-status" aria-hidden="true">{'✓' if done else ''}</div>
      <div class="task-body">
        <div class="task-title-row">
          <h3>{esc(task["title"])}</h3>
          <span class="status-pill {status_class}">{status}</span>
        </div>
        <p>{esc(task["description"])}</p>
        <div class="task-meta">
          <span>{esc(task["frequency"])}</span>
          <span>{esc(task["responsible_name"] or 'Équipe')}</span>
          <span>Limite {esc(task["due_time"])}</span>
        </div>
        {form}
      </div>
    </article>
    """


def employee_progress_rows(employees: list[sqlite3.Row]) -> str:
    if not employees:
        return '<p class="empty-state">Aucun employé actif.</p>'
    rows = []
    with get_connection() as conn:
        for employee in employees:
            tasks = conn.execute(
                """
                SELECT t.id, c.id AS completion_id
                FROM tasks t
                LEFT JOIN task_completions c
                  ON c.task_id = t.id AND c.user_id = ? AND c.completion_date = ?
                WHERE t.active = 1 AND (t.responsible_user_id IS NULL OR t.responsible_user_id = ?)
                """,
                (employee["id"], today_iso(), employee["id"]),
            ).fetchall()
            total = len(tasks)
            completed = sum(1 for task in tasks if task["completion_id"])
            percent = int((completed / total) * 100) if total else 0
            rows.append(
                f"""
                <div class="employee-progress">
                  <div class="progress-row-head"><strong>{esc(employee["name"])}</strong><span>{completed}/{total} tâches</span></div>
                  <span class="meter"><span style="width:{percent}%"></span></span>
                  <small>{percent}% réalisé</small>
                </div>
                """
            )
    return "\n".join(rows)


def overdue_count(tasks: list[sqlite3.Row]) -> int:
    now = datetime.now().time()
    total = 0
    for task in tasks:
        if task["completed_at"]:
            continue
        try:
            due = time.fromisoformat(task["due_time"])
        except ValueError:
            due = time(18, 0)
        if due < now:
            total += 1
    return total


def manager_alerts(tasks: list[sqlite3.Row]) -> str:
    overdue = overdue_count(tasks)
    lines = [
        f"{overdue} tâche(s) en retard" if overdue else "Aucune tâche en retard",
        "Module pertes prévu après validation",
    ]
    return "<ul class=\"alert-list\">" + "".join(f"<li>{esc(line)}</li>" for line in lines) + "</ul>"


def task_form() -> str:
    with get_connection() as conn:
        employees = conn.execute(
            "SELECT id, name FROM users WHERE role = 'employe' AND active = 1 ORDER BY name"
        ).fetchall()
    options = '<option value="">Équipe</option>' + "".join(
        f'<option value="{row["id"]}">{esc(row["name"])}</option>' for row in employees
    )
    return f"""
    <form class="form-stack task-create" method="post" action="/tasks/create">
      <div>
        <p class="section-kicker">Planification</p>
        <h2>Nouvelle tâche</h2>
      </div>
      <div class="field-stack">
        <label>Titre
          <input name="title" required>
        </label>
        <label>Description
          <textarea name="description" rows="3"></textarea>
        </label>
        <label>Fréquence
          <select name="frequency">
            <option>Quotidienne</option>
            <option>Hebdomadaire</option>
            <option>Unique</option>
          </select>
        </label>
        <label>Responsable
          <select name="responsible_user_id">{options}</select>
        </label>
        <label>Heure limite
          <input name="due_time" type="time" value="18:00">
        </label>
      </div>
      <button class="primary-button" type="submit">Enregistrer</button>
    </form>
    """


def employees_filter_options(selected_id: str = "") -> str:
    with get_connection() as conn:
        employees = conn.execute(
            "SELECT id, name FROM users WHERE role = 'employe' AND active = 1 ORDER BY name"
        ).fetchall()
    options = [f'<option value="" {"selected" if not selected_id else ""}>Tous les employés</option>']
    for employee in employees:
        selected = "selected" if selected_id == str(employee["id"]) else ""
        options.append(f'<option value="{employee["id"]}" {selected}>{esc(employee["name"])}</option>')
    return "".join(options)


def task_history_page(user: sqlite3.Row, filters: dict[str, str]) -> str:
    selected_date = filters.get("date", today_iso())
    selected_employee = filters.get("employee_id", "")
    clauses = ["1 = 1"]
    params: list[object] = []
    if selected_date:
        clauses.append("c.completion_date = ?")
        params.append(selected_date)
    if selected_employee:
        clauses.append("c.user_id = ?")
        params.append(selected_employee)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.*, t.title, t.frequency, t.due_time, u.name AS user_name
            FROM task_completions c
            JOIN tasks t ON t.id = c.task_id
            JOIN users u ON u.id = c.user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.completed_at DESC
            """,
            params,
        ).fetchall()

    table_rows = "\n".join(
        f"""
        <tr>
          <td>{esc(row["completion_date"])}</td>
          <td><strong>{esc(row["title"])}</strong></td>
          <td>{esc(row["user_name"])}</td>
          <td>{esc(row["frequency"])}</td>
          <td>{esc(row["completed_at"][11:16])}</td>
          <td>{esc(row["comment"])}</td>
        </tr>
        """
        for row in rows
    )
    if not table_rows:
        table_rows = '<tr><td colspan="6">Aucune tâche complétée pour ces filtres.</td></tr>'

    query = urlencode({"date": selected_date, "employee_id": selected_employee})
    return layout(
        "Historique des tâches",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Historique</p>
            <h1>Tâches complétées</h1>
            <p>Suivi des validations par date, employé et commentaire.</p>
          </div>
          <div class="progress-tile">
            <span>{len(rows)}</span>
            <small>Validations filtrées</small>
          </div>
        </section>
        <section class="panel page-panel">
          <form class="filters-bar" method="get" action="/tasks/history">
            <label>Date
              <input type="date" name="date" value="{esc(selected_date)}">
            </label>
            <label>Employé
              <select name="employee_id">{employees_filter_options(selected_employee)}</select>
            </label>
            <button class="primary-button" type="submit">Filtrer</button>
            <a class="ghost-link" href="/tasks/history">Réinitialiser</a>
          </form>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Tâche</th><th>Employé</th><th>Fréquence</th><th>Heure</th><th>Commentaire</th></tr></thead>
              <tbody>{table_rows}</tbody>
            </table>
          </div>
        </section>
        """,
        user,
    )


def fetch_products() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE active = 1 ORDER BY category, name"
        ).fetchall()


def product_options(selected_id: str = "") -> str:
    options = [f'<option value="" {"selected" if not selected_id else ""}>Tous les produits</option>']
    for product in fetch_products():
        selected = "selected" if selected_id == str(product["id"]) else ""
        options.append(
            f'<option value="{product["id"]}" {selected}>{esc(product["name"])} - {product["unit_price"]:.2f} MAD</option>'
        )
    return "".join(options)


def motive_options(selected: str = "", include_all: bool = True) -> str:
    options = []
    if include_all:
        options.append(f'<option value="" {"selected" if not selected else ""}>Tous les motifs</option>')
    for motive in LOSS_MOTIVES:
        is_selected = "selected" if selected == motive else ""
        options.append(f'<option {is_selected}>{esc(motive)}</option>')
    return "".join(options)


def loss_where_clause(user: sqlite3.Row, filters: dict[str, str]) -> tuple[str, list[object]]:
    clauses = ["1 = 1"]
    params: list[object] = []
    if user["role"] == "employe":
        clauses.append("l.user_id = ?")
        params.append(user["id"])
    if filters.get("date"):
        clauses.append("l.loss_date = ?")
        params.append(filters["date"])
    if filters.get("motive"):
        clauses.append("l.motive = ?")
        params.append(filters["motive"])
    if filters.get("product_id"):
        clauses.append("l.product_id = ?")
        params.append(filters["product_id"])
    return " AND ".join(clauses), params


def fetch_losses(user: sqlite3.Row, filters: dict[str, str]) -> list[sqlite3.Row]:
    where_sql, params = loss_where_clause(user, filters)
    with get_connection() as conn:
        return conn.execute(
            f"""
            SELECT l.*, p.name AS product_name, p.category, u.name AS user_name
            FROM losses l
            JOIN products p ON p.id = l.product_id
            JOIN users u ON u.id = l.user_id
            WHERE {where_sql}
            ORDER BY l.loss_date DESC, l.created_at DESC
            """,
            params,
        ).fetchall()


def loss_total_for_period(user: sqlite3.Row, start_date: date) -> float:
    filters: dict[str, str] = {}
    clauses = ["l.loss_date >= ?"]
    params: list[object] = [start_date.isoformat()]
    if user["role"] == "employe":
        clauses.append("l.user_id = ?")
        params.append(user["id"])
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM(l.value), 0) AS total FROM losses l WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
    return float(row["total"])


def losses_page(user: sqlite3.Row, filters: dict[str, str]) -> str:
    error = filters.get("erreur", "")
    rows = fetch_losses(user, filters)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    total_filtered = sum(float(row["value"]) for row in rows)
    table_rows = "\n".join(
        f"""
        <tr>
          <td>{esc(row["loss_date"])}</td>
          <td><strong>{esc(row["product_name"])}</strong><small>{esc(row["category"])}</small></td>
          <td>{row["quantity"]:g}</td>
          <td><span class="status-pill pending">{esc(row["motive"])}</span></td>
          <td><strong>{row["value"]:.2f} MAD</strong></td>
          <td>{esc(row["user_name"])}</td>
        </tr>
        """
        for row in rows
    )
    if not table_rows:
        table_rows = '<tr><td colspan="6">Aucune perte déclarée pour ces filtres.</td></tr>'

    filter_query = urlencode(
        {
            key: filters.get(key, "")
            for key in ["date", "motive", "product_id"]
            if filters.get(key)
        }
    )
    export_link = f"/losses/export?{filter_query}" if filter_query else "/losses/export"
    export_action = (
        f'<a class="ghost-link" href="{export_link}">Exporter CSV</a>'
        if user["role"] in {"manager", "admin"}
        else ""
    )
    alert = f'<p class="alert">{esc(error)}</p>' if error else ""

    return layout(
        "Pertes",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Module pertes</p>
            <h1>Déclaration et suivi des pertes</h1>
            <p>Calcul automatique de la valeur perdue à partir du prix unitaire produit.</p>
          </div>
          <div class="progress-tile">
            <span>{total_filtered:.0f}</span>
            <small>MAD filtrés</small>
          </div>
        </section>
        <section class="stat-grid">
          <article class="stat-card"><span class="stat-label">Aujourd’hui</span><strong>{loss_total_for_period(user, today):.0f}</strong><small>MAD déclarés</small></article>
          <article class="stat-card"><span class="stat-label">Semaine</span><strong>{loss_total_for_period(user, week_start):.0f}</strong><small>Depuis lundi</small></article>
          <article class="stat-card accent"><span class="stat-label">Mois</span><strong>{loss_total_for_period(user, month_start):.0f}</strong><small>Mois en cours</small></article>
        </section>
        <section class="content-grid manager-grid">
          <div class="main-column">
            <div class="panel">
              <div class="section-head">
                <div><p class="section-kicker">Suivi</p><h2>Pertes déclarées</h2></div>
                {export_action}
              </div>
              <form class="filters-bar" method="get" action="/losses">
                <label>Date
                  <input type="date" name="date" value="{esc(filters.get("date", ""))}">
                </label>
                <label>Motif
                  <select name="motive">{motive_options(filters.get("motive", ""))}</select>
                </label>
                <label>Produit
                  <select name="product_id">{product_options(filters.get("product_id", ""))}</select>
                </label>
                <button class="primary-button" type="submit">Filtrer</button>
                <a class="ghost-link" href="/losses">Réinitialiser</a>
              </form>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Date</th><th>Produit</th><th>Qté</th><th>Motif</th><th>Valeur</th><th>Déclaré par</th></tr></thead>
                  <tbody>{table_rows}</tbody>
                </table>
              </div>
            </div>
          </div>
          <aside class="side-panel">
            <p class="section-kicker">Déclaration</p>
            <h2>Nouvelle perte</h2>
            {alert}
            <form class="form-stack" method="post" action="/losses/create">
              <label>Produit
                <select name="product_id" required>{product_options("")}</select>
              </label>
              <label>Quantité
                <input name="quantity" type="number" min="0.01" step="0.01" required>
              </label>
              <label>Motif
                <select name="motive">{motive_options("Péremption", include_all=False)}</select>
              </label>
              <label>Date
                <input name="loss_date" type="date" value="{today_iso()}">
              </label>
              <label>Commentaire
                <textarea name="comment" rows="3"></textarea>
              </label>
              <button class="primary-button" type="submit">Enregistrer la perte</button>
            </form>
          </aside>
        </section>
        """,
        user,
    )


def training_page(user: sqlite3.Row) -> str:
    with get_connection() as conn:
        modules = conn.execute(
            """
            SELECT m.*, COALESCE(tp.status, 'Non commencé') AS status,
                   COALESCE(tp.progress_percent, 0) AS progress_percent
            FROM training_modules m
            LEFT JOIN training_progress tp ON tp.module_id = m.id AND tp.user_id = ?
            WHERE m.active = 1
            ORDER BY m.sort_order, m.id
            """,
            (user["id"],),
        ).fetchall()
        team_rows = conn.execute(
            """
            SELECT u.name AS user_name, m.title, COALESCE(tp.status, 'Non commencé') AS status,
                   COALESCE(tp.progress_percent, 0) AS progress_percent
            FROM users u
            CROSS JOIN training_modules m
            LEFT JOIN training_progress tp ON tp.user_id = u.id AND tp.module_id = m.id
            WHERE u.role = 'employe' AND u.active = 1 AND m.active = 1
            ORDER BY u.name, m.sort_order
            """
        ).fetchall()

    module_cards = "\n".join(
        f"""
        <article class="module-card">
          <div>
            <p class="section-kicker">{esc(row["status"])}</p>
            <h3>{esc(row["title"])}</h3>
            <p>{esc(row["description"])}</p>
          </div>
          <span class="meter"><span style="width:{row["progress_percent"]}%"></span></span>
          <small>{row["progress_percent"]}% de progression</small>
        </article>
        """
        for row in modules
    )
    team_table = ""
    if user["role"] in {"manager", "admin"}:
        team_table_rows = "\n".join(
            f"""
            <tr>
              <td>{esc(row["user_name"])}</td>
              <td><strong>{esc(row["title"])}</strong></td>
              <td><span class="status-pill pending">{esc(row["status"])}</span></td>
              <td>{row["progress_percent"]}%</td>
            </tr>
            """
            for row in team_rows
        )
        team_table = f"""
        <div class="panel">
          <div class="section-head"><div><p class="section-kicker">Équipe</p><h2>Progression employés</h2></div></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Employé</th><th>Module</th><th>Statut</th><th>Progression</th></tr></thead>
              <tbody>{team_table_rows}</tbody>
            </table>
          </div>
        </div>
        """

    return layout(
        "Formations",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Module formation</p>
            <h1>Formations internes</h1>
            <p>Première base pour suivre l’intégration et les modules obligatoires.</p>
          </div>
          <div class="progress-tile">
            <span>{len(modules)}</span>
            <small>Modules actifs</small>
          </div>
        </section>
        <section class="content-grid">
          <div class="main-column">
            <div class="panel">
              <div class="section-head"><div><p class="section-kicker">Mon parcours</p><h2>Modules disponibles</h2></div></div>
              <div class="module-list">{module_cards}</div>
            </div>
            {team_table}
          </div>
          <aside class="side-panel">
            <p class="section-kicker">V2 préparée</p>
            <h2>À compléter</h2>
            <div class="setting-row"><span>Quiz</span><strong>Prévu</strong></div>
            <div class="setting-row"><span>Attestation</span><strong>Prévu</strong></div>
            <div class="setting-row"><span>Contenus</span><strong>Démo</strong></div>
          </aside>
        </section>
        """,
        user,
    )


def role_options(selected: str) -> str:
    return "".join(
        f'<option value="{key}" {"selected" if key == selected else ""}>{esc(label)}</option>'
        for key, label in ROLE_LABELS.items()
    )


def active_options(active: int) -> str:
    return (
        f'<option value="1" {"selected" if active else ""}>Actif</option>'
        f'<option value="0" {"selected" if not active else ""}>Inactif</option>'
    )


def admin_users_page(user: sqlite3.Row, params: dict[str, str]) -> str:
    with get_connection() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY active DESC, role, name").fetchall()
    message = params.get("message", "")
    error = params.get("erreur", "")
    feedback = (f'<p class="alert">{esc(error)}</p>' if error else "") + (
        f'<p class="success-message">{esc(message)}</p>' if message else ""
    )
    rows = "\n".join(
        f"""
        <article class="management-card">
          <form class="management-form" method="post" action="/admin/users/update">
            <input type="hidden" name="user_id" value="{row["id"]}">
            <div class="inline-fields">
              <label>Nom
                <input name="name" value="{esc(row["name"])}" required>
              </label>
              <label>Email
                <input name="email" type="email" value="{esc(row["email"])}" required>
              </label>
              <label>Rôle
                <select name="role">{role_options(row["role"])}</select>
              </label>
              <label>Statut
                <select name="active">{active_options(row["active"])}</select>
              </label>
            </div>
            <button class="primary-button small" type="submit">Modifier</button>
          </form>
          <form class="button-row" method="post" action="/admin/users/reset-password">
            <input type="hidden" name="user_id" value="{row["id"]}">
            <input name="password" placeholder="Nouveau mot de passe (défaut oilkam123)">
            <button class="ghost-button" type="submit">Réinitialiser le mot de passe</button>
          </form>
        </article>
        """
        for row in users
    )
    return layout(
        "Administration utilisateurs",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Administration</p>
            <h1>Gestion des utilisateurs</h1>
            <p>Créer, modifier, activer ou désactiver les comptes et leurs rôles.</p>
          </div>
          <div class="progress-tile"><span>{len(users)}</span><small>Comptes</small></div>
        </section>
        <section class="content-grid manager-grid">
          <div class="main-column">
            <div class="panel">
              <div class="section-head"><div><p class="section-kicker">Comptes</p><h2>Liste des utilisateurs</h2></div></div>
              {feedback}
              <div class="management-list">{rows}</div>
            </div>
          </div>
          <aside class="side-panel">
            <p class="section-kicker">Nouveau</p>
            <h2>Créer un utilisateur</h2>
            <form class="form-stack" method="post" action="/admin/users/create">
              <label>Nom
                <input name="name" required>
              </label>
              <label>Email
                <input name="email" type="email" required>
              </label>
              <label>Rôle
                <select name="role">{role_options("employe")}</select>
              </label>
              <label>Statut
                <select name="active">{active_options(1)}</select>
              </label>
              <label>Mot de passe
                <input name="password" placeholder="oilkam123 par défaut">
              </label>
              <button class="primary-button" type="submit">Créer le compte</button>
            </form>
          </aside>
        </section>
        """,
        user,
    )


def admin_products_page(user: sqlite3.Row, params: dict[str, str]) -> str:
    with get_connection() as conn:
        products = conn.execute("SELECT * FROM products ORDER BY active DESC, category, name").fetchall()
    error = params.get("erreur", "")
    feedback = f'<p class="alert">{esc(error)}</p>' if error else ""
    cards = "\n".join(
        f"""
        <article class="management-card">
          <form class="management-form" method="post" action="/admin/products/save">
            <input type="hidden" name="product_id" value="{row["id"]}">
            <div class="inline-fields">
              <label>Nom
                <input name="name" value="{esc(row["name"])}" required>
              </label>
              <label>Catégorie
                <input name="category" value="{esc(row["category"])}" required>
              </label>
              <label>Prix unitaire
                <input name="unit_price" type="number" step="0.01" min="0" value="{row["unit_price"]:.2f}" required>
              </label>
              <label>Unité
                <input name="unit" value="{esc(row["unit"])}" required>
              </label>
              <label>Statut
                <select name="active">{active_options(row["active"])}</select>
              </label>
            </div>
            <button class="primary-button small" type="submit">Enregistrer</button>
          </form>
        </article>
        """
        for row in products
    )
    return layout(
        "Administration produits",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Produits</p>
            <h1>Catalogue pertes</h1>
            <p>Les produits actifs alimentent le formulaire de déclaration des pertes.</p>
          </div>
          <div class="progress-tile"><span>{len(products)}</span><small>Produits</small></div>
        </section>
        <section class="content-grid manager-grid">
          <div class="main-column">
            <div class="panel">
              <div class="section-head"><div><p class="section-kicker">Catalogue</p><h2>Produits enregistrés</h2></div></div>
              {feedback}
              <div class="management-list">{cards}</div>
            </div>
          </div>
          <aside class="side-panel">
            <p class="section-kicker">Nouveau</p>
            <h2>Ajouter un produit</h2>
            <form class="form-stack" method="post" action="/admin/products/save">
              <label>Nom
                <input name="name" required>
              </label>
              <label>Catégorie
                <input name="category" required>
              </label>
              <label>Prix unitaire
                <input name="unit_price" type="number" min="0" step="0.01" required>
              </label>
              <label>Unité
                <input name="unit" placeholder="pièce, bouteille, bidon..." required>
              </label>
              <label>Statut
                <select name="active">{active_options(1)}</select>
              </label>
              <button class="primary-button" type="submit">Ajouter</button>
            </form>
          </aside>
        </section>
        """,
        user,
    )


def admin_training_page(user: sqlite3.Row, params: dict[str, str]) -> str:
    with get_connection() as conn:
        modules = conn.execute(
            """
            SELECT m.*, q.question, q.option_a, q.option_b, q.option_c, q.correct_option
            FROM training_modules m
            LEFT JOIN training_quizzes q ON q.module_id = m.id
            ORDER BY m.active DESC, m.sort_order, m.title
            """
        ).fetchall()
    error = params.get("erreur", "")
    feedback = f'<p class="alert">{esc(error)}</p>' if error else ""
    cards = "\n".join(training_admin_card(row) for row in modules)
    return layout(
        "Administration formations",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Formations</p>
            <h1>Modules et quiz</h1>
            <p>Créer ou modifier les contenus de formation et leur question de validation.</p>
          </div>
          <div class="progress-tile"><span>{len(modules)}</span><small>Modules</small></div>
        </section>
        <section class="content-grid manager-grid">
          <div class="main-column">
            <div class="panel">
              <div class="section-head"><div><p class="section-kicker">Modules</p><h2>Formations existantes</h2></div></div>
              {feedback}
              <div class="management-list">{cards}</div>
            </div>
          </div>
          <aside class="side-panel">
            <p class="section-kicker">Nouveau</p>
            <h2>Créer un module</h2>
            {training_module_form()}
          </aside>
        </section>
        """,
        user,
    )


def training_admin_card(row: sqlite3.Row) -> str:
    return f"""
    <article class="management-card">
      {training_module_form(row)}
    </article>
    """


def correct_option_options(selected: str = "A") -> str:
    return "".join(
        f'<option value="{option}" {"selected" if selected == option else ""}>{option}</option>'
        for option in ["A", "B", "C"]
    )


def training_module_form(row: sqlite3.Row | None = None) -> str:
    module_id = row["id"] if row else ""
    return f"""
    <form class="management-form" method="post" action="/admin/training/save">
      <input type="hidden" name="module_id" value="{module_id}">
      <label>Titre
        <input name="title" value="{esc(row["title"]) if row else ""}" required>
      </label>
      <label>Description
        <textarea name="description" rows="2">{esc(row["description"]) if row else ""}</textarea>
      </label>
      <label>Contenu texte
        <textarea name="content" rows="4">{esc(row["content"]) if row else ""}</textarea>
      </label>
      <div class="inline-fields">
        <label>Ordre
          <input name="sort_order" type="number" value="{row["sort_order"] if row else 0}">
        </label>
        <label>Statut
          <select name="active">{active_options(row["active"] if row else 1)}</select>
        </label>
        <label>Bonne réponse
          <select name="correct_option">{correct_option_options(row["correct_option"] if row and row["correct_option"] else "A")}</select>
        </label>
      </div>
      <label>Question quiz
        <input name="question" value="{esc(row["question"]) if row and row["question"] else ""}" required>
      </label>
      <div class="inline-fields">
        <label>Réponse A
          <input name="option_a" value="{esc(row["option_a"]) if row and row["option_a"] else ""}" required>
        </label>
        <label>Réponse B
          <input name="option_b" value="{esc(row["option_b"]) if row and row["option_b"] else ""}" required>
        </label>
        <label>Réponse C
          <input name="option_c" value="{esc(row["option_c"]) if row and row["option_c"] else ""}" required>
        </label>
      </div>
      <button class="primary-button small" type="submit">Enregistrer</button>
    </form>
    """


def training_page_v3(user: sqlite3.Row, params: dict[str, str]) -> str:
    message = params.get("message", "")
    error = params.get("erreur", "")
    with get_connection() as conn:
        modules = conn.execute(
            """
            SELECT m.*, q.question, q.option_a, q.option_b, q.option_c,
                   COALESCE(tp.status, 'Non commencé') AS status,
                   COALESCE(tp.progress_percent, 0) AS progress_percent,
                   tp.score, tp.validated_at
            FROM training_modules m
            LEFT JOIN training_quizzes q ON q.module_id = m.id AND q.active = 1
            LEFT JOIN training_progress tp ON tp.module_id = m.id AND tp.user_id = ?
            WHERE m.active = 1
            ORDER BY m.sort_order, m.id
            """,
            (user["id"],),
        ).fetchall()
        team_rows = conn.execute(
            """
            SELECT u.name AS user_name, m.title, COALESCE(tp.status, 'Non commencé') AS status,
                   COALESCE(tp.progress_percent, 0) AS progress_percent, tp.score
            FROM users u
            CROSS JOIN training_modules m
            LEFT JOIN training_progress tp ON tp.user_id = u.id AND tp.module_id = m.id
            WHERE u.role = 'employe' AND u.active = 1 AND m.active = 1
            ORDER BY u.name, m.sort_order
            """
        ).fetchall()

    feedback = (f'<p class="alert">{esc(error)}</p>' if error else "") + (
        f'<p class="success-message">{esc(message)}</p>' if message else ""
    )
    module_cards = "\n".join(training_module_card(row) for row in modules)
    team_table = ""
    if user["role"] in {"manager", "admin"}:
        team_table_rows = "\n".join(
            f"""
            <tr>
              <td>{esc(row["user_name"])}</td>
              <td><strong>{esc(row["title"])}</strong></td>
              <td><span class="status-pill pending">{esc(row["status"])}</span></td>
              <td>{row["progress_percent"]}%</td>
              <td>{'' if row["score"] is None else str(row["score"]) + '%'}</td>
            </tr>
            """
            for row in team_rows
        )
        team_table = f"""
        <div class="panel">
          <div class="section-head"><div><p class="section-kicker">Équipe</p><h2>Progression employés</h2></div></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Employé</th><th>Module</th><th>Statut</th><th>Progression</th><th>Score</th></tr></thead>
              <tbody>{team_table_rows}</tbody>
            </table>
          </div>
        </div>
        """

    return layout(
        "Formations",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Module formation</p>
            <h1>Formations internes</h1>
            <p>Contenus, quiz de validation, score et attestation imprimable.</p>
          </div>
          <div class="progress-tile"><span>{len(modules)}</span><small>Modules actifs</small></div>
        </section>
        <section class="content-grid">
          <div class="main-column">
            <div class="panel">
              <div class="section-head"><div><p class="section-kicker">Mon parcours</p><h2>Modules disponibles</h2></div></div>
              {feedback}
              <div class="module-list">{module_cards}</div>
            </div>
            {team_table}
          </div>
          <aside class="side-panel">
            <p class="section-kicker">Validation</p>
            <h2>Règle actuelle</h2>
            <div class="setting-row"><span>Score minimum</span><strong>{TRAINING_PASSING_SCORE}%</strong></div>
            <div class="setting-row"><span>Attestation</span><strong>Imprimable</strong></div>
            <div class="setting-row"><span>Suivi</span><strong>Automatique</strong></div>
          </aside>
        </section>
        """,
        user,
    )


def training_module_card(row: sqlite3.Row) -> str:
    certificate_link = ""
    if row["status"] == "Terminé":
        certificate_link = f'<a class="ghost-link" href="/training/certificate?module_id={row["id"]}">Attestation</a>'
    return f"""
    <article class="module-card">
      <div>
        <p class="section-kicker">{esc(row["status"])}</p>
        <h3>{esc(row["title"])}</h3>
        <p>{esc(row["description"])}</p>
        <div class="training-content">{esc(row["content"])}</div>
      </div>
      <span class="meter"><span style="width:{row["progress_percent"]}%"></span></span>
      <small>{row["progress_percent"]}% de progression {'' if row["score"] is None else '- score ' + str(row["score"]) + '%'}</small>
      <form class="quiz-form" method="post" action="/training/quiz">
        <input type="hidden" name="module_id" value="{row["id"]}">
        <strong>{esc(row["question"] or "Question indisponible")}</strong>
        <label><input type="radio" name="answer" value="A" required> {esc(row["option_a"] or "")}</label>
        <label><input type="radio" name="answer" value="B"> {esc(row["option_b"] or "")}</label>
        <label><input type="radio" name="answer" value="C"> {esc(row["option_c"] or "")}</label>
        <div class="button-row">
          <button class="primary-button small" type="submit">Valider le quiz</button>
          {certificate_link}
        </div>
      </form>
    </article>
    """


def certificate_page(user: sqlite3.Row, params: dict[str, str]) -> str:
    module_id = params.get("module_id", "")
    employee_id = params.get("employee_id", str(user["id"])) if user["role"] in {"manager", "admin"} else str(user["id"])
    with get_connection() as conn:
        cert = conn.execute(
            """
            SELECT c.*, u.name AS user_name, m.title AS module_title
            FROM training_certificates c
            JOIN users u ON u.id = c.user_id
            JOIN training_modules m ON m.id = c.module_id
            WHERE c.module_id = ? AND c.user_id = ?
            """,
            (module_id, employee_id),
        ).fetchone()
    if cert is None:
        body = '<section class="panel page-panel"><h1>Attestation indisponible</h1><p>La formation n’est pas encore validée.</p></section>'
        return layout("Attestation", body, user)
    return layout(
        "Attestation",
        f"""
        <section class="certificate-page">
          <div class="certificate">
            <p class="eyebrow">Oil Kam</p>
            <h1>Attestation de formation</h1>
            <p>Nous attestons que <strong>{esc(cert["user_name"])}</strong> a validé la formation :</p>
            <h2>{esc(cert["module_title"])}</h2>
            <div class="certificate-grid">
              <div><span>Date de validation</span><strong>{esc(cert["validated_at"][:10])}</strong></div>
              <div><span>Score obtenu</span><strong>{cert["score"]}%</strong></div>
            </div>
            <p class="certificate-note">Document généré par l’application Oil Kam. Impression ou enregistrement PDF via le navigateur.</p>
            <button class="primary-button print-button" type="button" onclick="window.print()">Imprimer / Enregistrer en PDF</button>
          </div>
        </section>
        """,
        user,
    )


def reports_page(user: sqlite3.Row, params: dict[str, str]) -> str:
    start = params.get("start", date.today().replace(day=1).isoformat())
    end = params.get("end", today_iso())
    with get_connection() as conn:
        loss_rows = conn.execute(
            """
            SELECT l.loss_date, p.name AS product_name, l.motive, l.quantity, l.value, u.name AS user_name
            FROM losses l
            JOIN products p ON p.id = l.product_id
            JOIN users u ON u.id = l.user_id
            WHERE l.loss_date BETWEEN ? AND ?
            ORDER BY l.loss_date DESC
            """,
            (start, end),
        ).fetchall()
        task_rows = conn.execute(
            """
            SELECT c.completion_date, t.title, u.name AS user_name, c.completed_at
            FROM task_completions c
            JOIN tasks t ON t.id = c.task_id
            JOIN users u ON u.id = c.user_id
            WHERE c.completion_date BETWEEN ? AND ?
            ORDER BY c.completed_at DESC
            """,
            (start, end),
        ).fetchall()
        training_rows = conn.execute(
            """
            SELECT u.name AS user_name, m.title, COALESCE(tp.status, 'Non commencé') AS status,
                   COALESCE(tp.progress_percent, 0) AS progress_percent, tp.score
            FROM users u
            CROSS JOIN training_modules m
            LEFT JOIN training_progress tp ON tp.user_id = u.id AND tp.module_id = m.id
            WHERE u.role = 'employe' AND u.active = 1 AND m.active = 1
            ORDER BY u.name, m.sort_order
            """
        ).fetchall()
    loss_total = sum(float(row["value"]) for row in loss_rows)
    return layout(
        "Rapports",
        f"""
        <section class="hero-band dashboard-hero">
          <div class="hero-copy">
            <p class="eyebrow">Rapports</p>
            <h1>Synthèse imprimable</h1>
            <p>Pertes, tâches complétées et progression formations sur une période.</p>
          </div>
          <div class="progress-tile"><span>{loss_total:.0f}</span><small>MAD pertes</small></div>
        </section>
        <section class="panel page-panel report-actions">
          <form class="filters-bar" method="get" action="/reports">
            <label>Début <input type="date" name="start" value="{esc(start)}"></label>
            <label>Fin <input type="date" name="end" value="{esc(end)}"></label>
            <button class="primary-button" type="submit">Actualiser</button>
            <button class="ghost-button" type="button" onclick="window.print()">Imprimer</button>
            <a class="ghost-link" href="/losses/export">CSV pertes</a>
          </form>
        </section>
        {report_table("Rapport des pertes", ["Date", "Produit", "Motif", "Qté", "Valeur", "Déclaré par"], [[r["loss_date"], r["product_name"], r["motive"], r["quantity"], f'{r["value"]:.2f} MAD', r["user_name"]] for r in loss_rows])}
        {report_table("Rapport des tâches complétées", ["Date", "Tâche", "Employé", "Heure"], [[r["completion_date"], r["title"], r["user_name"], r["completed_at"][11:16]] for r in task_rows])}
        {report_table("Rapport progression formations", ["Employé", "Formation", "Statut", "Progression", "Score"], [[r["user_name"], r["title"], r["status"], str(r["progress_percent"]) + "%", "" if r["score"] is None else str(r["score"]) + "%"] for r in training_rows])}
        """,
        user,
    )


def report_table(title: str, headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    if not body:
        body = f'<tr><td colspan="{len(headers)}">Aucune donnée pour cette période.</td></tr>'
    return f"""
    <section class="panel page-panel report-section">
      <div class="section-head"><div><p class="section-kicker">Rapport</p><h2>{esc(title)}</h2></div></div>
      <div class="table-wrap">
        <table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
      </div>
    </section>
    """


def run(host: str, port: int) -> None:
    init_db(DB_PATH)
    server = ThreadingHTTPServer((host, port), OilKamHandler)
    print(f"Oil Kam prêt : http://{host}:{port}")
    print("Comptes : employe@oilkam.demo, manager@oilkam.demo, admin@oilkam.demo")
    print("Mot de passe : oilkam123")
    server.serve_forever()


if __name__ == "__main__":
    default_host = os.environ.get("HOST")
    if not default_host:
        default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    default_port = int(os.environ.get("PORT", "8000"))

    parser = argparse.ArgumentParser(description="Serveur de démonstration Oil Kam")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", default=default_port, type=int)
    args = parser.parse_args()
    run(args.host, args.port)
