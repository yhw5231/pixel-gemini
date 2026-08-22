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
RUN_STATUS_AWAITING_2FA = "awaiting_2fa"
RUN_STATUS_DONE = "done"
RUN_STATUS_ERROR = "error"

# How long a run may live before the watchdog force-quits its browser, and
# how long the automation waits for a manually entered 2FA code.
RUN_TIMEOUT_SEC = int(os.environ.get("RUN_TIMEOUT_SEC", "600"))
TWO_FA_WAIT_SEC = int(os.environ.get("TWO_FA_WAIT_SEC", "600"))

try:
    import pyotp
except ImportError:  # pragma: no cover - TOTP support is optional
    pyotp = None

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
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email        TEXT UNIQUE NOT NULL,
            password     TEXT NOT NULL,
            note         TEXT DEFAULT '',
            totp_secret  TEXT DEFAULT '',
            created_at   TEXT NOT NULL
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
    # Migration for databases created before TOTP support existed.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
    if "totp_secret" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN totp_secret TEXT DEFAULT ''")
        logger.info("Migrated accounts table: added totp_secret column")

    # Runs left over from a previous process can never finish – close them.
    stale = conn.execute(
        "UPDATE runs SET status = ?, error = ?, finished_at = ? "
        "WHERE status IN (?, ?, ?)",
        (RUN_STATUS_ERROR, "服务重启，任务中断 (interrupted by restart).",
         _now(), RUN_STATUS_QUEUED, RUN_STATUS_RUNNING, RUN_STATUS_AWAITING_2FA),
    ).rowcount
    if stale:
        logger.info("Marked %d stale run(s) as error after restart", stale)

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
        "SELECT id, email, password, note, totp_secret, created_at "
        "FROM accounts ORDER BY id DESC"
    ).fetchall()


def add_account(email: str, password: str, note: str = "",
                totp_secret: str = "") -> None:
    get_db().execute(
        "INSERT INTO accounts (email, password, note, totp_secret, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (email, password, note, _normalize_totp(totp_secret), _now()),
    )
    get_db().commit()


def _normalize_totp(secret: str) -> str:
    """Strip spaces/upper-case a base32 TOTP secret; '' stays ''."""
    return "".join((secret or "").split()).upper()


def _do_add_account(email: str, password: str, note: str,
                    totp_secret: str, lang: str) -> None:
    try:
        add_account(email, password, note, totp_secret)
        flash(f"已添加账户 {email}。" if lang == "zh" else f"Account {email} added.", "ok")
    except sqlite3.IntegrityError:
        flash(f"账户 {email} 已存在。" if lang == "zh" else f"Account {email} already exists.", "error")


def delete_account(account_id: int) -> None:
    get_db().execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    get_db().commit()


def get_account(account_id: int):
    return get_db().execute(
        "SELECT id, email, password, totp_secret FROM accounts WHERE id = ?",
        (account_id,),
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


def update_run_status(run_id: int, status: str) -> None:
    get_db().execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
    get_db().commit()


def finish_run(run_id: int, status: str, offer_link=None, error=None,
               device=None) -> None:
    get_db().execute(
        "UPDATE runs SET status = ?, offer_link = ?, error = ?, device = ?, "
        "finished_at = ? WHERE id = ? AND status NOT IN (?, ?)",
        (status, offer_link, error, device, _now(), run_id,
         RUN_STATUS_DONE, RUN_STATUS_ERROR),
    )
    get_db().commit()


# ── Background automation runner ──────────────────────────────────────────────

def run_automation(account_id: int, account_email: str, password: str,
                   run_id: int, totp_secret: str = "") -> None:
    """Execute the Gemini offer check in a background thread."""
    # Database access requires an application context (threads have none).
    with app.app_context():
        _run_automation(account_id, account_email, password, run_id, totp_secret)


def _run_automation(account_id: int, account_email: str, password: str,
                    run_id: int, totp_secret: str = "") -> None:
    """Background body – runs inside an application context."""
    def set_run(message: str, status: str = None, persist: bool = False):
        with RUNS_LOCK:
            entry = RUNS[run_id]
            entry["status"] = status or entry.get("status", RUN_STATUS_RUNNING)
            entry["message"] = message
        if persist and status:
            update_run_status(run_id, status)

    with RUNS_LOCK:
        RUNS[run_id] = {
            "status": RUN_STATUS_RUNNING,
            "message": "Starting…",
            "link": None,
            "started_at": time.time(),
            "driver": None,
            "code_event": threading.Event(),
            "code": None,
        }
    update_run_status(run_id, RUN_STATUS_RUNNING)

    def otp_provider():
        """Return a 2FA code for the automation, or None to abort."""
        secret = _normalize_totp(totp_secret)
        if secret and pyotp is not None:
            try:
                code = pyotp.TOTP(secret).now()
                logger.info("Supplied TOTP code for run #%s from stored secret", run_id)
                return code
            except Exception:
                logger.exception("Invalid TOTP secret for run #%s – "
                                 "falling back to manual entry", run_id)
        # Manual path: ask the user through the web UI and wait.
        set_run("等待两步验证码…请在运行页面输入 Google 验证码。"
                if getattr(g, "lang", "zh") != "en" else
                "Two-step verification required – enter the code on the run page.",
                RUN_STATUS_AWAITING_2FA, persist=True)
        with RUNS_LOCK:
            entry = RUNS[run_id]
            event = entry["code_event"]
        if not event.wait(TWO_FA_WAIT_SEC):
            return None
        with RUNS_LOCK:
            code = RUNS[run_id].get("code")
        if code:
            set_run("Verification code received – continuing login…",
                    RUN_STATUS_RUNNING, persist=True)
        return code

    def driver_callback(driver):
        with RUNS_LOCK:
            if run_id in RUNS:
                RUNS[run_id]["driver"] = driver

    try:
        device = create_device_profile()
        set_run("Launching Pixel 10 Pro simulator and logging into Google…")
        offer_link = check_gemini_offer(
            account_email, password, device,
            otp_provider=otp_provider,
            driver_callback=driver_callback,
        )
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
    finally:
        with RUNS_LOCK:
            if run_id in RUNS:
                RUNS[run_id]["driver"] = None
                RUNS[run_id]["code_event"].set()  # release any waiter


def submit_2fa_code(run_id: int, code: str) -> bool:
    """Hand a manually entered 2FA code to a waiting run. False if not waiting."""
    with RUNS_LOCK:
        entry = RUNS.get(run_id)
        if not entry or entry.get("status") != RUN_STATUS_AWAITING_2FA:
            return False
        entry["code"] = code
        entry["code_event"].set()
        entry["status"] = RUN_STATUS_RUNNING
        entry["message"] = "Verification code received – continuing login…"
    update_run_status(run_id, RUN_STATUS_RUNNING)
    return True


WATCHDOG_INTERVAL_SEC = 20


def _watchdog_loop() -> None:
    """Force-quit browser sessions whose run exceeded RUN_TIMEOUT_SEC."""
    while True:
        time.sleep(WATCHDOG_INTERVAL_SEC)
        try:
            with RUNS_LOCK:
                snapshot = [
                    (rid, dict(e)) for rid, e in RUNS.items()
                    if e.get("status") in (RUN_STATUS_RUNNING, RUN_STATUS_QUEUED,
                                           RUN_STATUS_AWAITING_2FA)
                    and time.time() - e.get("started_at", time.time())
                    > RUN_TIMEOUT_SEC
                ]
            for run_id, entry in snapshot:
                logger.warning("Run #%s exceeded %ss – terminating browser",
                               run_id, RUN_TIMEOUT_SEC)
                with RUNS_LOCK:
                    if run_id in RUNS:
                        RUNS[run_id]["status"] = RUN_STATUS_ERROR
                        RUNS[run_id]["message"] = _truncate(
                            f"Run timed out after {RUN_TIMEOUT_SEC}s")
                        RUNS[run_id]["code_event"].set()
                driver = entry.get("driver")
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                # If the automation thread is stuck, at least the DB reflects
                # reality; the thread's own finish_run is a no-op then.
                with app.app_context():
                    row = get_db().execute(
                        "SELECT status FROM runs WHERE id = ?", (run_id,)
                    ).fetchone()
                    if row and row["status"] in (
                        RUN_STATUS_QUEUED, RUN_STATUS_RUNNING, RUN_STATUS_AWAITING_2FA
                    ):
                        finish_run(run_id, RUN_STATUS_ERROR,
                                   error=f"Run timed out after {RUN_TIMEOUT_SEC}s")
        except Exception:
            logger.exception("Run watchdog iteration failed")


def start_watchdog() -> None:
    """Start the single watchdog daemon thread (idempotent)."""
    with RUNS_LOCK:
        existing = RUNS.get("__watchdog__")
    if existing:
        return
    t = threading.Thread(target=_watchdog_loop, daemon=True, name="run-watchdog")
    with RUNS_LOCK:
        RUNS.setdefault("__watchdog__", True)
    t.start()


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
            totp_secret = _normalize_totp(request.form.get("totp_secret") or "")
            if not email or not password:
                flash("邮箱和密码不能为空。" if g.lang == "zh" else "Email and password are required.", "error")
            elif totp_secret and pyotp is not None:
                try:
                    pyotp.TOTP(totp_secret).now()
                except Exception:
                    flash("TOTP 密钥格式无效（应为 Base32）。" if g.lang == "zh" else "Invalid TOTP secret (Base32 expected).", "error")
                    return redirect(url_for("accounts"))
                else:
                    _do_add_account(email, password, note, totp_secret, g.lang)
            else:
                _do_add_account(email, password, note, totp_secret, g.lang)
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
        # Register the run before starting the worker so the run page/API can
        # immediately observe it and the watchdog can detect startup failures.
        with RUNS_LOCK:
            RUNS[run_id] = {
                "status": RUN_STATUS_QUEUED,
                "message": "任务已排队，正在启动自动化…" if g.lang == "zh" else "Queued; starting automation…",
                "link": None,
                "started_at": time.time(),
                "driver": None,
                "code_event": threading.Event(),
                "code": None,
            }
        thread = threading.Thread(
            target=run_automation,
            args=(account["id"], account["email"], account["password"], run_id,
                  account["totp_secret"] or ""),
            daemon=True,
            name=f"run-{run_id}",
        )
        try:
            thread.start()
        except Exception as exc:
            logger.exception("Unable to start automation thread for run #%s", run_id)
            with RUNS_LOCK:
                RUNS[run_id]["status"] = RUN_STATUS_ERROR
                RUNS[run_id]["message"] = _truncate(f"Unable to start automation: {exc}")
            finish_run(run_id, RUN_STATUS_ERROR,
                       error=f"Unable to start automation: {exc}")
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
            live = dict(RUNS[run_id]) if run_id in RUNS else None
        live_status = (live or {}).get("status")
        # The in-memory state is authoritative while the run is alive.
        db_status = run["status"]
        status = live_status if (
            live_status and db_status not in (RUN_STATUS_DONE, RUN_STATUS_ERROR)
        ) else db_status
        return jsonify({
            "id": run["id"],
            "status": status,
            "message": (live or {}).get("message"),
            "offer_link": run["offer_link"],
            "error": run["error"],
            "finished_at": run["finished_at"],
            "awaiting_2fa": status == RUN_STATUS_AWAITING_2FA,
        })

    @app.post("/run/<int:run_id>/code")
    @login_required
    def submit_code(run_id: int):
        code = (request.form.get("code") or "").strip()
        if not code and request.is_json:
            code = str((request.get_json(silent=True) or {}).get("code") or "")
        code = "".join(code.split())
        if not code:
            return jsonify({"error": "empty code"}), 400
        if not submit_2fa_code(run_id, code):
            return jsonify({"error": "run is not waiting for a code"}), 409
        return jsonify({"ok": True})

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
init_db()
start_watchdog()

if __name__ == "__main__":
    init_db()
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8910"))
    app.run(host=host, port=port, debug=False)
