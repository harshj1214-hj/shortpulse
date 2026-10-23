from __future__ import annotations

from flask import Blueprint, abort, g, redirect, render_template, url_for

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
