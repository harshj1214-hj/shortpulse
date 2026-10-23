"""QR & sharing studio: styled QR codes (PNG and SVG) with colours, module
styles, rounded "eyes", a centre logo and a caption.

The qrcode library only builds the module matrix (`get_matrix()`); drawing is
done here with Pillow (PNG) or plain SVG text, so every style is under our
control and testable. A logo hides part of the code, so it forces the highest
error-correction level (H, ~30% recoverable) and is capped at 25% of the width.
"""
from __future__ import annotations

import base64
import json
import re
from functools import lru_cache
from io import BytesIO
from xml.sax.saxutils import escape

from flask import Blueprint, abort, current_app, g, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from . import services as S
from .db import get_db, now
from .security import login_required

bp = Blueprint("studio", __name__, url_prefix="/api/links")

DEFAULTS = {"fg": "#16243A", "bg": "#FFFFFF", "style": "square", "eyes": "square", "ec": "M",
            "margin": 4, "caption": "", "logo_scale": 0.22}
STYLES = {"square", "rounded", "dots"}
EYES = {"square", "rounded"}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MIN_CONTRAST = 4.0
# Corner radii of the "soft" finder eyes (outer ring, gap, centre), in modules.
# Rounder eyes look nicer, but OpenCV's detector stopped finding the code at
# radii above ~0.3 module in tests/test_features.py, so only a gentle softening
# is offered. Phone cameras may tolerate more; test on real phones before going rounder.
EYE_RADII = (0.3, 0.21, 0.15)
MAX_LOGO_BYTES = 512 * 1024


# ----------------------------------------------------------------- options
def _luminance(hex_color: str) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g_, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g_) + 0.0722 * ch(b)


def contrast_ratio(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def clean_options(raw: dict, base: dict | None = None) -> tuple[dict, list[str]]:
    opts = dict(base or DEFAULTS)
    errors: list[str] = []
    for key in ("fg", "bg"):
        if key in raw:
            v = str(raw[key]).strip()
            if HEX_RE.match(v):
                opts[key] = v.upper()
            else:
                errors.append(f"{key} must be a colour like #16243A.")
    for key, allowed, label in (("style", STYLES, "module style"), ("eyes", EYES, "eye style"),
                                ("ec", {"L", "M", "Q", "H"}, "error-correction level")):
        if key in raw:
            if raw[key] in allowed:
                opts[key] = raw[key]
            else:
                errors.append(f"Unknown {label}.")
    if "margin" in raw:
        try:
            opts["margin"] = max(2, min(8, int(raw["margin"])))
        except (TypeError, ValueError):
            errors.append("Margin must be a whole number.")
    if "caption" in raw:
        cap = re.sub(r"[\x00-\x1f\x7f]", "", str(raw["caption"] or "")).strip()
        if len(cap) > 40:
            errors.append("Captions can be at most 40 characters.")
        opts["caption"] = cap[:40]
    if "logo_scale" in raw:
        try:
            opts["logo_scale"] = max(0.12, min(0.25, float(raw["logo_scale"])))
        except (TypeError, ValueError):
            errors.append("Logo size must be a number.")
    if not errors:
        if _luminance(opts["fg"]) >= _luminance(opts["bg"]):
            errors.append("Use a darker colour for the code than for the background. "
                          "Many phone cameras can't read light-on-dark codes.")
        elif contrast_ratio(opts["fg"], opts["bg"]) < MIN_CONTRAST:
            errors.append(f"The colours are too similar ({contrast_ratio(opts['fg'], opts['bg']):.1f}:1). "
                          f"Use at least {MIN_CONTRAST:.0f}:1 so phones can scan it.")
    return opts, errors


# ------------------------------------------------------------------ matrix
@lru_cache(maxsize=256)
def build_matrix(data: str, ec: str) -> tuple[tuple[bool, ...], ...]:
    import qrcode  # ImportError is handled by the routes (503)
    levels = {"L": qrcode.constants.ERROR_CORRECT_L, "M": qrcode.constants.ERROR_CORRECT_M,
              "Q": qrcode.constants.ERROR_CORRECT_Q, "H": qrcode.constants.ERROR_CORRECT_H}
    qr = qrcode.QRCode(error_correction=levels[ec], border=0, box_size=1)
    qr.add_data(data)
    qr.make(fit=True)
    return tuple(tuple(bool(c) for c in row) for row in qr.get_matrix())


def _eye_origins(n: int) -> list[tuple[int, int]]:
    return [(0, 0), (0, n - 7), (n - 7, 0)]  # (row, col) of the three finder patterns


def _in_eye(r: int, c: int, n: int) -> bool:
    return any(er <= r < er + 7 and ec <= c < ec + 7 for er, ec in _eye_origins(n))


def _rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _font(px: int):
    try:
        return ImageFont.load_default(size=px)  # Pillow >= 10.1 ships a scalable font
    except TypeError:
        return ImageFont.load_default()


# ------------------------------------------------------------------- PNG
def render_png(matrix, opts: dict, size: int = 1024, logo_png: bytes | None = None) -> bytes:
    n, margin = len(matrix), opts["margin"]
    total = n + 2 * margin
    m = max(2, size // total)
    qr_px = m * total
    cap_h = int(qr_px * 0.15) if opts["caption"] else 0
    fg, bg = _rgb(opts["fg"]), _rgb(opts["bg"])
    img = Image.new("RGB", (qr_px, qr_px + cap_h), bg)
    d = ImageDraw.Draw(img)
    off = margin * m
    style = opts["style"]
    half = m // 2

    for r in range(n):
        for c in range(n):
            if not matrix[r][c] or _in_eye(r, c, n):
                continue
            x, y = off + c * m, off + r * m
            if style == "dots":
                inset = max(1, round(m * 0.06))
                d.ellipse([x + inset, y + inset, x + m - 1 - inset, y + m - 1 - inset], fill=fg)
            elif style == "rounded":
                d.rounded_rectangle([x, y, x + m - 1, y + m - 1], radius=max(1, int(m * 0.42)), fill=fg)
                if c + 1 < n and matrix[r][c + 1] and not _in_eye(r, c + 1, n):
                    d.rectangle([x + half, y, x + m + half - 1, y + m - 1], fill=fg)
                if r + 1 < n and matrix[r + 1][c] and not _in_eye(r + 1, c, n):
                    d.rectangle([x, y + half, x + m - 1, y + m + half - 1], fill=fg)
            else:
                d.rectangle([x, y, x + m - 1, y + m - 1], fill=fg)

    for er, ec in _eye_origins(n):
        x, y, s = off + ec * m, off + er * m, 7 * m
        if opts["eyes"] == "rounded":
            ro, rm, ri = (int(m * k) for k in EYE_RADII)
            d.rounded_rectangle([x, y, x + s - 1, y + s - 1], radius=ro, fill=fg)
            d.rounded_rectangle([x + m, y + m, x + s - m - 1, y + s - m - 1], radius=rm, fill=bg)
            d.rounded_rectangle([x + 2 * m, y + 2 * m, x + s - 2 * m - 1, y + s - 2 * m - 1], radius=ri, fill=fg)
        else:
            d.rectangle([x, y, x + s - 1, y + s - 1], fill=fg)
            d.rectangle([x + m, y + m, x + s - m - 1, y + s - m - 1], fill=bg)
            d.rectangle([x + 2 * m, y + 2 * m, x + s - 2 * m - 1, y + s - 2 * m - 1], fill=fg)

    if logo_png:
        logo = Image.open(BytesIO(logo_png)).convert("RGBA")
        box = int(n * m * opts["logo_scale"])
        logo.thumbnail((box, box))
        pad = max(2, int(m * 0.7))
        cx = cy = off + (n * m) // 2
        lx, ly = cx - logo.width // 2, cy - logo.height // 2
        d.rounded_rectangle([lx - pad, ly - pad, lx + logo.width + pad, ly + logo.height + pad], radius=pad, fill=bg)
        img.paste(logo, (lx, ly), logo)

    if opts["caption"]:
        font = _font(max(10, int(cap_h * 0.42)))
        bbox = d.textbbox((0, 0), opts["caption"], font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        y_text = qr_px + (cap_h - h) / 2 - bbox[1] - off * 0.3
        d.text(((qr_px - w) / 2 - bbox[0], y_text), opts["caption"], fill=fg, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------------- SVG
def render_svg(matrix, opts: dict, logo_png: bytes | None = None) -> str:
    n, margin = len(matrix), opts["margin"]
    total = n + 2 * margin
    cap_h = round(total * 0.15, 2) if opts["caption"] else 0
    fg, bg = opts["fg"], opts["bg"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total + cap_h}" '
           f'width="{total * 16}" height="{round((total + cap_h) * 16)}">',
           f'<rect width="{total}" height="{total + cap_h}" fill="{bg}"/>']
    sq, extra = [], []
    for r in range(n):
        for c in range(n):
            if not matrix[r][c] or _in_eye(r, c, n):
                continue
            x, y = c + margin, r + margin
            if opts["style"] == "dots":
                extra.append(f'<circle cx="{x + .5}" cy="{y + .5}" r=".44" fill="{fg}"/>')
            elif opts["style"] == "rounded":
                extra.append(f'<rect x="{x}" y="{y}" width="1" height="1" rx=".42" fill="{fg}"/>')
                if c + 1 < n and matrix[r][c + 1] and not _in_eye(r, c + 1, n):
                    sq.append(f"M{x + .5} {y}h1v1h-1z")
                if r + 1 < n and matrix[r + 1][c] and not _in_eye(r + 1, c, n):
                    sq.append(f"M{x} {y + .5}h1v1h-1z")
            else:
                sq.append(f"M{x} {y}h1v1h-1z")
    if sq:
        out.append(f'<path d="{"".join(sq)}" fill="{fg}" shape-rendering="crispEdges"/>')
    out.extend(extra)
    for er, ec in _eye_origins(n):
        x, y = ec + margin, er + margin
        ro, rm, ri = EYE_RADII if opts["eyes"] == "rounded" else (0, 0, 0)
        out.append(f'<rect x="{x}" y="{y}" width="7" height="7" rx="{ro}" fill="{fg}"/>')
        out.append(f'<rect x="{x + 1}" y="{y + 1}" width="5" height="5" rx="{rm}" fill="{bg}"/>')
        out.append(f'<rect x="{x + 2}" y="{y + 2}" width="3" height="3" rx="{ri}" fill="{fg}"/>')
    if logo_png:
        box = n * opts["logo_scale"]
        c0 = margin + n / 2 - box / 2
        b64 = base64.b64encode(logo_png).decode()
        out.append(f'<rect x="{c0 - .7:.2f}" y="{c0 - .7:.2f}" width="{box + 1.4:.2f}" height="{box + 1.4:.2f}" rx=".7" fill="{bg}"/>')
        out.append(f'<image href="data:image/png;base64,{b64}" x="{c0:.2f}" y="{c0:.2f}" width="{box:.2f}" height="{box:.2f}"/>')
    if opts["caption"]:
        out.append(f'<text x="{total / 2}" y="{total + cap_h / 2 - margin * .3:.2f}" font-family="Arial, Helvetica, sans-serif" '
                   f'font-size="{cap_h * .42:.2f}" text-anchor="middle" dominant-baseline="middle" fill="{fg}">'
                   f'{escape(opts["caption"])}</text>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------ routes
def _link(link_id: int):
    row = S.get_link_row(get_db(), link_id, g.user)
    if row is None:
        abort(404)
    return row


def _saved(link_id: int) -> tuple[dict, bytes | None]:
    r = get_db().execute("SELECT options, logo_png FROM qr_designs WHERE link_id = ?", (link_id,)).fetchone()
    if not r:
        return dict(DEFAULTS), None
    opts, _ = clean_options(json.loads(r["options"] or "{}"))
    return opts, r["logo_png"]


def _render_args(link_id: int):
    saved, logo = _saved(link_id)
    overrides = {k: v for k, v in request.args.items() if k in DEFAULTS}
    opts, errors = clean_options(overrides, saved) if overrides else (saved, [])
    if request.args.get("logo") == "0":
        logo = None
    return opts, logo, errors


def _matrix_or_error(row, opts, logo):
    try:
        return build_matrix(f"{S.base_url()}/{row['code']}?src=qr", "H" if logo else opts["ec"]), None
    except ImportError:
        return None, (jsonify(error="qr_unavailable",
                              message="QR support is not installed (pip install qrcode pillow)."), 503)


@bp.get("/<int:link_id>/qr.png")
@login_required
def qr_png(link_id: int):
    row = _link(link_id)
    opts, logo, errors = _render_args(link_id)
    if errors:
        return jsonify(error="design", message=errors[0], errors=errors), 422
    matrix, err = _matrix_or_error(row, opts, logo)
    if err:
        return err
    size = max(128, min(2048, request.args.get("size", 640, type=int)))
    return send_file(BytesIO(render_png(matrix, opts, size, logo)), mimetype="image/png", max_age=0,
                     as_attachment=request.args.get("download") == "1", download_name=f"shortpulse-{row['code']}.png")


@bp.get("/<int:link_id>/qr.svg")
@login_required
def qr_svg(link_id: int):
    row = _link(link_id)
    opts, logo, errors = _render_args(link_id)
    if errors:
        return jsonify(error="design", message=errors[0], errors=errors), 422
    matrix, err = _matrix_or_error(row, opts, logo)
    if err:
        return err
    resp = send_file(BytesIO(render_svg(matrix, opts, logo).encode()), mimetype="image/svg+xml", max_age=0,
                     as_attachment=request.args.get("download") == "1", download_name=f"shortpulse-{row['code']}.svg")
    # The caption is escaped; this header is a second fence if the SVG is opened directly.
    resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    return resp


@bp.get("/<int:link_id>/qr-design")
@login_required
def get_design(link_id: int):
    _link(link_id)
    opts, logo = _saved(link_id)
    return jsonify(options=opts, has_logo=logo is not None, defaults=DEFAULTS, min_contrast=MIN_CONTRAST)


@bp.put("/<int:link_id>/qr-design")
@login_required
def save_design(link_id: int):
    _link(link_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    opts, errors = clean_options(data)
    if errors:
        return jsonify(error="design", message=errors[0], errors=errors), 422
    get_db().execute("""INSERT INTO qr_designs (link_id, options, updated_at) VALUES (?, ?, ?)
                        ON CONFLICT(link_id) DO UPDATE SET options = excluded.options, updated_at = excluded.updated_at""",
                     (link_id, json.dumps(opts), now()))
    return jsonify(ok=True, options=opts)


@bp.post("/<int:link_id>/qr-logo")
@login_required
def upload_logo(link_id: int):
    _link(link_id)
    file = request.files.get("file")
    data = file.read(MAX_LOGO_BYTES + 1) if file else b""
    if not data:
        return jsonify(error="logo", message="Choose an image file."), 422
    if len(data) > MAX_LOGO_BYTES:
        return jsonify(error="logo", message="Logos can be at most 512 KB."), 422
    try:
        with Image.open(BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP", "GIF") or im.width * im.height > 4_000_000:
                raise ValueError("unsupported")
            im.load()
            logo = im.convert("RGBA")
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        return jsonify(error="logo", message="Use a PNG, JPEG, WebP or GIF image."), 422
    logo.thumbnail((512, 512))
    buf = BytesIO()
    logo.save(buf, format="PNG")  # re-encoding drops metadata and anything that isn't pixels
    get_db().execute("""INSERT INTO qr_designs (link_id, options, logo_png, updated_at) VALUES (?, '{}', ?, ?)
                        ON CONFLICT(link_id) DO UPDATE SET logo_png = excluded.logo_png, updated_at = excluded.updated_at""",
                     (link_id, buf.getvalue(), now()))
    return jsonify(ok=True)


@bp.delete("/<int:link_id>/qr-logo")
@login_required
def delete_logo(link_id: int):
    _link(link_id)
    get_db().execute("UPDATE qr_designs SET logo_png = NULL, updated_at = ? WHERE link_id = ?", (now(), link_id))
    return jsonify(ok=True)
