from __future__ import annotations

import re
import sqlite3

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, now
from .security import client_ip, rate_limited

bp = Blueprint("auth", __name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _safe_next(target: str | None) -> str:
    # Only allow local paths, never //evil.com or https://evil.com (open redirect).
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("views.dashboard")


def _login(user) -> None:
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("views.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        error = None
        if not EMAIL_RE.match(email):
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Use at least 8 characters for your password."
        if error is None:
            db = get_db()
            try:
                cur = db.execute("INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, 'user', ?)",
                                 (email, generate_password_hash(password), now()))
            except sqlite3.IntegrityError:
                error = "An account with this email already exists. Sign in instead."
            else:
                _login({"id": cur.lastrowid})
                return redirect(url_for("views.dashboard"))
        flash(error, "danger")
    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("views.dashboard"))
    if request.method == "POST":
        wait = rate_limited(f"login:{client_ip()}", current_app.config["RATE_LOGIN"])
        if wait:
            flash(f"Too many sign-in attempts. Try again in {wait} seconds.", "danger")
            return render_template("login.html"), 429
        email = request.form.get("email", "").strip().lower()
        db = get_db()
        user = db.execute("SELECT id, password_hash, status FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            if user["status"] == "suspended":
                flash("This account is suspended. Contact an administrator.", "danger")
                return render_template("login.html"), 403
            if user["status"] == "active":
                db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now(), user["id"]))
                _login(user)
                return redirect(_safe_next(request.args.get("next")))
        flash("Email or password is incorrect.", "danger")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("views.index"))
