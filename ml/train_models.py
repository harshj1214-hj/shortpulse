"""Train both ShortPulse models and save them to ml/models/*.joblib.

    python ml/train_models.py                       # synthetic data (default)
    python ml/train_models.py --url-csv data.csv    # real URL dataset
    python ml/train_models.py --bot-csv clicks.csv  # real labelled click features

The notebook ml/train_models.ipynb runs the same functions step by step.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import training as T  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-csv", help="CSV with url,label (or url,type)")
    ap.add_argument("--bot-csv", help="CSV with BOT_FEATURES columns + label")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.bot_csv:
        bot_df, bot_src = T.load_bot_csv(args.bot_csv), f"csv:{Path(args.bot_csv).name}"
    else:
        bot_df, bot_src = T.synthetic_clicks(), "synthetic"
    bot = T.train_bot_model(bot_df, bot_src)
    T.save(bot, "bot_model.joblib")

    if args.url_csv:
        url_df, url_src = T.url_matrix(T.load_url_csv(args.url_csv)), f"csv:{Path(args.url_csv).name}"
    else:
        url_df, url_src = T.url_matrix(T.synthetic_urls()), "synthetic"
    url = T.train_url_model(url_df, url_src)
    T.save(url, "url_model.joblib")

    if not args.quiet:
        print(json.dumps({"bot_model": {"trained_on": bot_src, **bot["metrics"]},
                          "url_model": {"trained_on": url_src, **url["metrics"]}}, indent=2))
    if "synthetic" in (bot_src, url_src):
        print("\nNOTE: synthetic-data metrics validate the pipeline only, not real-world accuracy.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
