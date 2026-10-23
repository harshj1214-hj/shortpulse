/*
 * ShortPulse C core engine — public API.
 *
 * Built as a shared library (libshortpulse.so / .dylib) and called from
 * Python through ctypes (see app/engine.py).
 *
 * Responsibilities ("C decides"):
 *   1. Base62 short-code generation (CSPRNG, no modulo bias)
 *   2. Alias hash table for O(1) "is this alias taken / reserved?" checks
 *   3. Redirect rule evaluation in a fixed order:
 *        status -> expiry -> click limit -> password
 *   4. Token-bucket rate limiter keyed by arbitrary strings
 *
 * Platforms: Linux and macOS (pthreads, getrandom/arc4random) and Windows
 * (SRW locks, BCryptGenRandom) built with MinGW-w64 gcc.
 *
 * Thread safety: every function that touches shared state takes a lock.
 * ctypes releases the GIL during foreign calls, so Flask request threads and
 * the click-log worker really do call in here concurrently.
 *
 * What this engine does NOT guarantee: state is per process. The SQLite
 * database (UNIQUE constraints + atomic UPDATEs) is the source of truth; the
 * engine is the fast path in front of it. See README "Design decisions".
 */
#ifndef SHORTPULSE_ENGINE_H
#define SHORTPULSE_ENGINE_H

#include <stddef.h>
#include <stdint.h>

/* Exported symbols: a Windows DLL must mark them explicitly. */
#if defined(_WIN32)
#define LP_API __declspec(dllexport)
#else
#define LP_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Redirect decisions returned by lp_check_rules. */
enum lp_decision {
    LP_ALLOW = 0,
    LP_DENY_DISABLED = 1,  /* admin takedown */
    LP_DENY_PAUSED = 2,    /* owner paused the link */
    LP_DENY_EXPIRED = 3,
    LP_DENY_LIMIT = 4,     /* click cap reached */
    LP_NEED_PASSWORD = 5,  /* show the password form */
    LP_BAD_PASSWORD = 6
};

/* Link status codes passed into lp_check_rules. */
enum lp_status {
    LP_STATUS_ACTIVE = 0,
    LP_STATUS_PAUSED = 1,
    LP_STATUS_DISABLED = 2
};

/* Password state passed into lp_check_rules. */
enum lp_password_state {
    LP_PW_NOT_SUPPLIED = -1,
    LP_PW_WRONG = 0,
    LP_PW_CORRECT = 1
};

/* Results of lp_alias_validate. */
enum lp_alias_result {
    LP_ALIAS_OK = 0,
    LP_ALIAS_TOO_SHORT = 1,
    LP_ALIAS_TOO_LONG = 2,
    LP_ALIAS_BAD_CHARS = 3,
    LP_ALIAS_RESERVED = 4,
    LP_ALIAS_TAKEN = 5
};

#define LP_ALIAS_MIN_LEN 3
#define LP_ALIAS_MAX_LEN 32

LP_API const char *lp_version(void);

/* Initialise tables. Safe to call more than once (later calls are no-ops).
 * Returns 0 on success, -1 on allocation failure. */
LP_API int lp_init(size_t alias_capacity, size_t rate_max_keys);
LP_API void lp_shutdown(void);

/* ---- Base62 -------------------------------------------------------------- */
/* Encode value into out (NUL-terminated). Returns length, or -1 if too small. */
LP_API int lp_base62_encode(uint64_t value, char *out, size_t out_size);
/* Decode s into *out. Returns 0 on success, -1 on bad input or overflow. */
LP_API int lp_base62_decode(const char *s, uint64_t *out);
/* Write a random Base62 code of `length` chars (+NUL) that is not currently in
 * the alias table. Returns 0 on success, -1 on failure. */
LP_API int lp_generate_code(char *out, size_t length);

/* ---- Alias hash table ---------------------------------------------------- */
/* Full validation: length, charset [A-Za-z0-9_-], reserved words, taken. */
LP_API int lp_alias_validate(const char *alias);
/* Returns 1 if added, 0 if already present, -1 on error. */
LP_API int lp_alias_add(const char *alias);
/* Returns 1 if removed, 0 if not present. */
LP_API int lp_alias_remove(const char *alias);
LP_API int lp_alias_exists(const char *alias);
LP_API size_t lp_alias_count(void);
/* Reserve a word (case-insensitive), e.g. a top-level route like "admin". */
LP_API int lp_reserved_add(const char *word);

/* ---- Rule order ---------------------------------------------------------- */
/* expires_at <= 0 means "never"; max_clicks <= 0 means "no limit".
 * Order is fixed: status, expiry, click limit, password. */
LP_API int lp_check_rules(int status, int64_t expires_at, int64_t now,
                   int64_t clicks, int64_t max_clicks,
                   int has_password, int password_state);

/* ---- Token bucket -------------------------------------------------------- */
/* Take one token from the bucket named `key`. Returns 0 if allowed, otherwise
 * the number of milliseconds until a token will be available (>= 1). */
LP_API int64_t lp_rate_take(const char *key, double capacity, double refill_per_sec,
                     int64_t now_ms);
LP_API size_t lp_rate_key_count(void);
LP_API void lp_rate_reset(void);

#ifdef __cplusplus
}
#endif

#endif /* SHORTPULSE_ENGINE_H */
