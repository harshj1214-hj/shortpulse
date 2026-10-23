from __future__ import annotations

from flask import (Blueprint, abort, current_app, g, jsonify, redirect, render_template,
                   send_from_directory, url_for)

from . import services as S
from .db import get_db
from .security import admin_required, login_required

bp = Blueprint("views", __name__)


@bp.get("/")
def index():
    if g.user:
        return redirect(url_for("views.dashboard"))
    return render_template("index.html")


@bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@bp.get("/links")
@login_required
def links():
    return render_template("links.html")


@bp.get("/links/new")
@login_required
def new_link():
    return render_template("wizard.html")


@bp.get("/links/<int:link_id>")
@login_required
def link_detail(link_id: int):
    if S.get_link_row(get_db(), link_id, g.user) is None:
        abort(404)
    return render_template("link_detail.html", link_id=link_id)


@bp.get("/links/<int:link_id>/studio")
@login_required
def studio(link_id: int):
    row = S.get_link_row(get_db(), link_id, g.user)
    if row is None:
        abort(404)
    return render_template("studio.html", link=S.link_to_dict(row, S.base_url()))


@bp.get("/links/<int:link_id>/poster")
@login_required
def poster(link_id: int):
    row = S.get_link_row(get_db(), link_id, g.user)
    if row is None:
        abort(404)
    return render_template("poster.html", link=S.link_to_dict(row, S.base_url()))


# ---- installable app (PWA) and Android APK support -------------------------
@bp.get("/manifest.webmanifest")
def manifest():
    icons = "/static/icons/"
    resp = jsonify({
        "id": "/dashboard",
        "name": "ShortPulse",
        "short_name": "ShortPulse",
        "description": "Short links with rules, analytics and safety",
        "start_url": "/dashboard?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#EAF1F9",
        "theme_color": "#13213A",
        "icons": [
            {"src": icons + "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icons + "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icons + "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "New link", "url": "/links/new", "icons": [{"src": icons + "icon-192.png", "sizes": "192x192"}]},
            {"name": "My links", "url": "/links", "icons": [{"src": icons + "icon-192.png", "sizes": "192x192"}]},
        ],
    })
    resp.mimetype = "application/manifest+json"
    return resp


@bp.get("/sw.js")
def service_worker():
    # Served from the root so the worker's scope is the whole app.
    resp = send_from_directory(current_app.static_folder, "sw.js", mimetype="application/javascript", max_age=0)
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.get("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links: proves the Android app (from PWABuilder) belongs to this site,
    so it opens full-screen without a browser bar."""
    pkg = current_app.config.get("TWA_PACKAGE_NAME", "")
    fps = [f.strip().upper() for f in current_app.config.get("TWA_SHA256_FINGERPRINTS", "").split(",") if f.strip()]
    if not pkg or not fps:
        abort(404)
    return jsonify([{"relation": ["delegate_permission/common.handle_all_urls"],
                     "target": {"namespace": "android_app", "package_name": pkg, "sha256_cert_fingerprints": fps}}])


@bp.get("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.get("/campaigns")
@login_required
def campaigns():
    return render_template("campaigns.html")


@bp.get("/import")
@login_required
def bulk_import():
    return render_template("import.html")


@bp.get("/admin")
@admin_required
def admin():
    return render_template("admin.html")


@bp.get("/admin/users/<int:user_id>")
@admin_required
def admin_user(user_id: int):
    if get_db().execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        abort(404)
    return render_template("admin_user.html", user_id=user_id)
