# Alternative to the native Render runtime (Render can build this Dockerfile too).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc make libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/shortpulse
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN make -C engine clean all test
RUN python -c "import sys; sys.path.insert(0, '.'); from app.ml import Models; s = Models('ml/models').status(); sys.exit(0 if s['bot_model']['loaded'] and s['url_model']['loaded'] else 1)" \
    || python ml/train_models.py --quiet
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "gunicorn wsgi:app --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:${PORT}"]
