"""Feature extraction shared by training (ml/) and serving (app/).

Both the notebook and the Flask app import this module, so a model can never
be trained on one definition of a feature and served with another. Each saved
model stores the feature list it was trained with; app/ml.py refuses to use a
model whose list does not match the lists below.
"""
from __future__ import annotations

import ipaddress
import math
import re
from collections import Counter
from urllib.parse import urlsplit

# --------------------------------------------------------------------------
# Self-declared crawlers and link-preview fetchers. These are handled by a
# deterministic rule in the redirect path (not by ML): they say who they are.
# --------------------------------------------------------------------------
DECLARED_BOT_RE = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|facebookexternalhit|facebookcatalog|preview|"
    r"embedly|whatsapp|telegram|skypeuripreview|bitlybot|lighthouse|pingdom|"
    r"uptime|monitor|headlesschrome|phantomjs|python-urllib|scrapy",
    re.IGNORECASE)

HTTP_LIBRARY_RE = re.compile(
    r"python-requests|curl/|wget/|go-http-client|okhttp|java/|axios|node-fetch|"
    r"libwww|httpclient|aiohttp|httpx|postmanruntime|insomnia",
    re.IGNORECASE)
BROWSER_TOKEN_RE = re.compile(r"mozilla/5\.0.*(chrome|safari|firefox|edg|opr)/", re.IGNORECASE)
HEADLESS_RE = re.compile(r"headless|electron|puppeteer|playwright|selenium|webdriver", re.IGNORECASE)


def is_declared_bot(user_agent: str) -> bool:
    return bool(DECLARED_BOT_RE.search(user_agent or ""))


# --------------------------------------------------------------------------
# Bot-click features (Random Forest). Behavioural features come from the
# click-log worker's sliding window per visitor.
# --------------------------------------------------------------------------
BOT_FEATURES = [
    "ua_length",
    "ua_has_browser_token",
    "ua_is_http_library",
    "ua_is_headless",
    "ua_empty",
    "has_accept_language",
    "has_referrer",
    "is_head_request",
    "clicks_last_60s",
    "distinct_links_last_60s",
    "secs_since_prev_click",
]


def bot_features(*, user_agent: str, has_accept_language: bool, has_referrer: bool,
                 is_head_request: bool, clicks_last_60s: int, distinct_links_last_60s: int,
                 secs_since_prev_click: float | None) -> list[float]:
    ua = user_agent or ""
    gap = 3600.0 if secs_since_prev_click is None else min(float(secs_since_prev_click), 3600.0)
    return [
        float(min(len(ua), 512)),
        float(bool(BROWSER_TOKEN_RE.search(ua))),
        float(bool(HTTP_LIBRARY_RE.search(ua))),
        float(bool(HEADLESS_RE.search(ua))),
        float(len(ua.strip()) == 0),
        float(bool(has_accept_language)),
        float(bool(has_referrer)),
        float(bool(is_head_request)),
        float(clicks_last_60s),
        float(distinct_links_last_60s),
        gap,
    ]


# --------------------------------------------------------------------------
# Malicious-URL features (Logistic Regression). Lexical only: no network
# calls, so scoring is instant and cannot be abused for SSRF.
# --------------------------------------------------------------------------
SUSPICIOUS_TLDS = {
    "zip", "mov", "xyz", "top", "tk", "ml", "ga", "cf", "gq", "click", "country",
    "kim", "work", "rest", "fit", "loan", "men", "date", "racing", "review",
}
SUSPICIOUS_WORDS = (
    "login", "signin", "verify", "verification", "account", "secure", "update",
    "banking", "confirm", "password", "wallet", "unlock", "suspend", "kyc",
    "reward", "bonus", "free", "gift", "claim", "otp",
)
EXECUTABLE_EXT_RE = re.compile(r"\.(exe|scr|apk|bat|cmd|msi|jar|vbs|ps1)(\?|$)", re.IGNORECASE)

URL_FEATURES = [
    "url_length",
    "host_length",
    "path_length",
    "num_dots_host",
    "num_hyphens_host",
    "num_subdomains",
    "digit_ratio",
    "special_char_count",
    "has_ip_host",
    "has_at_symbol",
    "is_https",
    "has_port",
    "suspicious_tld",
    "suspicious_word_count",
    "has_punycode",
    "path_depth",
    "query_param_count",
    "host_entropy",
    "double_slash_in_path",
    "has_executable_ext",
]

# Human-readable explanations used in wizard warnings.
URL_FEATURE_TEXT = {
    "url_length": "very long URL",
    "host_length": "unusually long domain name",
    "path_length": "very long path",
    "num_dots_host": "many dots in the domain",
    "num_hyphens_host": "many hyphens in the domain",
    "num_subdomains": "deeply nested subdomains",
    "digit_ratio": "lots of digits in the URL",
    "special_char_count": "many special characters",
    "has_ip_host": "raw IP address instead of a domain",
    "has_at_symbol": "'@' symbol in the URL",
    "is_https": "not using HTTPS",
    "has_port": "non-standard port",
    "suspicious_tld": "domain ending often used for abuse",
    "suspicious_word_count": "words like login/verify/account",
    "has_punycode": "internationalised (look-alike) domain",
    "path_depth": "deeply nested path",
    "query_param_count": "many query parameters",
    "host_entropy": "random-looking domain name",
    "double_slash_in_path": "'//' inside the path",
    "has_executable_ext": "links to an executable file",
}


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return bool(re.fullmatch(r"(0x[0-9a-f]+|\d+)", host, re.IGNORECASE))  # 0x7f000001 / 2130706433


def url_features(url: str) -> list[float]:
    url = (url or "").strip()
    try:
        parts = urlsplit(url if "://" in url else "http://" + url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        parts, host, port = urlsplit("http://invalid"), "", None
    path = parts.path or ""
    labels = [p for p in host.split(".") if p]
    tld = labels[-1] if labels else ""
    lowered = url.lower()
    digits = sum(ch.isdigit() for ch in url)
    host_is_ip = _is_ip(host) if host else False
    return [
        float(min(len(url), 2048)),
        float(len(host)),
        float(len(path)),
        float(host.count(".")),
        float(host.count("-")),
        float(max(len(labels) - 2, 0)) if not host_is_ip else 0.0,
        digits / max(len(url), 1),
        float(sum(url.count(c) for c in "@~%=&;!$*")),
        float(host_is_ip),
        float("@" in parts.netloc),
        float(parts.scheme == "https"),
        float(port is not None and port not in (80, 443)),
        float(tld in SUSPICIOUS_TLDS),
        float(sum(lowered.count(w) for w in SUSPICIOUS_WORDS)),
        float("xn--" in host),
        float(len([p for p in path.split("/") if p])),
        float(len([q for q in parts.query.split("&") if q])),
        _entropy(labels[-2] if len(labels) >= 2 else host),
        float("//" in path),
        float(bool(EXECUTABLE_EXT_RE.search(path))),
    ]
