#!/usr/bin/env python3
"""Check production discard/zero submission with synchronous and delayed IO."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_client_timeouts import ROOT


def function_source(name):
    source = (ROOT / "kernel/mars_if.c").read_text()
    match = re.search(rf"void {name}\([^;]*?\)\n\{{", source)
    if not match:
        raise ValueError(name)
    start = match.start()
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


MOCKS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#define loff_t long long
#define PAGE_SIZE 4096
#define MREF_WRITE 1
#define MREF_MAY_WRITE 2
#define MREF_DISCARD 4
#define REQ_OP_DISCARD 3
#define REQ_OP_WRITE_ZEROES 9
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define atomic_set(p, n) (*(p) = (n))
#define atomic_inc(p) (++*(p))
#define atomic_dec(p) (--*(p))
#define atomic_dec_and_test(p) (--*(p) == 0)
#define READ_ONCE(x) (x)
#define unlikely(x) (x)
#define MARS_FAT(...) assert(0)
#define MARS_IO(...) ((void)0)
#define mars_trace(...) ((void)0)
#define mars_log_trace(...) ((void)0)
#define trace_mars_request(...) ((void)0)
#define LAST_CALLBACK(cb) ((void)(cb))
#define CHECK_PTR(p, label) do { if (!(p)) { assert(p); goto label; } } while (0)
struct if_input;
struct request_queue { struct if_input *queuedata; };
struct block_device { struct request_queue *queue; };
struct bio {
    struct block_device *bi_bdev;
    struct { unsigned long long bi_sector; unsigned int bi_size; } bi_iter;
    int op, completions, error;
};
struct bio_wrapper { struct bio *bio; int bi_comp_cnt, error; };
struct if_brick {
    const char *brick_name;
    struct { bool led_on; } power;
    int error_code, write_flying_count, read_flying_count;
};
struct if_input { struct if_brick *brick; };
struct generic_callback { void *cb_private; int cb_error; };
struct mref_object;
struct if_mref_aspect {
    struct if_input *input;
    struct mref_object *object;
    int bio_count;
    struct bio_wrapper *orig_biow[8];
};
struct mref_object {
    struct if_mref_aspect aspect;
    loff_t ref_pos;
    int ref_len, ref_flags, refs;
    void *ref_data;
    char data[4096];
    struct generic_callback cb;
    void (*endio)(struct generic_callback *);
};
static int allocations, frees, wrapper_count, gets, ios, starts, ends, unplugs;
static int alloc_failure, get_failure, invalid_length, error_io;
static bool deferred, issuing, wrapper_failure;
static struct mref_object *pending[8];
static struct bio *active_bio;
static int cmpxchg(int *p, int old, int value) {
    int prev = *p; if (prev == old) *p = value; return prev;
}
static struct request_queue *bdev_get_queue(struct block_device *bdev) { return bdev->queue; }
static int bio_op(struct bio *bio) { return bio->op; }
static void _if_unplug(struct if_input *input) { (void)input; unplugs++; }
static void *brick_zmem_alloc(size_t size) {
    if (wrapper_failure) return NULL;
    wrapper_count++; void *p = calloc(1, size); assert(p); return p;
}
static void brick_mem_free(void *p) { assert(wrapper_count == 1); wrapper_count--; free(p); }
static void _if_start_io_acct(struct if_input *input, struct bio_wrapper *wrapper) {
    (void)input; assert(wrapper->bio == active_bio); starts++;
}
static void _if_end_io_acct(struct if_input *input, struct bio_wrapper *wrapper) {
    (void)input; assert(wrapper->bio == active_bio && starts == 1 && ends == 0); ends++;
}
static void _call_bio_endio(struct if_brick *brick, struct bio *bio, int error) {
    (void)brick; assert(!issuing && bio->completions == 0);
    bio->completions++; bio->error = error;
}
static struct mref_object *if_alloc_mref(struct if_brick *brick) {
    (void)brick;
    if (alloc_failure == allocations + 1) return NULL;
    struct mref_object *m = calloc(1, sizeof(*m)); assert(m); allocations++;
    m->aspect.object = m; return m;
}
static struct if_mref_aspect *if_mref_get_aspect(struct if_brick *brick, struct mref_object *m) {
    (void)brick; return &m->aspect;
}
static void if_free_mref(struct mref_object *m) { assert(!m->refs); frees++; free(m); }
static int backend_get(struct if_input *input, struct mref_object *m) {
    (void)input; gets++; assert(!m->refs);
    if (get_failure == gets) return -ENOSPC;
    m->refs = 1; m->ref_data = m->data;
    m->ref_len = invalid_length == gets ? 0 : 1024;
    return m->ref_len;
}
static void backend_put(struct if_input *input, struct mref_object *m) {
    (void)input; assert(m->refs > 0);
    if (!--m->refs) if_free_mref(m);
}
static void complete(struct mref_object *m, int error) {
    m->cb.cb_error = error; m->endio(&m->cb);
    backend_put(m->aspect.input, m);
}
static void backend_io(struct if_input *input, struct mref_object *m) {
    (void)input;
    assert(m->refs == 1 && (m->ref_flags & MREF_WRITE));
    assert(!!(m->ref_flags & MREF_DISCARD) == (active_bio->op == REQ_OP_DISCARD));
    for (int i = 0; i < m->ref_len; i++) assert(!m->data[i]);
    m->refs++; issuing = true;
    if (deferred) pending[ios] = m;
    ios++;
    if (!deferred) complete(m, ios == error_io ? -EIO : 0);
    issuing = false;
}
#define CALLBACK_ERROR(m) ((m)->cb.cb_error)
#define SETUP_CALLBACK(m, fn, a) do { (m)->endio = fn; (m)->cb.cb_private = a; } while (0)
#define GENERIC_INPUT_CALL(input, op, m) backend_get(input, m)
#define GENERIC_INPUT_CALL_VOID(input, op, m) backend_##op(input, m)
#define backend_mref_io backend_io
#define backend_mref_put backend_put
'''

MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    struct if_brick brick = { .brick_name = "test", .power.led_on = true };
    struct if_input input = { .brick = &brick };
    struct request_queue queue = { .queuedata = &input };
    struct block_device bdev = { .queue = &queue };
    struct bio bio = { .bi_bdev = &bdev, .bi_iter.bi_size = 4096, .op = REQ_OP_DISCARD };
    int expected = 0;
    active_bio = &bio;
    if (!strcmp(argv[1], "sync")) {}
    else if (!strcmp(argv[1], "zeroes")) bio.op = REQ_OP_WRITE_ZEROES;
    else if (!strcmp(argv[1], "deferred")) deferred = true;
    else if (!strcmp(argv[1], "sync_error")) { error_io = 1; expected = -EIO; }
    else if (!strcmp(argv[1], "deferred_error")) { deferred = true; error_io = 1; expected = -EIO; }
    else if (!strcmp(argv[1], "alloc_error")) { alloc_failure = 2; expected = -ENOMEM; }
    else if (!strcmp(argv[1], "get_error")) { get_failure = 2; expected = -ENOSPC; }
    else if (!strcmp(argv[1], "invalid_length")) { invalid_length = 2; expected = -EIO; }
    else if (!strcmp(argv[1], "partial_deferred")) { deferred = true; alloc_failure = 2; expected = -ENOMEM; }
    else if (!strcmp(argv[1], "off")) { brick.power.led_on = false; expected = -ESHUTDOWN; }
    else if (!strcmp(argv[1], "wrapper_error")) { wrapper_failure = true; expected = -ENOMEM; }
    else assert(0);
    if_submit_zeroes(&bio);
    if (deferred) {
        assert(bio.completions == 0 && wrapper_count == 1);
        /* Complete an early segment with error, then later successes. */
        for (int i = 0; i < ios; i++) complete(pending[i], i + 1 == error_io ? -EIO : 0);
    }
    assert(bio.completions == 1 && bio.error == expected && !wrapper_count);
    assert(frees == allocations && starts == ends && unplugs == 1);
    assert(!brick.write_flying_count && !brick.read_flying_count);
    if (!expected) assert(ios == 4);
    return 0;
}
'''


class FrontendCompletion(unittest.TestCase):
    def test_production_zeroes_completion(self):
        code = MOCKS + "\n".join(function_source(f) for f in [
            "if_complete_biow", "if_endio", "if_submit_zeroes"]) + MAIN
        with tempfile.TemporaryDirectory(prefix="mars-frontend-test-") as directory:
            source = Path(directory) / "frontend.c"
            binary = Path(directory) / "frontend"
            source.write_text(code)
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(source), "-o", str(binary)], check=True)
            for scenario in ["sync", "zeroes", "deferred", "sync_error",
                             "deferred_error", "alloc_error", "get_error",
                             "invalid_length", "partial_deferred", "off", "wrapper_error"]:
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), scenario], check=True)


if __name__ == "__main__":
    unittest.main()
