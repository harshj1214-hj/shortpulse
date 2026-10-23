"""Loads the scikit-learn models saved by ml/train_models.py (joblib bundles).

Failure policy:
  * Model file missing or trained on a different feature list -> the model is
    disabled, a warning is logged, and a simple documented heuristic is used
    instead. The app keeps working; the dashboard shows which mode is active.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from . import features as F

log = logging.getLogger("shortpulse.ml")


class _Bundle:
    def __init__(self, path: Path, expected_features: list[str]):
        self.path = path
        self.model = None
        self.threshold = 0.5
        self.info: dict = {"loaded": False, "path": str(path)}
        if not path.exists():
            log.warning("model %s not found; using heuristic fallback", path.name)
            return
        try:
            import joblib
            bundle = joblib.load(path)
        except Exception as exc:  # version mismatch, corrupt file, ...
            log.warning("could not load %s (%s); using heuristic fallback", path.name, exc)
            return
        if bundle.get("feature_names") != expected_features:
            log.warning("%s was trained on a different feature list; retrain it", path.name)
            return
        self.model = bundle["model"]
        self.threshold = float(bundle.get("threshold", 0.5))
        self.info = {
            "loaded": True,
            "path": str(path),
            "trained_on": bundle.get("trained_on", "unknown"),
            "trained_at": bundle.get("trained_at"),
            "metrics": bundle.get("metrics", {}),
            "threshold": self.threshold,
        }
        try:  # single-threaded inference inside request/worker threads
            self.model.set_params(**{k: 1 for k in self.model.get_params() if k.endswith("n_jobs")})
        except Exception:
            pass


class Models:
    def __init__(self, model_dir: str | Path):
        model_dir = Path(model_dir)
        self.bot = _Bundle(model_dir / "bot_model.joblib", F.BOT_FEATURES)
        self.url = _Bundle(model_dir / "url_model.joblib", F.URL_FEATURES)

    def status(self) -> dict:
        return {"bot_model": self.bot.info, "url_model": self.url.info}

    # ------------------------------------------------------------------ URLs
    def score_url(self, url: str) -> tuple[float, list[str]]:
        """Return (probability the URL is malicious, top reasons)."""
        x = F.url_features(url)
        if self.url.model is None:
            return self._url_heuristic(x)
        prob = float(self.url.model.predict_proba(np.array([x]))[0][1])
        return prob, self._url_reasons(x)

    def _url_reasons(self, x: list[float]) -> list[str]:
        """Explain a logistic-regression score: coefficient x scaled value."""
        try:
            scaler = self.url.model.named_steps["scale"]
            clf = self.url.model.named_steps["clf"]
        except (AttributeError, KeyError):
            return []
        scaled = scaler.transform(np.array([x]))[0]
        contrib = clf.coef_[0] * scaled
        reasons = []
        for i in np.argsort(contrib)[::-1]:
            name = F.URL_FEATURES[i]
            if contrib[i] <= 0.25 or len(reasons) == 3:
                break
            if name == "is_https":
                if x[i] == 0:
                    reasons.append(F.URL_FEATURE_TEXT[name])
            elif scaled[i] > 0:
                reasons.append(F.URL_FEATURE_TEXT[name])
        return reasons

    @staticmethod
    def _url_heuristic(x: list[float]) -> tuple[float, list[str]]:
        f = dict(zip(F.URL_FEATURES, x))
        reasons = []
        score = 0.05
        for name, weight in (("has_ip_host", .35), ("has_at_symbol", .35), ("suspicious_tld", .2),
                             ("has_punycode", .2), ("has_executable_ext", .3)):
            if f[name]:
                score += weight
                reasons.append(F.URL_FEATURE_TEXT[name])
        if f["suspicious_word_count"] >= 2:
            score += .2
            reasons.append(F.URL_FEATURE_TEXT["suspicious_word_count"])
        return min(score, 0.99), reasons[:3]

    # ---------------------------------------------------------------- clicks
    def score_clicks(self, rows: list[list[float]]) -> list[tuple[float, str]]:
        """Batch-score click feature rows. Returns (bot probability, reason)."""
        if not rows:
            return []
        if self.bot.model is None:
            return [self._bot_heuristic(r) for r in rows]
        probs = self.bot.model.predict_proba(np.array(rows))[:, 1]
        return [(float(p), self._bot_reason(r) if p >= self.bot.threshold else "") for p, r in zip(probs, rows)]

    @property
    def bot_threshold(self) -> float:
        return self.bot.threshold

    @staticmethod
    def _bot_reason(r: list[float]) -> str:
        f = dict(zip(F.BOT_FEATURES, r))
        if f["clicks_last_60s"] >= 10:
            return f"{int(f['clicks_last_60s'])} clicks from this visitor in 60s"
        if f["ua_is_http_library"]:
            return "scripted HTTP client"
        if f["ua_is_headless"]:
            return "headless browser"
        if f["ua_empty"]:
            return "no user agent"
        if f["secs_since_prev_click"] < 2:
            return "clicks less than 2s apart"
        return "behaviour matches automated traffic"

    def _bot_heuristic(self, r: list[float]) -> tuple[float, str]:
        f = dict(zip(F.BOT_FEATURES, r))
        bot = (f["ua_is_http_library"] or f["ua_is_headless"] or f["ua_empty"]
               or f["clicks_last_60s"] >= 20 or not f["ua_has_browser_token"])
        return (0.9, self._bot_reason(r)) if bot else (0.1, "")
