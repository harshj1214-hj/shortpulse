"""Training code for the two ShortPulse models. Used by train_models.ipynb and
train_models.py so the notebook and the CLI produce identical models.

IMPORTANT — about the data
--------------------------
No labelled click logs exist for this project yet, so by default both models
are trained on SYNTHETIC data produced by the generators below. Metrics on
synthetic data only prove that the pipeline works; they say nothing about
real-world accuracy (the model mostly learns the generator's assumptions).
To train on real data, pass a CSV (see load_url_csv / load_bot_csv).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (confusion_matrix, f1_score, precision_recall_curve,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import features as F  # noqa: E402

MODEL_DIR = ROOT / "ml" / "models"

# ============================================================================
# Synthetic click data (bot vs human)
# ============================================================================
BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
]
LIBRARY_UAS = ["python-requests/2.32.3", "curl/8.5.0", "Go-http-client/1.1", "okhttp/4.12.0",
               "axios/1.7.7", "Wget/1.21.4", "Java/17.0.2", "PostmanRuntime/7.42.0"]
HEADLESS_UAS = ["Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Electron/31.0 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) Playwright/1.47 Chrome/129.0 Safari/537.36"]


def _human(rng: np.random.Generator) -> dict:
    burst = rng.random() < 0.04  # power users opening several links quickly
    clicks = int(rng.integers(3, 9)) if burst else int(rng.poisson(0.3)) + 1
    first = rng.random() < 0.6
    gap = None if first else float(rng.exponential(8 if burst else 600))
    return dict(user_agent=str(rng.choice(BROWSER_UAS)), has_accept_language=rng.random() < 0.97,
                has_referrer=rng.random() < 0.55, is_head_request=rng.random() < 0.002,
                clicks_last_60s=clicks, distinct_links_last_60s=int(min(clicks, 1 + rng.binomial(clicks, 0.4))),
                secs_since_prev_click=gap, label=0)


def _bot(rng: np.random.Generator) -> dict:
    kind = rng.choice(["script", "spoofed", "slow", "headless", "empty"], p=[.35, .35, .15, .1, .05])
    if kind == "script":
        ua, al, ref, head = str(rng.choice(LIBRARY_UAS)), rng.random() < .1, rng.random() < .05, rng.random() < .2
        clicks, gap = int(rng.poisson(15)) + 1, float(rng.exponential(1.5))
    elif kind == "spoofed":  # browser UA, machine behaviour
        ua, al, ref, head = str(rng.choice(BROWSER_UAS)), rng.random() < .5, rng.random() < .1, rng.random() < .05
        clicks, gap = int(rng.poisson(25)) + 3, float(rng.exponential(0.8))
    elif kind == "slow":  # low-and-slow: deliberately hard to separate from humans
        ua, al, ref, head = str(rng.choice(BROWSER_UAS)), rng.random() < .7, rng.random() < .2, False
        clicks, gap = int(rng.integers(1, 4)), float(rng.uniform(20, 300))
    elif kind == "headless":
        ua, al, ref, head = str(rng.choice(HEADLESS_UAS)), rng.random() < .6, rng.random() < .1, False
        clicks, gap = int(rng.poisson(8)) + 1, float(rng.exponential(3))
    else:
        ua, al, ref, head = "", rng.random() < .1, False, rng.random() < .3
        clicks, gap = int(rng.poisson(5)) + 1, float(rng.exponential(5))
    distinct = int(min(clicks, max(1, rng.binomial(clicks, 0.7))))
    return dict(user_agent=ua, has_accept_language=al, has_referrer=ref, is_head_request=head,
                clicks_last_60s=clicks, distinct_links_last_60s=distinct, secs_since_prev_click=gap, label=1)


def synthetic_clicks(n: int = 20000, bot_share: float = 0.3, label_noise: float = 0.01,
                     seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = [(_bot if rng.random() < bot_share else _human)(rng) for _ in range(n)]
    raw = pd.DataFrame(rows)
    X = pd.DataFrame([F.bot_features(**{k: r[k] for k in r if k != "label"}) for r in rows],
                     columns=F.BOT_FEATURES)
    y = raw["label"].to_numpy().copy()
    flip = rng.random(n) < label_noise
    y[flip] = 1 - y[flip]
    X["label"] = y
    return X


def load_bot_csv(path: str | Path) -> pd.DataFrame:
    """CSV with every column in features.BOT_FEATURES plus `label` (1 = bot)."""
    df = pd.read_csv(path)
    missing = [c for c in F.BOT_FEATURES + ["label"] if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")
    return df[F.BOT_FEATURES + ["label"]].dropna()


# ============================================================================
# Synthetic URL data (benign vs malicious)
# ============================================================================
WORDS = ["news", "shop", "learn", "cloud", "data", "music", "travel", "food", "health", "code",
         "blog", "campus", "events", "library", "sports", "photo", "market", "career", "science", "games"]
BRANDS = ["paypal", "sbi", "hdfc", "amazon", "netflix", "google", "apple", "microsoft", "paytm", "icici"]
SAFE_TLDS = ["com", "org", "in", "edu", "co.in", "io", "gov.in", "net"]
BAD_TLDS = sorted(F.SUSPICIOUS_TLDS)


def _rand_token(rng, n):
    alphabet = list("abcdefghijklmnopqrstuvwxyz0123456789")
    return "".join(rng.choice(alphabet, n))


# Real, popular benign hosts. Long names, subdomains and login pages on these
# are deliberate hard negatives: without them the model learns "long host = bad".
REAL_BENIGN_HOSTS = [
    "en.wikipedia.org", "github.com", "docs.python.org", "flask.palletsprojects.com", "getbootstrap.com",
    "www.chartjs.org", "scikit-learn.org", "stackoverflow.com", "www.youtube.com", "www.linkedin.com",
    "www.instagram.com", "www.amazon.in", "www.flipkart.com", "nptel.ac.in", "swayam.gov.in",
    "www.irctc.co.in", "www.hdfcbank.com", "www.geeksforgeeks.org", "www.w3schools.com", "medium.com",
    "www.coursera.org", "www.kaggle.com", "developer.mozilla.org", "learn.microsoft.com", "cloud.google.com",
    "aws.amazon.com", "timesofindia.indiatimes.com", "www.thehindu.com", "www.zomato.com", "www.swiggy.com",
    "paytm.com", "razorpay.com", "www.canva.com", "www.notion.so", "www.figma.com", "zoom.us",
    "drive.google.com", "docs.google.com", "forms.gle", "play.google.com", "apps.apple.com",
    "support.google.com", "accounts.google.com", "login.microsoftonline.com", "www.onlinesbi.sbi",
    "netbanking.hdfcbank.com", "www.paypal.com", "www.netflix.com", "openai.com", "huggingface.co",
]
SUBDOMAINS = ["", "", "www.", "www.", "docs.", "blog.", "en.", "m.", "api.", "learn.", "support.", "events."]


def _benign_url(rng) -> str:
    if rng.random() < .5:
        host = str(rng.choice(REAL_BENIGN_HOSTS))
    else:
        name = "".join(str(w) for w in rng.choice(WORDS, int(rng.integers(1, 4))))
        if rng.random() < .25:
            name = f"{rng.choice(WORDS)}-{rng.choice(WORDS)}"
        host = f"{rng.choice(SUBDOMAINS)}{name}.{rng.choice(SAFE_TLDS)}"
    scheme = "https" if rng.random() < .9 else "http"
    path = "/".join(str(w) for w in rng.choice(WORDS, int(rng.integers(0, 4))))
    url = f"{scheme}://{host}/{path}"
    r = rng.random()
    if r < .25:
        url += f"?utm_source={rng.choice(WORDS)}&utm_medium=social&utm_campaign={rng.choice(WORDS)}"
    elif r < .35:  # long document / product URLs: benign but "noisy"
        url += f"/d/{_rand_token(rng, 32)}/edit?id={rng.integers(10**6, 10**9)}"
    elif r < .42:  # real sites have login and account pages too
        url += f"/{rng.choice(['login', 'signin', 'account', 'verify-email'])}?next=/{rng.choice(WORDS)}"
    elif r < .55:  # dates and product ids: benign URLs full of digits
        url += f"/{rng.integers(2019, 2027)}/{rng.integers(1, 13):02d}/{rng.choice(WORDS)}-{rng.integers(1, 99999)}"
    return url


def _malicious_url(rng) -> str:
    brand, word = str(rng.choice(BRANDS)), str(rng.choice(["secure", "verify", "account", "update", "login", "kyc"]))
    kind = rng.choice(["ip", "impersonate", "at", "subdomains", "punycode", "exe", "plain"],
                      p=[.15, .3, .08, .15, .07, .1, .15])
    if kind == "ip":
        ip = ".".join(str(int(x)) for x in rng.integers(1, 255, 4))
        return f"http://{ip}/{word}/{brand}/index.php?session={_rand_token(rng, 16)}"
    if kind == "impersonate":
        tld = str(rng.choice(BAD_TLDS)) if rng.random() < .6 else "com"
        return f"http://{brand}-{word}-{rng.choice(WORDS)}.{tld}/{word}/confirm?id={rng.integers(1000, 99999)}"
    if kind == "at":
        return f"http://{brand}.com@{_rand_token(rng, 10)}.{rng.choice(BAD_TLDS)}/{word}"
    if kind == "subdomains":
        return f"http://{brand}.com.{word}.{_rand_token(rng, 8)}.{rng.choice(BAD_TLDS + ['com'])}/{word}.html"
    if kind == "punycode":
        return f"https://xn--{_rand_token(rng, 8)}.com/{word}"
    if kind == "exe":
        return f"http://{_rand_token(rng, 9)}.{rng.choice(BAD_TLDS)}/download/{rng.choice(WORDS)}_setup.exe"
    # "plain": looks clean lexically — a hard case no lexical model can catch reliably
    if rng.random() < .5:
        return f"https://{rng.choice(WORDS)}-{rng.choice(WORDS)}.{rng.choice(SAFE_TLDS)}/{rng.choice(WORDS)}"
    return f"https://{rng.choice(WORDS)}{rng.integers(10, 999)}.com/{rng.choice(WORDS)}"


def synthetic_urls(n: int = 12000, malicious_share: float = 0.4, label_noise: float = 0.01,
                   seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    labels = (rng.random(n) < malicious_share).astype(int)
    urls = [_malicious_url(rng) if lab else _benign_url(rng) for lab in labels]
    flip = rng.random(n) < label_noise
    labels[flip] = 1 - labels[flip]
    return pd.DataFrame({"url": urls, "label": labels})


BENIGN_LABELS = {"0", "benign", "good", "legitimate", "safe", "false"}


def load_url_csv(path: str | Path) -> pd.DataFrame:
    """CSV with `url` and a label column (`label` or `type`). Anything that is
    not benign/good/legitimate/0 is treated as malicious (phishing, malware,
    defacement, ...)."""
    df = pd.read_csv(path)
    label_col = "label" if "label" in df.columns else "type" if "type" in df.columns else None
    if "url" not in df.columns or label_col is None:
        raise ValueError(f"{path} needs a `url` column and a `label` (or `type`) column")
    df = df[["url", label_col]].dropna().drop_duplicates(subset="url")
    df["label"] = (~df[label_col].astype(str).str.strip().str.lower().isin(BENIGN_LABELS)).astype(int)
    return df[["url", "label"]]


def url_matrix(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame([F.url_features(u) for u in df["url"]], columns=F.URL_FEATURES)
    X["label"] = df["label"].to_numpy()
    return X


# ============================================================================
# Training / evaluation
# ============================================================================
def split(df: pd.DataFrame, seed: int = 42):
    """60/20/20 train/validation/test. The threshold is tuned on validation only,
    so the test numbers are not optimistically biased by that choice."""
    X, y = df.drop(columns="label").to_numpy(), df["label"].to_numpy()
    X_tr, X_tmp, y_tr, y_tmp = train_test_split(X, y, test_size=0.4, stratify=y, random_state=seed)
    X_val, X_te, y_val, y_te = train_test_split(X_tmp, y_tmp, test_size=0.5, stratify=y_tmp, random_state=seed)
    return X_tr, y_tr, X_val, y_val, X_te, y_te


def pick_threshold(y_true, proba, min_precision: float) -> float:
    """Highest-recall threshold whose precision is >= min_precision."""
    prec, rec, thr = precision_recall_curve(y_true, proba)
    best = None
    for p, r, t in zip(prec[:-1], rec[:-1], thr):
        if p >= min_precision and (best is None or r > best[1]):
            best = (t, r)
    return float(best[0]) if best else 0.5


def evaluate(y_true, proba, threshold: float) -> dict:
    pred = (proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "threshold": round(float(threshold), 3),
        "precision": round(float(precision_score(y_true, pred, zero_division=0)), 3),
        "recall": round(float(recall_score(y_true, pred, zero_division=0)), 3),
        "f1": round(float(f1_score(y_true, pred, zero_division=0)), 3),
        "roc_auc": round(float(roc_auc_score(y_true, proba)), 3) if len(set(y_true)) > 1 else None,
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "n_test": int(len(y_true)),
    }


def bot_baseline_proba(X: np.ndarray) -> np.ndarray:
    """Hand-written rule (same as the app's fallback) to compare the model against."""
    f = {name: X[:, i] for i, name in enumerate(F.BOT_FEATURES)}
    rule = ((f["ua_is_http_library"] > 0) | (f["ua_is_headless"] > 0) | (f["ua_empty"] > 0)
            | (f["clicks_last_60s"] >= 20) | (f["ua_has_browser_token"] == 0))
    return rule.astype(float)


def url_baseline_proba(X: np.ndarray) -> np.ndarray:
    f = {name: X[:, i] for i, name in enumerate(F.URL_FEATURES)}
    rule = ((f["has_ip_host"] > 0) | (f["has_at_symbol"] > 0) | (f["suspicious_tld"] > 0)
            | (f["has_punycode"] > 0) | (f["has_executable_ext"] > 0))
    return rule.astype(float)


def train_bot_model(df: pd.DataFrame, trained_on: str, seed: int = 42) -> dict:
    X_tr, y_tr, X_val, y_val, X_te, y_te = split(df, seed)
    model = RandomForestClassifier(n_estimators=150, max_depth=12, min_samples_leaf=3,
                                   class_weight="balanced", random_state=seed, n_jobs=-1)
    model.fit(X_tr, y_tr)
    # Precision first: a false positive silently deletes a real human click from analytics.
    threshold = pick_threshold(y_val, model.predict_proba(X_val)[:, 1], min_precision=0.95)
    metrics = {
        "model": evaluate(y_te, model.predict_proba(X_te)[:, 1], threshold),
        "baseline_rule": evaluate(y_te, bot_baseline_proba(X_te), 0.5),
        "feature_importance": dict(sorted(
            ((n, round(float(v), 4)) for n, v in zip(F.BOT_FEATURES, model.feature_importances_)),
            key=lambda kv: -kv[1])),
    }
    return {"model": model, "feature_names": F.BOT_FEATURES, "threshold": threshold,
            "trained_on": trained_on, "trained_at": int(time.time()), "metrics": metrics}


def train_url_model(df: pd.DataFrame, trained_on: str, seed: int = 42) -> dict:
    X_tr, y_tr, X_val, y_val, X_te, y_te = split(df, seed)
    model = Pipeline([("scale", StandardScaler()),
                      ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", C=0.1))])
    model.fit(X_tr, y_tr)
    # This model raises warnings in the wizard; frequent false warnings teach
    # users to ignore them, so we still ask for high precision.
    threshold = pick_threshold(y_val, model.predict_proba(X_val)[:, 1], min_precision=0.95)
    coefs = model.named_steps["clf"].coef_[0]
    metrics = {
        "model": evaluate(y_te, model.predict_proba(X_te)[:, 1], threshold),
        "baseline_rule": evaluate(y_te, url_baseline_proba(X_te), 0.5),
        "coefficients": dict(sorted(((n, round(float(c), 3)) for n, c in zip(F.URL_FEATURES, coefs)),
                                    key=lambda kv: -abs(kv[1]))),
    }
    return {"model": model, "feature_names": F.URL_FEATURES, "threshold": threshold,
            "trained_on": trained_on, "trained_at": int(time.time()), "metrics": metrics}


def save(bundle: dict, name: str, model_dir: Path = MODEL_DIR) -> Path:
    import joblib
    import sklearn
    model_dir.mkdir(parents=True, exist_ok=True)
    bundle = dict(bundle, sklearn_version=sklearn.__version__)
    path = model_dir / name
    joblib.dump(bundle, path, compress=3)
    return path
