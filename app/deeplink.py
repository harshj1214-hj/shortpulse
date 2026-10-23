"""Open YouTube, Spotify and Instagram links in the native app on phones.

How it works: on a phone, instead of a plain 302 the visitor gets a tiny page
that tries the app first and falls back to the website.
  * Android: an `intent://` URL with the app's package name. Chrome opens the
    app if it is installed, otherwise it goes to `S.browser_fallback_url` itself.
  * iOS: the app's URL scheme (`vnd.youtube://`, `spotify:track:...`,
    `instagram://user?...`). If nothing happens within 1.5 s the page continues
    to the website.
App URL schemes are controlled by the app makers and can change; test on real
phones before a demo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qs, quote, urlsplit

MOBILE_RE = re.compile(r"Android|iPhone|iPad|iPod", re.IGNORECASE)
IOS_RE = re.compile(r"iPhone|iPad|iPod", re.IGNORECASE)
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SPOTIFY_RE = re.compile(r"^/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?(track|album|playlist|artist|episode|show)/([A-Za-z0-9]{22})")
IG_HOSTS = {"instagram.com", "www.instagram.com"}
IG_USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
IG_NOT_USERS = {"p", "reel", "reels", "tv", "explore", "accounts", "stories", "direct", "about", "legal"}


@dataclass(frozen=True)
class AppTarget:
    platform: str
    label: str
    ios: str | None      # None: no reliable scheme, iPhones get a normal redirect
    android: str
    web: str


def is_mobile(user_agent: str) -> bool:
    return bool(MOBILE_RE.search(user_agent or ""))


def _intent(parts: SplitResult, package: str, web: str) -> str:
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return (f"intent://{parts.netloc}{path}{query}#Intent;scheme=https;package={package};"
            f"S.browser_fallback_url={quote(web, safe='')};end")


def _youtube(p: SplitResult, web: str) -> AppTarget | None:
    host = (p.hostname or "").lower()
    vid = None
    if host == "youtu.be":
        vid = p.path.strip("/").split("/")[0]
    elif host in YT_HOSTS:
        if p.path == "/watch":
            vid = parse_qs(p.query).get("v", [""])[0]
        else:
            m = re.match(r"^/(?:shorts|live|embed)/([^/]+)", p.path)
            vid = m.group(1) if m else None
    else:
        return None
    if vid and YT_ID_RE.match(vid):
        return AppTarget("youtube", "YouTube", f"vnd.youtube://{vid}",
                         _intent(urlsplit(f"https://www.youtube.com/watch?v={vid}"),
                                 "com.google.android.youtube", web), web)
    if host == "youtu.be":
        return None
    ios = f"youtube://www.youtube.com{p.path or '/'}" + (f"?{p.query}" if p.query else "")
    return AppTarget("youtube", "YouTube", ios, _intent(p, "com.google.android.youtube", web), web)


def _spotify(p: SplitResult, web: str) -> AppTarget | None:
    if (p.hostname or "").lower() != "open.spotify.com":
        return None
    m = SPOTIFY_RE.match(p.path)
    if not m:
        return None
    kind, sid = m.groups()
    return AppTarget("spotify", "Spotify", f"spotify:{kind}:{sid}", _intent(p, "com.spotify.music", web), web)


def _instagram(p: SplitResult, web: str) -> AppTarget | None:
    if (p.hostname or "").lower() not in IG_HOSTS:
        return None
    segs = [s for s in p.path.split("/") if s]
    ios = None
    if len(segs) == 1 and segs[0].lower() not in IG_NOT_USERS and IG_USER_RE.match(segs[0]):
        ios = f"instagram://user?username={segs[0]}"
    elif not segs:
        ios = "instagram://app"
    # Posts and reels: iOS has no public scheme that takes the URL's short code,
    # so iPhones get a normal redirect (iOS may still hand it to the app).
    return AppTarget("instagram", "Instagram", ios, _intent(p, "com.instagram.android", web), web)


def detect(url: str) -> AppTarget | None:
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    for fn in (_youtube, _spotify, _instagram):
        target = fn(p, url)
        if target:
            return target
    return None


def describe(url: str | None) -> dict | None:
    t = detect(url) if url else None
    return {"platform": t.platform, "label": t.label} if t else None


def app_url_for(target: AppTarget, user_agent: str) -> str | None:
    """The URL to try on this phone, or None if a normal redirect is better."""
    if IOS_RE.search(user_agent or ""):
        return target.ios
    if re.search(r"Android", user_agent or "", re.IGNORECASE):
        return target.android
    return None
