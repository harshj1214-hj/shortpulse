"""Asynchronous click logging (threading + queue).

The redirect handler only puts a small dict on the queue and returns. This
worker thread then:
  1. parses the user agent and looks up the location,
  2. computes behavioural features for the visitor (sliding 60 s window),
  3. scores the batch with the bot model,
  4. writes click_events (and click_count for uncapped links) in one transaction.

No click is dropped when the queue is full: the request thread processes that
event itself (backpressure) instead. What can be lost is the queue's contents
if the process is killed with SIGKILL; on normal shutdown we drain the queue.
"""
from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
from collections import deque

from . import features as F
from .db import connect, transaction
from .enrich import parse_user_agent, referrer_domain
from .security import mask_ip, visitor_hash

log = logging.getLogger("shortpulse.clicklog")

INSERT_SQL = """
INSERT INTO click_events (link_id, ts, visitor_hash, source, referrer_domain, user_agent,
                          device, browser, os, country, city, is_bot, bot_score, bot_reason,
                          click_uid, ip_address, ip_masked, app_switch, landing)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def purge_ips(conn, retention_days: int) -> int:
    """Erase full IPs older than the retention period (the masked form stays)."""
    cutoff = int(time.time()) - retention_days * 86400
    return conn.execute("UPDATE click_events SET ip_address = NULL WHERE ip_address IS NOT NULL AND ts < ?",
                        (cutoff,)).rowcount


class ClickLogger:
    def __init__(self, *, db_path: str, models, geo, salt: str, maxsize: int = 10_000,
                 store_full_ip: bool = True, ip_retention_days: int = 30):
        self.store_full_ip = store_full_ip
        self.ip_retention_days = ip_retention_days
        self._next_purge = 0.0
        self.db_path = db_path
        self.models = models
        self.geo = geo
        self.salt = salt
        self.maxsize = maxsize
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._start_lock = threading.Lock()
        self._process_lock = threading.Lock()  # worker vs. backpressure path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid: int | None = None
        self._window: dict[str, deque] = {}     # visitor -> deque[(ts, link_id)]
        self._last_seen: dict[str, float] = {}
        self.stats = {"processed": 0, "sync_fallback": 0, "errors": 0}
        atexit.register(self.stop)

    # ------------------------------------------------------------- lifecycle
    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._pid == os.getpid():
            return
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive() and self._pid == os.getpid():
                return
            if self._pid not in (None, os.getpid()):
                # Forked (e.g. gunicorn --preload): threads don't survive fork, start fresh.
                self.q = queue.Queue(maxsize=self.maxsize)
            self._pid = os.getpid()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="click-logger", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every queued click is stored (used by tests and the demo)."""
        deadline = time.monotonic() + timeout
        while self.q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.q.unfinished_tasks == 0

    # --------------------------------------------------------------- produce
    def enqueue(self, event: dict) -> None:
        self._ensure_worker()
        try:
            self.q.put_nowait(event)
        except queue.Full:
            self.stats["sync_fallback"] += 1
            conn = connect(self.db_path)
            try:
                self._process([event], conn)
            finally:
                conn.close()

    # --------------------------------------------------------------- consume
    def _run(self) -> None:
        conn = connect(self.db_path)
        try:
            while not (self._stop.is_set() and self.q.empty()):
                if time.time() >= self._next_purge:
                    try:
                        purge_ips(conn, self.ip_retention_days)
                    except Exception:
                        log.exception("IP purge failed")
                    self._next_purge = time.time() + 600
                try:
                    batch = [self.q.get(timeout=0.5)]
                except queue.Empty:
                    continue
                while len(batch) < 100:
                    try:
                        batch.append(self.q.get_nowait())
                    except queue.Empty:
                        break
                try:
                    self._process(batch, conn)
                except Exception:
                    self.stats["errors"] += 1
                    log.exception("failed to store %d click(s)", len(batch))
                finally:
                    for _ in batch:
                        self.q.task_done()
        finally:
            conn.close()

    def _behaviour(self, visitor: str, ts: float, link_id: int) -> tuple[int, int, float | None]:
        win = self._window.setdefault(visitor, deque())
        while win and win[0][0] < ts - 60:
            win.popleft()
        prev = self._last_seen.get(visitor)
        win.append((ts, link_id))
        self._last_seen[visitor] = ts
        distinct = len({lid for _, lid in win})
        return len(win), distinct, (ts - prev) if prev is not None else None

    def _prune(self, now: float) -> None:
        stale = [v for v, t in self._last_seen.items() if t < now - 3600]
        for v in stale:
            self._last_seen.pop(v, None)
            self._window.pop(v, None)

    def _process(self, batch: list[dict], conn) -> None:
        with self._process_lock:
            rows, feats = [], []
            for ev in batch:
                ua = ev.get("ua", "")
                vh = visitor_hash(ev.get("ip", ""), ua, self.salt)
                clicks, distinct, gap = self._behaviour(vh, ev["ts"], ev["link_id"])
                feats.append(F.bot_features(
                    user_agent=ua, has_accept_language=ev.get("accept_language", False),
                    has_referrer=bool(ev.get("referrer")), is_head_request=ev.get("is_head", False),
                    clicks_last_60s=clicks, distinct_links_last_60s=distinct, secs_since_prev_click=gap))
                country, city = self.geo.lookup(ev.get("ip", ""))
                rows.append((ev, vh, parse_user_agent(ua), country, city))

            scores = self.models.score_clicks(feats)
            threshold = self.models.bot_threshold
            with transaction(conn):
                for (ev, vh, uai, country, city), (prob, reason) in zip(rows, scores):
                    declared = ev.get("declared_bot", False)
                    is_bot = declared or prob >= threshold
                    if declared:
                        reason = "declared crawler or link preview"
                    conn.execute(INSERT_SQL, (
                        ev["link_id"], int(ev["ts"]), vh, ev.get("source", "link"),
                        referrer_domain(ev.get("referrer", "")), ev.get("ua", "")[:512],
                        uai["device"], uai["browser"], uai["os"], country, city,
                        int(is_bot), round(prob, 4), reason if is_bot else "",
                        ev.get("click_uid"), ev.get("ip") if self.store_full_ip else None,
                        mask_ip(ev.get("ip", "")), int(bool(ev.get("app_switch"))), int(bool(ev.get("landing")))))
                    if ev.get("count_in_worker") and not declared:
                        conn.execute("UPDATE links SET click_count = click_count + 1 WHERE id = ?",
                                     (ev["link_id"],))
            self.stats["processed"] += len(batch)
            if self.stats["processed"] % 1000 < len(batch):
                self._prune(time.time())
