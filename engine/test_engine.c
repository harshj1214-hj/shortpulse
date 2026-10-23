/* Unit + concurrency tests for the ShortPulse C engine.  Run: make -C engine test */
#include "shortpulse_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
typedef HANDLE thread_t;
#define THREAD_FN(name) static DWORD WINAPI name(LPVOID arg)
#define THREAD_RETURN return 0
#define thread_start(t, fn, a) (*(t) = CreateThread(NULL, 0, fn, a, 0, NULL))
#define thread_join(t) (WaitForSingleObject(t, INFINITE), CloseHandle(t))
#else
#include <pthread.h>
typedef pthread_t thread_t;
#define THREAD_FN(name) static void *name(void *arg)
#define THREAD_RETURN return NULL
#define thread_start(t, fn, a) pthread_create(t, NULL, fn, a)
#define thread_join(t) pthread_join(t, NULL)
#endif

static int failures = 0;
#define CHECK(cond, msg)                                                   \
    do {                                                                   \
        if (!(cond)) {                                                     \
            fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, msg);  \
            failures++;                                                    \
        }                                                                  \
    } while (0)

static void test_base62(void) {
    char buf[16];
    uint64_t back = 0;
    uint64_t samples[] = {0, 1, 61, 62, 3843, 3844, 123456789ULL, UINT64_MAX};
    for (size_t i = 0; i < sizeof samples / sizeof samples[0]; i++) {
        CHECK(lp_base62_encode(samples[i], buf, sizeof buf) > 0, "encode");
        CHECK(lp_base62_decode(buf, &back) == 0 && back == samples[i], "roundtrip");
    }
    CHECK(lp_base62_encode(0, buf, sizeof buf) == 1 && strcmp(buf, "0") == 0, "zero");
    CHECK(lp_base62_encode(62, buf, sizeof buf) == 2 && strcmp(buf, "10") == 0, "62 -> 10");
    CHECK(lp_base62_decode("ab!c", &back) == -1, "reject bad char");
    CHECK(lp_base62_decode("zzzzzzzzzzzzzz", &back) == -1, "reject overflow");
    CHECK(lp_base62_encode(UINT64_MAX, buf, 4) == -1, "small buffer");
}

static void test_generate(void) {
    char code[8];
    for (int i = 0; i < 20000; i++) {
        CHECK(lp_generate_code(code, 7) == 0, "generate");
        CHECK(strlen(code) == 7, "length");
        for (int j = 0; j < 7; j++) {
            char c = code[j];
            int ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
            CHECK(ok, "charset");
        }
        /* 20000 codes out of 62^7: a duplicate here would indicate a broken RNG. */
        CHECK(lp_alias_add(code) == 1, "unique generated code");
    }
    CHECK(lp_generate_code(code, 0) == -1, "length 0 rejected");
}

static void test_alias(void) {
    CHECK(lp_alias_validate("ab") == LP_ALIAS_TOO_SHORT, "too short");
    CHECK(lp_alias_validate("abcdefghijklmnopqrstuvwxyz0123456") == LP_ALIAS_TOO_LONG, "too long");
    CHECK(lp_alias_validate("diwali sale") == LP_ALIAS_BAD_CHARS, "space");
    CHECK(lp_alias_validate("diwali/sale") == LP_ALIAS_BAD_CHARS, "slash");
    CHECK(lp_alias_validate("ADMIN") == LP_ALIAS_RESERVED, "reserved is case-insensitive");
    CHECK(lp_reserved_add("settings") == 1, "reserve new word");
    CHECK(lp_alias_validate("Settings") == LP_ALIAS_RESERVED, "runtime reserved word");
    CHECK(lp_alias_validate("diwali-sale_26") == LP_ALIAS_OK, "valid");
    CHECK(lp_alias_add("diwali-sale_26") == 1, "add");
    CHECK(lp_alias_add("diwali-sale_26") == 0, "add twice");
    CHECK(lp_alias_validate("diwali-sale_26") == LP_ALIAS_TAKEN, "taken");
    CHECK(lp_alias_validate("Diwali-Sale_26") == LP_ALIAS_OK, "aliases are case-sensitive");
    CHECK(lp_alias_remove("diwali-sale_26") == 1, "remove");
    CHECK(lp_alias_validate("diwali-sale_26") == LP_ALIAS_OK, "free again");
}

static void test_rules(void) {
    const int64_t now = 1000000, past = now - 1, future = now + 100;
    /* Order matters: each case has several failing rules; the first one wins. */
    CHECK(lp_check_rules(LP_STATUS_DISABLED, past, now, 10, 5, 1, LP_PW_CORRECT) == LP_DENY_DISABLED, "status first");
    CHECK(lp_check_rules(LP_STATUS_PAUSED, past, now, 10, 5, 1, LP_PW_CORRECT) == LP_DENY_PAUSED, "paused");
    CHECK(lp_check_rules(LP_STATUS_ACTIVE, past, now, 10, 5, 1, LP_PW_CORRECT) == LP_DENY_EXPIRED, "expiry before limit");
    CHECK(lp_check_rules(LP_STATUS_ACTIVE, now, now, 0, 0, 0, LP_PW_NOT_SUPPLIED) == LP_DENY_EXPIRED, "expires exactly now");
    CHECK(lp_check_rules(LP_STATUS_ACTIVE, future, now, 5, 5, 1, LP_PW_NOT_SUPPLIED) == LP_DENY_LIMIT, "limit before password");
    CHECK(lp_check_rules(LP_STATUS_ACTIVE, future, now, 4, 5, 1, LP_PW_NOT_SUPPLIED) == LP_NEED_PASSWORD, "need password");
    CHECK(lp_check_rules(LP_STATUS_ACTIVE, future, now, 4, 5, 1, LP_PW_WRONG) == LP_BAD_PASSWORD, "bad password");
    CHECK(lp_check_rules(LP_STATUS_ACTIVE, future, now, 4, 5, 1, LP_PW_CORRECT) == LP_ALLOW, "allow with password");
    CHECK(lp_check_rules(LP_STATUS_ACTIVE, 0, now, 999, 0, 0, LP_PW_NOT_SUPPLIED) == LP_ALLOW, "no rules");
    CHECK(lp_check_rules(42, 0, now, 0, 0, 0, LP_PW_NOT_SUPPLIED) == LP_DENY_DISABLED, "unknown status fails closed");
}

static void test_rate(void) {
    lp_rate_reset();
    int64_t t = 1000000;
    for (int i = 0; i < 5; i++) CHECK(lp_rate_take("create:1", 5, 1.0, t) == 0, "burst allowed");
    int64_t wait = lp_rate_take("create:1", 5, 1.0, t);
    CHECK(wait > 0 && wait <= 1001, "6th denied with ~1s wait");
    CHECK(lp_rate_take("create:2", 5, 1.0, t) == 0, "other key independent");
    CHECK(lp_rate_take("create:1", 5, 1.0, t + 1000) == 0, "refilled after 1s");
    CHECK(lp_rate_take("create:1", 5, 1.0, t + 1000) > 0, "only one token refilled");
    CHECK(lp_rate_take("create:1", 5, 1.0, t - 5000) > 0, "clock going backwards gives no tokens");
}

/* ---- concurrency ------------------------------------------------------- */
#define THREADS 8
static int allowed_count[THREADS];

THREAD_FN(rate_worker) {
    int id = *(int *)arg;
    for (int i = 0; i < 1000; i++)
        if (lp_rate_take("shared-bucket", 100, 0.0, 5000) == 0) allowed_count[id]++;
    THREAD_RETURN;
}

THREAD_FN(alias_worker) {
    (void)arg;
    char key[32];
    for (int i = 0; i < 5000; i++) {
        snprintf(key, sizeof key, "conc-%d", i); /* every thread adds the same keys */
        lp_alias_add(key);
    }
    THREAD_RETURN;
}

static void test_concurrency(void) {
    thread_t th[THREADS];
    int ids[THREADS];
    lp_rate_reset();
    for (int i = 0; i < THREADS; i++) {
        ids[i] = i;
        allowed_count[i] = 0;
        thread_start(&th[i], rate_worker, &ids[i]);
    }
    int total = 0;
    for (int i = 0; i < THREADS; i++) {
        thread_join(th[i]);
        total += allowed_count[i];
    }
    CHECK(total == 100, "exactly capacity tokens granted across 8 threads");

    size_t before = lp_alias_count();
    for (int i = 0; i < THREADS; i++) thread_start(&th[i], alias_worker, NULL);
    for (int i = 0; i < THREADS; i++) thread_join(th[i]);
    CHECK(lp_alias_count() == before + 5000, "no lost or duplicated aliases under contention");
}

static void test_rate_eviction(void) {
    lp_shutdown();
    lp_init(64, 50); /* tiny limiter table */
    char key[32];
    for (int i = 0; i < 500; i++) {
        snprintf(key, sizeof key, "ip-%d", i);
        lp_rate_take(key, 3, 1.0, 1000 + i);
    }
    CHECK(lp_rate_key_count() <= 50, "limiter memory is bounded");
}

int main(void) {
    CHECK(lp_init(1024, 1000) == 0, "init");
    test_base62();
    test_generate();
    test_alias();
    test_rules();
    test_rate();
    test_concurrency();
    test_rate_eviction();
    lp_shutdown();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("All C engine tests passed (%s)\n", lp_version());
    return 0;
}
