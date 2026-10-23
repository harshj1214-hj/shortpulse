"""ShortPulse application factory."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

import click
from flask import Flask, g, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import ROOT, Config


def create_app(overrides: dict | None = None) -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__, instance_path=str(ROOT / "instance"))
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    Config.finalize(app.config)
    app.permanent_session_lifetime = timedelta(days=7)

    if app.config["TRUSTED_PROXIES"]:
        n = app.config["TRUSTED_PROXIES"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    from .clicklog import ClickLogger
    from .db import close_db, init_schema
    from .engine import Engine
    from .enrich import GeoLookup
    from .ml import Models
    from .redirect import CodeConverter

    init_schema(app.config["DATABASE"])
    engine = Engine()
    models = Models(app.config["MODEL_DIR"])
    geo = GeoLookup(app.config["GEOLITE2_PATH"])
    app.extensions["engine"] = engine
    app.extensions["models"] = models
    app.extensions["geo"] = geo
    app.extensions["clicklog"] = ClickLogger(db_path=app.config["DATABASE"], models=models, geo=geo,
                                             salt=app.config["IP_HASH_SALT"],
                                             maxsize=app.config["CLICK_QUEUE_SIZE"],
                                             store_full_ip=app.config["STORE_FULL_IP"],
                                             ip_retention_days=app.config["IP_RETENTION_DAYS"])

    app.url_map.converters["code"] = CodeConverter
    from . import admin, analytics, auth, engage, importer, landing, links_api, qrstudio, redirect, views
    for bp in (auth.bp, views.bp, links_api.bp, analytics.bp, admin.bp, importer.bp, engage.bp, qrstudio.bp,
               landing.bp):
        app.register_blueprint(bp)

    @app.get("/healthz")
    def healthz():
        clicklog = app.extensions["clicklog"]
        return jsonify(ok=True, engine=engine.version, models=models.status(),
                       click_queue=clicklog.q.qsize(), click_stats=clicklog.stats)

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.svg")

    app.register_blueprint(redirect.bp)  # catch-all /<code>; static routes still win in Werkzeug

    # Every top-level path segment of a real route becomes a reserved alias, so
    # nobody can create a short link that the router would never reach.
    for rule in app.url_map.iter_rules():
        first = rule.rule.strip("/").split("/")[0]
        if first and not first.startswith("<"):
            engine.reserve(first)

    from .security import check_csrf, csrf_token, is_admin, load_user

    @app.before_request
    def _before():
        load_user()
        check_csrf()

    app.teardown_appcontext(close_db)

    @app.context_processor
    def _inject():
        ga = app.config.get("GA_MEASUREMENT_ID", "")
        return {"current_user": g.get("user"), "csrf_token": csrf_token, "is_admin": is_admin,
                "ga_id": ga if re.fullmatch(r"G-[A-Z0-9]{4,12}", ga or "") else ""}

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    def _error(status: int, message: str):
        if request.path.startswith("/api/"):
            return jsonify(error=status, message=message), status
        return render_template("error.html", status=status, message=message), status

    app.register_error_handler(400, lambda e: _error(400, "The request was malformed."))
    app.register_error_handler(403, lambda e: _error(403, "You don't have access to this page."))
    app.register_error_handler(404, lambda e: _error(404, "This page doesn't exist."))
    app.register_error_handler(413, lambda e: _error(413, "That file is larger than 1 MB."))

    _load_aliases(app)
    _startup_data(app)
    _register_cli(app)
    return app


def _load_aliases(app: Flask) -> None:
    """Fill the C hash table with every existing code (O(n) once at boot)."""
    from .db import connect
    conn = connect(app.config["DATABASE"])
    try:
        for (code,) in conn.execute("SELECT code FROM links"):
            app.extensions["engine"].alias_add(code)
    finally:
        conn.close()


def _startup_data(app: Flask) -> None:
    from .seed import ensure_admin, seed_demo
    ensure_admin(app.config["DATABASE"], app.config["ADMIN_EMAIL"], app.config["ADMIN_PASSWORD"])
    if app.config["SEED_ON_START"]:
        creds = seed_demo(app.config["DATABASE"], app.extensions["engine"],
                          admin_email=app.config["ADMIN_EMAIL"], admin_password=app.config["ADMIN_PASSWORD"],
                          demo_password=app.config.get("DEMO_PASSWORD", ""))
        if creds:
            admin_pw = "(from ADMIN_PASSWORD)" if app.config["ADMIN_PASSWORD"] else creds["admin_password"]
            demo_pw = "(from DEMO_PASSWORD)" if app.config.get("DEMO_PASSWORD") else creds["user_password"]
            logging.getLogger("shortpulse.seed").warning(
                "Seeded demo data. Admin: %s / %s  Demo user: %s / %s",
                creds["admin_email"], admin_pw, creds["user_email"], demo_pw)


def _register_cli(app: Flask) -> None:
    @app.cli.command("seed")
    @click.option("--admin-email", default="")
    @click.option("--admin-password", default="")
    @click.option("--demo-password", default="")
    def seed_cmd(admin_email, admin_password, demo_password):
        """Create demo users, campaigns, links and synthetic click history."""
        from .seed import seed_demo
        creds = seed_demo(app.config["DATABASE"], app.extensions["engine"],
                          admin_email=admin_email or app.config["ADMIN_EMAIL"],
                          admin_password=admin_password or app.config["ADMIN_PASSWORD"],
                          demo_password=demo_password)
        if not creds:
            click.echo("Database already has links; nothing seeded.")
            return
        click.echo(f"Admin:     {creds['admin_email']} / {creds['admin_password']}")
        click.echo(f"Demo user: {creds['user_email']} / {creds['user_password']}")
        click.echo(f"Password-protected demo link /vip-preview: {creds['vip_link_password']}")

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.password_option()
    def create_admin(email, password):
        """Create (or promote) an admin account."""
        from .seed import ensure_admin
        ensure_admin(app.config["DATABASE"], email, password)
        click.echo(f"{email} is now an admin.")
