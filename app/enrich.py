"""Click enrichment: device/browser/OS (user-agents) and location (GeoLite2).

Both run in the click-log worker thread, never in the redirect path.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("shortpulse.enrich")

try:
    from user_agents import parse as _ua_parse
except ImportError:  # keeps local dev working before `pip install -r requirements.txt`
    _ua_parse = None
    log.warning("user-agents not installed; using the minimal built-in parser")


def parse_user_agent(ua: str) -> dict:
    if not ua:
        return {"device": "Other", "browser": "Other", "os": "Other"}
    if _ua_parse is not None:
        u = _ua_parse(ua)
        device = ("Bot" if u.is_bot else "Tablet" if u.is_tablet else "Mobile" if u.is_mobile
                  else "Desktop" if u.is_pc else "Other")
        return {"device": device, "browser": u.browser.family or "Other", "os": u.os.family or "Other"}
    return _fallback_parse(ua)


def _fallback_parse(ua: str) -> dict:
    device = ("Tablet" if re.search(r"iPad|Tablet", ua) else
              "Mobile" if re.search(r"Mobi|iPhone|Android", ua) else
              "Desktop" if re.search(r"Windows|Macintosh|X11|Linux", ua) else "Other")
    for pattern, name in ((r"Edg/", "Edge"), (r"OPR/", "Opera"), (r"SamsungBrowser", "Samsung Internet"),
                          (r"Firefox|FxiOS", "Firefox"), (r"Chrome|CriOS", "Chrome"),
                          (r"Safari", "Safari")):
        if re.search(pattern, ua):
            browser = name
            break
    else:
        browser = "Other"
    for pattern, name in ((r"Windows", "Windows"), (r"Android", "Android"), (r"iPhone|iPad", "iOS"),
                          (r"Mac OS X", "Mac OS X"), (r"Linux", "Linux")):
        if re.search(pattern, ua):
            os_name = name
            break
    else:
        os_name = "Other"
    return {"device": device, "browser": browser, "os": os_name}


def referrer_domain(referrer: str) -> str:
    if not referrer:
        return ""
    try:
        host = urlsplit(referrer).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


class GeoLookup:
    """MaxMind GeoLite2-City lookup. The .mmdb file needs a free MaxMind account
    and must not be committed to a public repo; point GEOLITE2_PATH at it."""

    def __init__(self, path: str | None):
        self.reader = None
        if not path:
            return
        if not Path(path).exists():
            log.warning("GEOLITE2_PATH=%s does not exist; locations will show as Unknown", path)
            return
        try:
            import geoip2.database
            self.reader = geoip2.database.Reader(path)
        except Exception as exc:
            log.warning("GeoLite2 unavailable (%s); locations will show as Unknown", exc)

    @property
    def enabled(self) -> bool:
        return self.reader is not None

    def lookup(self, ip: str) -> tuple[str, str]:
        if not self.reader or not ip:
            return "Unknown", ""
        try:
            if not ipaddress.ip_address(ip).is_global:
                return "Local network", ""
            resp = self.reader.city(ip)
            return resp.country.name or "Unknown", resp.city.name or ""
        except Exception:  # AddressNotFoundError, bad IP, ...
            return "Unknown", ""
