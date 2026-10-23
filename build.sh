#!/usr/bin/env bash
# Render build step (also fine locally): compile + test the C engine, install
# Python deps, and retrain the models only if the bundled ones can't be loaded.
set -euo pipefail
make -C engine
make -C engine test
pip install -r requirements.txt
python - <<'PY' || python ml/train_models.py --quiet
import sys
sys.path.insert(0, ".")
from app.ml import Models
s = Models("ml/models").status()
sys.exit(0 if s["bot_model"]["loaded"] and s["url_model"]["loaded"] else 1)
PY
