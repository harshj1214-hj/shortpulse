"""ctypes wrapper for the C core engine (engine/libshortpulse.so).

Python never re-implements the engine's decisions: if the shared library is
missing the app refuses to start, so a demo can't silently run on a fallback.
"""
from __future__ import annotations

import ctypes
import os
import platform
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Mirrors enum lp_decision in shortpulse_engine.h
ALLOW, DENY_DISABLED, DENY_PAUSED, DENY_EXPIRED, DENY_LIMIT, NEED_PASSWORD, BAD_PASSWORD = range(7)
DECISION_NAMES = {
    ALLOW: "allow", DENY_DISABLED: "disabled", DENY_PAUSED: "paused", DENY_EXPIRED: "expired",
    DENY_LIMIT: "limit_reached", NEED_PASSWORD: "need_password", BAD_PASSWORD: "bad_password",
}
STATUS_CODES = {"active": 0, "paused": 1, "disabled": 2}
PW_NOT_SUPPLIED, PW_WRONG, PW_CORRECT = -1, 0, 1

# Mirrors enum lp_alias_result
ALIAS_MESSAGES = {
    1: ("alias_too_short", "Alias must be at least 3 characters."),
    2: ("alias_too_long", "Alias can be at most 32 characters."),
    3: ("alias_bad_chars", "Use only letters, numbers, hyphens and underscores."),
    4: ("alias_reserved", "This alias is reserved by the platform."),
    5: ("alias_taken", "This alias is already in use."),
}


def _default_lib_path() -> Path:
    names = {"Windows": "shortpulse.dll", "Darwin": "libshortpulse.dylib"}
    return ROOT / "engine" / names.get(platform.system(), "libshortpulse.so")


class Engine:
    def __init__(self, lib_path: str | os.PathLike | None = None,
                 alias_capacity: int = 4096, rate_max_keys: int = 100_000):
        path = Path(lib_path or os.environ.get("SHORTPULSE_ENGINE") or _default_lib_path())
        if not path.exists():
            hint = "windows.cmd build" if os.name == "nt" else "make -C engine"
            raise RuntimeError(f"C engine not found at {path}. Build it first: `{hint}`")
        # CDLL (not PyDLL) releases the GIL during calls; the C side is lock-protected.
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as exc:
            raise RuntimeError(
                f"Could not load the C engine at {path}: {exc}. On Windows this usually means the DLL "
                "and Python are different bitness (32 vs 64-bit). Use 64-bit MinGW-w64 gcc and 64-bit "
                "Python, then run `windows.cmd build` again.") from exc
        self._lib = lib

        lib.lp_version.restype = ctypes.c_char_p
        lib.lp_init.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        lib.lp_init.restype = ctypes.c_int
        lib.lp_base62_encode.argtypes = [ctypes.c_uint64, ctypes.c_char_p, ctypes.c_size_t]
        lib.lp_base62_encode.restype = ctypes.c_int
        lib.lp_base62_decode.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64)]
        lib.lp_base62_decode.restype = ctypes.c_int
        lib.lp_generate_code.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        lib.lp_generate_code.restype = ctypes.c_int
        for fn in ("lp_alias_validate", "lp_alias_add", "lp_alias_remove",
                   "lp_alias_exists", "lp_reserved_add"):
            getattr(lib, fn).argtypes = [ctypes.c_char_p]
            getattr(lib, fn).restype = ctypes.c_int
        lib.lp_alias_count.restype = ctypes.c_size_t
        lib.lp_check_rules.argtypes = [ctypes.c_int, ctypes.c_int64, ctypes.c_int64,
                                       ctypes.c_int64, ctypes.c_int64, ctypes.c_int, ctypes.c_int]
        lib.lp_check_rules.restype = ctypes.c_int
        lib.lp_rate_take.argtypes = [ctypes.c_char_p, ctypes.c_double, ctypes.c_double, ctypes.c_int64]
        lib.lp_rate_take.restype = ctypes.c_int64
        lib.lp_rate_key_count.restype = ctypes.c_size_t
        lib.lp_rate_reset.restype = None
        lib.lp_shutdown.restype = None
        self._init_args = (alias_capacity, rate_max_keys)

        if lib.lp_init(alias_capacity, rate_max_keys) != 0:
            raise RuntimeError("C engine failed to initialise (out of memory?)")

    def reset(self) -> None:
        """Drop all engine state (aliases, reserved words, buckets). The library
        is process-global, so tests that build several apps call this first."""
        self._lib.lp_shutdown()
        self._lib.lp_init(*self._init_args)

    # ---- info ---------------------------------------------------------------
    @property
    def version(self) -> str:
        return self._lib.lp_version().decode()

    # ---- base62 -------------------------------------------------------------
    def base62_encode(self, value: int) -> str:
        buf = ctypes.create_string_buffer(16)
        if self._lib.lp_base62_encode(value, buf, 16) < 0:
            raise ValueError("encode failed")
        return buf.value.decode()

    def base62_decode(self, s: str) -> int:
        out = ctypes.c_uint64()
        if self._lib.lp_base62_decode(s.encode(), ctypes.byref(out)) != 0:
            raise ValueError(f"not a base62 string: {s!r}")
        return out.value

    def generate_code(self, length: int = 7) -> str:
        buf = ctypes.create_string_buffer(length + 1)
        if self._lib.lp_generate_code(buf, length) != 0:
            raise RuntimeError("could not generate a free short code")
        return buf.value.decode()

    # ---- aliases ------------------------------------------------------------
    @staticmethod
    def _b(s: str) -> bytes:
        return s.encode("utf-8", errors="replace")

    def alias_validate(self, alias: str) -> int:
        return self._lib.lp_alias_validate(self._b(alias))

    def alias_add(self, alias: str) -> bool:
        return self._lib.lp_alias_add(self._b(alias)) == 1

    def alias_remove(self, alias: str) -> bool:
        return self._lib.lp_alias_remove(self._b(alias)) == 1

    def alias_exists(self, alias: str) -> bool:
        return self._lib.lp_alias_exists(self._b(alias)) == 1

    def alias_count(self) -> int:
        return self._lib.lp_alias_count()

    def reserve(self, word: str) -> None:
        self._lib.lp_reserved_add(self._b(word))

    # ---- rules --------------------------------------------------------------
    def check_rules(self, *, status: str, expires_at: int | None, now: int,
                    clicks: int, max_clicks: int | None, has_password: bool,
                    password_state: int = PW_NOT_SUPPLIED) -> int:
        return self._lib.lp_check_rules(
            STATUS_CODES.get(status, 99), int(expires_at or 0), int(now),
            int(clicks), int(max_clicks or 0), 1 if has_password else 0, password_state)

    # ---- rate limiter -------------------------------------------------------
    def rate_take(self, key: str, capacity: float, refill_per_sec: float,
                  now_ms: int | None = None) -> int:
        """Return 0 if allowed, else milliseconds to wait."""
        if now_ms is None:
            now_ms = int(time.monotonic() * 1000)
        return self._lib.lp_rate_take(self._b(key), float(capacity), float(refill_per_sec), now_ms)

    def rate_reset(self) -> None:
        self._lib.lp_rate_reset()

    def rate_key_count(self) -> int:
        return self._lib.lp_rate_key_count()
