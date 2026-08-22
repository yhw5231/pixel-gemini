"""
Web interface for the Pixel 10 Pro Google One Gemini bot.

A Flask application that provides a browser UI for managing Gmail
accounts, running the Gemini offer automation, and viewing results.

Default administrator account: ``admin`` / ``admin`` (override with the
``ADMIN_USERNAME`` / ``ADMIN_PASSWORD`` environment variables).
"""

import logging
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import config
from device_simulator import create_device_profile
from google_automation import GoogleAutomationError, check_gemini_offer

# ── Paths & constants ─────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "pixel_gemini.db"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_DONE = "done"
RUN_STATUS_ERROR = "error"

logger = logging.getLogger(__name__)

# In-process registry: run_id -> {"status": ..., "message": ..., "link": ...}
RUNS: dict = {}
RUNS_LOCK = threading.Lock()


# ── Log ring buffer (for the web log viewer) ─────────────────────────────────

class RingBufferHandler(logging.Handler):
    """Keep the last N log records in memory for the /logs page."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.buffer: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                self.buffer.append(msg)
        except Exception:
            pass

    def snapshot(self) -> list:
        with self._lock:
            return list(self.buffer)


LOG_BUFFER = RingBufferHandler()
LOG_BUFFER.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
LOG_BUFFER.setLevel(logging.INFO)
logging.getLogger().addHandler(LOG_BUFFER)


# ── Database helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_db() -> sqlite3.Connection:
    """Return a per-request SQLite connection (row factory enabled)."""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create tables and seed the default admin account."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            note       TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id     INTEGER,
            account_email  TEXT NOT NULL,
            status         TEXT NOT NULL,
            offer_link     TEXT,
            error          TEXT,
            device         TEXT,
            created_at     TEXT NOT NULL,
            finished_at    TEXT
        );
        """
    )
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), _now()),
        )
        logger.info("Seeded default admin user '%s'", ADMIN_USERNAME)
    else:
        # Allow rotating the admin password through the environment variable.
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (generate_password_hash(ADMIN_PASSWORD), ADMIN_USERNAME),
        )
    conn.commit()
    conn.close()


def list_accounts() -> list:
    return get_db().execute(
        "SELECT id, email, password, note, created_at FROM accounts ORDER BY id DESC"
    ).fetchall()


def add_account(email: str, password: str, note: str = "") -> None:
    get_db().execute(
        "INSERT INTO accounts (email, password, note, created_at) VALUES (?, ?, ?, ?)",
        (email, password, note, _now()),
    )
    get_db().commit()


def delete_account(account_id: int) -> None:
    get_db().execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    get_db().commit()


def get_account(account_id: int):
    return get_db().execute(
        "SELECT id, email, password FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()


def list_runs(limit: int = 50) -> list:
    return get_db().execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def insert_run(account_id, account_email: str, status: str) -> int:
    cur = get_db().execute(
        "INSERT INTO runs (account_id, account_email, status, created_at) "
        "VALUES (?, ?, ?, ?)",
        (account_id, account_email, status, _now()),
    )
    get_db().commit()
    return cur.lastrowid


def finish_run(run_id: int, status: str, offer_link=None, error=None,
               device=None) -> None:
    get_db().execute(
        "UPDATE runs SET status = ?, offer_link = ?, error = ?, device = ?, "
        "finished_at = ? WHERE id = ?",
        (status, offer_link, error, device, _now(), run_id),
    )
    get_db().commit()


# ── Background automation runner ──────────────────────────────────────────────

def run_automation(account_id: int, account_email: str, password: str,
                   run_id: int) -> None:
    """Execute the Gemini offer check in a background thread."""
    # Database access requires an application context (threads have none).
    with app.app_context():
        _run_automation(account_id, account_email, password, run_id)


def _run_automation(account_id: int, account_email: str, password: str,
                    run_id: int) -> None:
    """Background body – runs inside an application context."""
    def set_run(message: str, status: str = None):
        with RUNS_LOCK:
            RUNS[run_id] = {
                "status": status or RUNS[run_id].get("status", RUN_STATUS_RUNNING),
                "message": message,
                "link": RUNS[run_id].get("link"),
            }

    with RUNS_LOCK:
        RUNS[run_id] = {"status": RUN_STATUS_RUNNING, "message": "Starting…", "link": None}
    try:
        device = create_device_profile()
        set_run("Launching Pixel 10 Pro simulator and logging into Google…")
        offer_link = check_gemini_offer(account_email, password, device)
        if offer_link:
            set_run(f"Offer found: {offer_link}")
            with RUNS_LOCK:
                RUNS[run_id]["link"] = offer_link
                RUNS[run_id]["status"] = RUN_STATUS_DONE
            finish_run(run_id, RUN_STATUS_DONE, offer_link=offer_link,
                       device=device.summary())
        else:
            set_run("No active Gemini Pro offer detected.")
            with RUNS_LOCK:
                RUNS[run_id]["status"] = RUN_STATUS_DONE
            finish_run(run_id, RUN_STATUS_DONE, device=device.summary())
    except GoogleAutomationError as exc:
        logger.warning("Automation error for %s: %s", account_email, exc)
        message = _truncate(str(exc))
        with RUNS_LOCK:
            RUNS[run_id]["status"] = RUN_STATUS_ERROR
            RUNS[run_id]["message"] = message
        finish_run(run_id, RUN_STATUS_ERROR, error=str(exc))
    except Exception as exc:  # noqa: BLE001 – surface anything to the UI
        logger.exception("Unexpected error during automation for %s", account_email)
        message = _truncate(f"Unexpected error: {exc}")
        with RUNS_LOCK:
            RUNS[run_id]["status"] = RUN_STATUS_ERROR
            RUNS[run_id]["message"] = message
        finish_run(run_id, RUN_STATUS_ERROR, error=str(exc))


def _truncate(text: str, limit: int = 300) -> str:
    """Shorten long error messages for compact UI display."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(view):
    """Require an authenticated admin session, else redirect to /login."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not flask_session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def current_user() -> str:
    return flask_session.get("user", "")


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

    app.teardown_appcontext(close_db)

    # ── Language selection ───────────────────────────────────────────────────

    @app.before_request
    def select_language():
        requested = request.args.get("lang")
        if requested in {"zh", "en"}:
            flask_session["lang"] = requested
        g.lang = flask_session.get("lang", "zh")

    @app.context_processor
    def inject_language():
        return {"lang": getattr(g, "lang", "zh")}

    @app.get("/language/<lang>")
    def set_language(lang: str):
        if lang in {"zh", "en"}:
            flask_session["lang"] = lang

        target = url_for("dashboard")
        if request.referrer:
            candidate = urlparse(urljoin(request.host_url, request.referrer))
            host = urlparse(request.host_url)
            if candidate.scheme in {"http", "https"} and candidate.netloc == host.netloc:
                target = candidate.path
                if candidate.query:
                    target += "?" + candidate.query
        return redirect(target)

    # ── Pages ────────────────────────────────────────────────────────────────

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if flask_session.get("user"):
            return redirect(url_for("dashboard"))
        error = None
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            row = get_db().execute(
                "SELECT password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row and check_password_hash(row["password_hash"], password):
                flask_session["user"] = username
                flask_session.permanent = True
                return redirect(request.args.get("next") or url_for("dashboard"))
            error = "用户名或密码错误。" if g.lang == "zh" else "Invalid username or password."
        return render_template("login.html", error=error)

    @app.get("/logout")
    def logout():
        flask_session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        stats = get_db().execute(
            "SELECT status, COUNT(*) AS n FROM runs GROUP BY status"
        ).fetchall()
        recent = list_runs(limit=10)
        return render_template(
            "dashboard.html",
            user=current_user(),
            stats={row["status"]: row["n"] for row in stats},
            recent=recent,
            run_status=RUN_STATUS_DONE,
        )

    # ── Accounts ─────────────────────────────────────────────────────────────

    @app.route("/accounts", methods=["GET", "POST"])
    @login_required
    def accounts():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""
            note = (request.form.get("note") or "").strip()
            if not email or not password:
                flash("邮箱和密码不能为空。" if g.lang == "zh" else "Email and password are required.", "error")
            else:
                try:
                    add_account(email, password, note)
                    flash(f"已添加账户 {email}。" if g.lang == "zh" else f"Account {email} added.", "ok")
                except sqlite3.IntegrityError:
                    flash(f"账户 {email} 已存在。" if g.lang == "zh" else f"Account {email} already exists.", "error")
            return redirect(url_for("accounts"))
        return render_template(
            "accounts.html",
            user=current_user(),
            accounts=list_accounts(),
        )

    @app.post("/accounts/<int:account_id>/delete")
    @login_required
    def account_delete(account_id: int):
        delete_account(account_id)
        flash("账户已删除。" if g.lang == "zh" else "Account deleted.", "ok")
        return redirect(url_for("accounts"))

    # ── Runs ─────────────────────────────────────────────────────────────────

    @app.post("/run")
    @login_required
    def start_run():
        account_id = request.form.get("account_id", type=int)
        account = get_account(account_id) if account_id else None
        if not account:
            flash("请选择有效账户。" if g.lang == "zh" else "Please choose a valid account.", "error")
            return redirect(url_for("accounts"))
        run_id = insert_run(account["id"], account["email"], RUN_STATUS_QUEUED)
        thread = threading.Thread(
            target=run_automation,
            args=(account["id"], account["email"], account["password"], run_id),
            daemon=True,
            name=f"run-{run_id}",
        )
        thread.start()
        flash(f"已为 {account['email']} 启动自动化。" if g.lang == "zh" else f"Automation started for {account['email']}.", "ok")
        return redirect(url_for("run_detail", run_id=run_id))

    @app.get("/runs")
    @login_required
    def runs():
        return render_template(
            "runs.html",
            user=current_user(),
            runs=list_runs(limit=100),
        )

    @app.get("/run/<int:run_id>")
    @login_required
    def run_detail(run_id: int):
        run = get_db().execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            flash("未找到该运行记录。" if g.lang == "zh" else "Run not found.", "error")
            return redirect(url_for("runs"))
        with RUNS_LOCK:
            live = RUNS.get(run_id)
        return render_template(
            "run.html",
            user=current_user(),
            run=run,
            live=live,
        )

    @app.get("/api/run/<int:run_id>")
    @login_required
    def api_run(run_id: int):
        run = get_db().execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            return jsonify({"error": "not found"}), 404
        with RUNS_LOCK:
            live = RUNS.get(run_id)
        return jsonify({
            "id": run["id"],
            "status": run["status"],
            "message": (live or {}).get("message"),
            "offer_link": run["offer_link"],
            "error": run["error"],
            "finished_at": run["finished_at"],
        })

    # ── Logs ─────────────────────────────────────────────────────────────────

    @app.get("/logs")
    @login_required
    def logs():
        return render_template(
            "logs.html",
            user=current_user(),
            lines=LOG_BUFFER.snapshot(),
        )

    return app


app = create_app()

if __name__ == "__main__":
    init_db()
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8910"))
    app.run(host=host, port=port, debug=False)
