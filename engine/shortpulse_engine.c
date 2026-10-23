/*
 * ShortPulse C core engine — implementation. See shortpulse_engine.h.
 *
 * Build:   make -C engine           (Linux/macOS -> libshortpulse.so / .dylib)
 *          windows.cmd build        (Windows, MinGW-w64 gcc -> shortpulse.dll)
 * Test:    make -C engine test  /  windows.cmd test
 */
#if defined(__linux__)
#define _GNU_SOURCE
#endif

#include "shortpulse_engine.h"

#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
typedef SRWLOCK lp_mutex;
#define LP_MUTEX_INIT SRWLOCK_INIT
#define lp_lock(m) AcquireSRWLockExclusive(m)
#define lp_unlock(m) ReleaseSRWLockExclusive(m)
#else
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <unistd.h>
#if defined(__linux__)
#include <sys/random.h>
#endif
typedef pthread_mutex_t lp_mutex;
#define LP_MUTEX_INIT PTHREAD_MUTEX_INITIALIZER
#define lp_lock(m) pthread_mutex_lock(m)
#define lp_unlock(m) pthread_mutex_unlock(m)
#endif

#define LP_VERSION_STR "shortpulse-engine 1.0.0"
#define LP_MAX_KEY_LEN 255

static const char B62[] =
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

const char *lp_version(void) { return LP_VERSION_STR; }

/* ========================================================================
 * Randomness: OS CSPRNG. Short codes must not be predictable, otherwise
 * anyone could enumerate every link on the platform.
 * ======================================================================== */
static int lp_random_bytes(unsigned char *buf, size_t n) {
#if defined(_WIN32)
    /* System-preferred CSPRNG; returns an NTSTATUS (>= 0 means success). */
    return BCryptGenRandom(NULL, buf, (ULONG)n, BCRYPT_USE_SYSTEM_PREFERRED_RNG) >= 0 ? 0 : -1;
#elif defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD__)
    arc4random_buf(buf, n);
    return 0;
#else
    size_t got = 0;
#if defined(__linux__)
    while (got < n) {
        ssize_t r = getrandom(buf + got, n - got, 0);
        if (r < 0) {
            if (errno == EINTR) continue;
            break; /* fall back to /dev/urandom */
        }
        got += (size_t)r;
    }
    if (got == n) return 0;
#endif
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd < 0) return -1;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r <= 0) {
            if (r < 0 && errno == EINTR) continue;
            close(fd);
            return -1;
        }
        got += (size_t)r;
    }
    close(fd);
    return 0;
#endif
}

/* ========================================================================
 * Hashing
 * ======================================================================== */
static uint64_t fnv1a(const char *s, int case_insensitive) {
    uint64_t h = 1469598103934665603ULL;
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (case_insensitive && c >= 'A' && c <= 'Z') c = (unsigned char)(c - 'A' + 'a');
        h ^= c;
        h *= 1099511628211ULL;
    }
    return h;
}

static size_t next_pow2(size_t n) {
    size_t p = 16;
    while (p < n) p <<= 1;
    return p;
}

static char *dup_str(const char *s) {
    size_t n = strlen(s);
    char *d = (char *)malloc(n + 1);
    if (d) memcpy(d, s, n + 1);
    return d;
}

/* ========================================================================
 * String set (separate chaining, power-of-two slots, resize at 0.75 load)
 * Used for the alias table (case-sensitive) and reserved words (insensitive).
 * ======================================================================== */
typedef struct str_node {
    char *key;
    struct str_node *next;
} str_node;

typedef struct {
    str_node **slots;
    size_t nslots;
    size_t count;
    int ci; /* case-insensitive */
} str_set;

/* ASCII-only case-insensitive compare (portable: no strcasecmp/_stricmp). */
static int ascii_ieq(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        unsigned char x = (unsigned char)*a, y = (unsigned char)*b;
        if (x >= 'A' && x <= 'Z') x = (unsigned char)(x - 'A' + 'a');
        if (y >= 'A' && y <= 'Z') y = (unsigned char)(y - 'A' + 'a');
        if (x != y) return 0;
    }
    return *a == *b;
}

static int key_eq(const str_set *s, const char *a, const char *b) {
    return s->ci ? ascii_ieq(a, b) : strcmp(a, b) == 0;
}

static int set_init(str_set *s, size_t capacity, int ci) {
    s->nslots = next_pow2(capacity < 16 ? 16 : capacity);
    s->slots = (str_node **)calloc(s->nslots, sizeof(str_node *));
    s->count = 0;
    s->ci = ci;
    return s->slots ? 0 : -1;
}

static void set_free(str_set *s) {
    if (!s->slots) return;
    for (size_t i = 0; i < s->nslots; i++) {
        str_node *n = s->slots[i];
        while (n) {
            str_node *nx = n->next;
            free(n->key);
            free(n);
            n = nx;
        }
    }
    free(s->slots);
    s->slots = NULL;
    s->nslots = s->count = 0;
}

static int set_contains(const str_set *s, const char *key) {
    if (!s->slots) return 0;
    size_t i = (size_t)(fnv1a(key, s->ci) & (s->nslots - 1));
    for (str_node *n = s->slots[i]; n; n = n->next)
        if (key_eq(s, n->key, key)) return 1;
    return 0;
}

static int set_grow(str_set *s) {
    size_t nn = s->nslots * 2;
    str_node **ns = (str_node **)calloc(nn, sizeof(str_node *));
    if (!ns) return -1;
    for (size_t i = 0; i < s->nslots; i++) {
        str_node *n = s->slots[i];
        while (n) {
            str_node *nx = n->next;
            size_t j = (size_t)(fnv1a(n->key, s->ci) & (nn - 1));
            n->next = ns[j];
            ns[j] = n;
            n = nx;
        }
    }
    free(s->slots);
    s->slots = ns;
    s->nslots = nn;
    return 0;
}

/* 1 = added, 0 = already present, -1 = error */
static int set_add(str_set *s, const char *key) {
    if (!s->slots || !key || !*key || strlen(key) > LP_MAX_KEY_LEN) return -1;
    if (set_contains(s, key)) return 0;
    if ((s->count + 1) * 4 > s->nslots * 3 && set_grow(s) != 0) return -1;
    str_node *n = (str_node *)malloc(sizeof(str_node));
    if (!n) return -1;
    n->key = dup_str(key);
    if (!n->key) {
        free(n);
        return -1;
    }
    size_t i = (size_t)(fnv1a(key, s->ci) & (s->nslots - 1));
    n->next = s->slots[i];
    s->slots[i] = n;
    s->count++;
    return 1;
}

static int set_remove(str_set *s, const char *key) {
    if (!s->slots || !key) return 0;
    size_t i = (size_t)(fnv1a(key, s->ci) & (s->nslots - 1));
    str_node **pp = &s->slots[i];
    while (*pp) {
        if (key_eq(s, (*pp)->key, key)) {
            str_node *dead = *pp;
            *pp = dead->next;
            free(dead->key);
            free(dead);
            s->count--;
            return 1;
        }
        pp = &(*pp)->next;
    }
    return 0;
}

/* ========================================================================
 * Global state
 * ======================================================================== */
typedef struct bucket {
    char *key;
    double tokens;
    double capacity;
    double rate;
    int64_t last_ms;
    struct bucket *next;
} bucket;

static lp_mutex init_mu = LP_MUTEX_INIT;
static lp_mutex alias_mu = LP_MUTEX_INIT;
static lp_mutex rate_mu = LP_MUTEX_INIT;
static int initialized = 0;

static str_set aliases;
static str_set reserved;

static bucket **rl_slots = NULL;
static size_t rl_nslots = 0;
static size_t rl_count = 0;
static size_t rl_max = 0;

static const char *BUILTIN_RESERVED[] = {
    "admin", "api", "static", "login", "logout", "register", "dashboard",
    "links", "campaigns", "import", "healthz", "favicon.ico", "robots.txt",
    NULL};

int lp_init(size_t alias_capacity, size_t rate_max_keys) {
    lp_lock(&init_mu);
    if (initialized) {
        lp_unlock(&init_mu);
        return 0;
    }
    int rc = 0;
    if (set_init(&aliases, alias_capacity, 0) != 0) rc = -1;
    if (rc == 0 && set_init(&reserved, 64, 1) != 0) rc = -1;
    if (rc == 0) {
        rl_max = rate_max_keys ? rate_max_keys : 100000;
        rl_nslots = next_pow2(rl_max);
        rl_slots = (bucket **)calloc(rl_nslots, sizeof(bucket *));
        if (!rl_slots) rc = -1;
    }
    if (rc == 0) {
        for (int i = 0; BUILTIN_RESERVED[i]; i++) set_add(&reserved, BUILTIN_RESERVED[i]);
        rl_count = 0;
        __atomic_store_n(&initialized, 1, __ATOMIC_RELEASE);
    } else {
        set_free(&aliases);
        set_free(&reserved);
        free(rl_slots);
        rl_slots = NULL;
    }
    lp_unlock(&init_mu);
    return rc;
}

static void ensure_init(void) {
    /* Atomic read: callers may race with lp_init on first use. */
    if (!__atomic_load_n(&initialized, __ATOMIC_ACQUIRE)) lp_init(1024, 100000);
}

static void rate_free_all(void) {
    if (!rl_slots) return;
    for (size_t i = 0; i < rl_nslots; i++) {
        bucket *b = rl_slots[i];
        while (b) {
            bucket *nx = b->next;
            free(b->key);
            free(b);
            b = nx;
        }
        rl_slots[i] = NULL;
    }
    rl_count = 0;
}

void lp_shutdown(void) {
    lp_lock(&init_mu);
    lp_lock(&alias_mu);
    lp_lock(&rate_mu);
    set_free(&aliases);
    set_free(&reserved);
    rate_free_all();
    free(rl_slots);
    rl_slots = NULL;
    rl_nslots = 0;
    __atomic_store_n(&initialized, 0, __ATOMIC_RELEASE);
    lp_unlock(&rate_mu);
    lp_unlock(&alias_mu);
    lp_unlock(&init_mu);
}

/* ========================================================================
 * Base62
 * ======================================================================== */
int lp_base62_encode(uint64_t value, char *out, size_t out_size) {
    char tmp[16]; /* 62^11 > 2^64, so 11 digits always suffice */
    int n = 0;
    do {
        tmp[n++] = B62[value % 62];
        value /= 62;
    } while (value > 0);
    if (!out || out_size < (size_t)n + 1) return -1;
    for (int i = 0; i < n; i++) out[i] = tmp[n - 1 - i];
    out[n] = '\0';
    return n;
}

static int b62_index(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'Z') return c - 'A' + 10;
    if (c >= 'a' && c <= 'z') return c - 'a' + 36;
    return -1;
}

int lp_base62_decode(const char *s, uint64_t *out) {
    if (!s || !*s || !out) return -1;
    uint64_t v = 0;
    for (; *s; s++) {
        int d = b62_index(*s);
        if (d < 0) return -1;
        if (v > (UINT64_MAX - (uint64_t)d) / 62) return -1; /* overflow */
        v = v * 62 + (uint64_t)d;
    }
    *out = v;
    return 0;
}

int lp_generate_code(char *out, size_t length) {
    if (!out || length == 0 || length > 64) return -1;
    ensure_init();
    unsigned char rnd[128];
    for (int attempt = 0; attempt < 16; attempt++) {
        size_t filled = 0;
        while (filled < length) {
            if (lp_random_bytes(rnd, sizeof rnd) != 0) return -1;
            for (size_t i = 0; i < sizeof rnd && filled < length; i++) {
                /* 248 = 62 * 4: rejecting 248..255 removes modulo bias. */
                if (rnd[i] < 248) out[filled++] = B62[rnd[i] % 62];
            }
        }
        out[length] = '\0';
        lp_lock(&alias_mu);
        int clash = set_contains(&aliases, out) || set_contains(&reserved, out);
        lp_unlock(&alias_mu);
        if (!clash) return 0;
    }
    return -1;
}

/* ========================================================================
 * Aliases
 * ======================================================================== */
static int alias_char_ok(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') || c == '-' || c == '_';
}

int lp_alias_validate(const char *alias) {
    ensure_init();
    if (!alias) return LP_ALIAS_TOO_SHORT;
    size_t n = strlen(alias);
    if (n < LP_ALIAS_MIN_LEN) return LP_ALIAS_TOO_SHORT;
    if (n > LP_ALIAS_MAX_LEN) return LP_ALIAS_TOO_LONG;
    for (size_t i = 0; i < n; i++)
        if (!alias_char_ok(alias[i])) return LP_ALIAS_BAD_CHARS;
    lp_lock(&alias_mu);
    int result = LP_ALIAS_OK;
    if (set_contains(&reserved, alias))
        result = LP_ALIAS_RESERVED;
    else if (set_contains(&aliases, alias))
        result = LP_ALIAS_TAKEN;
    lp_unlock(&alias_mu);
    return result;
}

int lp_alias_add(const char *alias) {
    ensure_init();
    lp_lock(&alias_mu);
    int r = set_add(&aliases, alias);
    lp_unlock(&alias_mu);
    return r;
}

int lp_alias_remove(const char *alias) {
    ensure_init();
    lp_lock(&alias_mu);
    int r = set_remove(&aliases, alias);
    lp_unlock(&alias_mu);
    return r;
}

int lp_alias_exists(const char *alias) {
    ensure_init();
    if (!alias) return 0;
    lp_lock(&alias_mu);
    int r = set_contains(&aliases, alias);
    lp_unlock(&alias_mu);
    return r;
}

size_t lp_alias_count(void) {
    ensure_init();
    lp_lock(&alias_mu);
    size_t c = aliases.count;
    lp_unlock(&alias_mu);
    return c;
}

int lp_reserved_add(const char *word) {
    ensure_init();
    lp_lock(&alias_mu);
    int r = set_add(&reserved, word);
    lp_unlock(&alias_mu);
    return r;
}

/* ========================================================================
 * Rule order: status -> expiry -> click limit -> password.
 * Password is last on purpose: a dead link never asks for a password, so it
 * cannot be used as a password-guessing oracle.
 * ======================================================================== */
int lp_check_rules(int status, int64_t expires_at, int64_t now,
                   int64_t clicks, int64_t max_clicks,
                   int has_password, int password_state) {
    if (status == LP_STATUS_PAUSED) return LP_DENY_PAUSED;
    if (status != LP_STATUS_ACTIVE) return LP_DENY_DISABLED; /* fail closed */
    if (expires_at > 0 && now >= expires_at) return LP_DENY_EXPIRED;
    if (max_clicks > 0 && clicks >= max_clicks) return LP_DENY_LIMIT;
    if (has_password) {
        if (password_state == LP_PW_CORRECT) return LP_ALLOW;
        if (password_state == LP_PW_WRONG) return LP_BAD_PASSWORD;
        return LP_NEED_PASSWORD;
    }
    return LP_ALLOW;
}

/* ========================================================================
 * Token bucket rate limiter
 * ======================================================================== */
static double refill(const bucket *b, int64_t now_ms) {
    int64_t dt = now_ms - b->last_ms;
    if (dt < 0) dt = 0; /* clock went backwards: no refill, no crash */
    double t = b->tokens + ((double)dt / 1000.0) * b->rate;
    return t > b->capacity ? b->capacity : t;
}

/* Called with rate_mu held when the table is full. A bucket that has refilled
 * to capacity behaves exactly like a missing bucket, so dropping it is free.
 * If nothing is idle, evict the least recently used bucket. */
static void rate_sweep(int64_t now_ms) {
    bucket *lru = NULL;
    size_t lru_slot = 0;
    for (size_t i = 0; i < rl_nslots; i++) {
        bucket **pp = &rl_slots[i];
        while (*pp) {
            bucket *b = *pp;
            if (refill(b, now_ms) >= b->capacity) {
                *pp = b->next;
                free(b->key);
                free(b);
                rl_count--;
                continue;
            }
            if (!lru || b->last_ms < lru->last_ms) {
                lru = b;
                lru_slot = i;
            }
            pp = &b->next;
        }
    }
    if (rl_count >= rl_max && lru) {
        bucket **pp = &rl_slots[lru_slot];
        while (*pp && *pp != lru) pp = &(*pp)->next;
        if (*pp) {
            *pp = lru->next;
            free(lru->key);
            free(lru);
            rl_count--;
        }
    }
}

int64_t lp_rate_take(const char *key, double capacity, double refill_per_sec,
                     int64_t now_ms) {
    ensure_init();
    if (!key || !*key || capacity < 1.0) return 1000;
    if (refill_per_sec < 0) refill_per_sec = 0;

    lp_lock(&rate_mu);
    size_t i = (size_t)(fnv1a(key, 0) & (rl_nslots - 1));
    bucket *b = rl_slots[i];
    while (b && strcmp(b->key, key) != 0) b = b->next;

    if (!b) {
        if (rl_count >= rl_max) {
            rate_sweep(now_ms);
            i = (size_t)(fnv1a(key, 0) & (rl_nslots - 1));
        }
        b = (bucket *)malloc(sizeof(bucket));
        if (!b || !(b->key = dup_str(key))) {
            free(b);
            lp_unlock(&rate_mu);
            return 1000; /* fail closed on OOM */
        }
        b->tokens = capacity;
        b->last_ms = now_ms;
        b->next = rl_slots[i];
        rl_slots[i] = b;
        rl_count++;
    }
    b->capacity = capacity;
    b->rate = refill_per_sec;
    b->tokens = refill(b, now_ms);
    if (now_ms > b->last_ms) b->last_ms = now_ms;

    int64_t wait_ms = 0;
    if (b->tokens >= 1.0) {
        b->tokens -= 1.0;
    } else if (b->rate <= 0.0) {
        wait_ms = INT64_MAX / 4; /* never refills */
    } else {
        double w = (1.0 - b->tokens) / b->rate * 1000.0;
        wait_ms = (int64_t)w + 1;
    }
    lp_unlock(&rate_mu);
    return wait_ms;
}

size_t lp_rate_key_count(void) {
    ensure_init();
    lp_lock(&rate_mu);
    size_t c = rl_count;
    lp_unlock(&rate_mu);
    return c;
}

void lp_rate_reset(void) {
    ensure_init();
    lp_lock(&rate_mu);
    rate_free_all();
    lp_unlock(&rate_mu);
}
