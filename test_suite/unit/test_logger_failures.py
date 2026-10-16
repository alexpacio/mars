#!/usr/bin/env python3
"""Run production logger completion paths against checked lifetime mocks.

The mocks validate ownership, queue accounting and recovery positions. Kernel
fault injection remains necessary to validate real asynchronous backends.
"""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from test_client_timeouts import PRELUDE, ROOT


def function_source(name):
    source = (ROOT / "kernel/mars_trans_logger.c").read_text()
    match = re.search(rf"(?:void|bool) {name}\([^;]*?\)\n\{{", source)
    if not match:
        raise ValueError(name)
    start = match.start()
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


MOCKS = PRELUDE.split("typedef int atomic_t;")[0] + r'''
#define loff_t long long
typedef int atomic_t;
#define atomic_inc(p) (++*(p))
#define atomic_dec(p) (--*(p))
#define atomic_read(p) (*(p))
#define atomic_dec_and_test(p) (--*(p) == 0)
#define READ_ONCE(x) (x)
#define likely(x) (x)
#define unlikely(x) (x)
#define CHECK_PTR(p, label) do { if (!(p)) { assert(p); goto label; } } while (0)
#define _CHECK(p, label) CHECK_PTR(p, label)
#define CHECK_ATOMIC(p, minimum) assert(*(p) >= (minimum))
#define MARS_ERR(...) ((void)0)
#define MARS_IO(...) ((void)0)
#define MARS_FAT(...) assert(0)
#define MREF_WRITE 1
#define LAST_CALLBACK(cb) ((void)(cb))
#define list_first_entry(head, type, field) list_entry((head)->next, type, field)
static int cmpxchg_int(int *p, int old, int value) {
    int previous = *p; if (previous == old) *p = value; return previous;
}
static bool cmpxchg_bool(bool *p, bool old, bool value) {
    bool previous = *p; if (previous == old) *p = value; return previous;
}
#define cmpxchg(p, old, value) _Generic((p), int *: cmpxchg_int, bool *: cmpxchg_bool)(p, old, value)
static void brick_wake_flagged(int *event, int flag) { (void)flag; ++*event; }
static void mars_trigger(void) {}
static void banning_reset(int *ban) { *ban = 0; }
struct generic_callback { void *cb_private; int cb_error; };
struct trans_logger_mref_aspect;
struct mref_object {
    struct trans_logger_mref_aspect *aspect;
    loff_t ref_pos; int ref_len, refs, completions, error;
};
struct logger_queue { int q_active, q_banning, queued; };
struct trans_logger_brick {
    const char *brick_path;
    int fatal_error, worker_event, worker_flag, caller_event, caller_flag;
    int log_fly_count;
    bool log_reads;
    struct logger_queue q_phase[4];
};
struct trans_logger_input {
    struct { loff_t inf_min_pos, inf_max_pos; int inf_min_pos_stamp; } inf;
    int inf_mutex, pos_count, released;
    struct list_head pos_list;
    struct { int posix_error_code; } logst;
};
struct writeback_info {
    struct trans_logger_brick *w_brick;
    loff_t w_pos; int w_len, w_error;
    struct list_head w_collect_list, w_sub_read_list, w_sub_write_list;
    atomic_t w_sub_read_count, w_sub_write_count, w_sub_log_count;
    void (*read_endio)(struct generic_callback *);
    void (*write_endio)(struct generic_callback *);
};
struct trans_logger_mref_aspect {
    struct mref_object *object;
    struct trans_logger_brick *my_brick;
    struct trans_logger_input *my_input, *log_input;
    struct trans_logger_mref_aspect *shadow_ref;
    struct writeback_info *wb;
    struct list_head collect_head, sub_head, pos_head;
    bool is_fired, is_collected, is_hashed, is_endio, is_persistent;
    loff_t log_pos;
    int wb_error, orig_flags;
};
static int freed_batches;
static void brick_mem_free(struct writeback_info *wb) { ++freed_batches; free(wb); }
static void down(int *lock) { assert(!*lock); *lock = 1; }
static void up(int *lock) { assert(*lock); *lock = 0; }
static void get_lamport(void *unused, int *stamp) { (void)unused; ++*stamp; }
static void _inf_callback(struct trans_logger_input *input, bool force) { (void)input; (void)force; }
static void _mref_check(struct mref_object *mref) { assert(mref->refs > 0); }
static void qq_deactivate(struct logger_queue *q) { assert(q->q_active > 0); q->q_active--; }
static void qq_wb_insert(struct logger_queue *q, struct writeback_info *wb) {
    assert(wb->w_error >= 0); q->q_active++; q->queued++;
}
static void qq_mref_insert(struct logger_queue *q, struct trans_logger_mref_aspect *a) {
    q->q_active++; q->queued++; a->object->refs++;
}
static void hash_put_all(struct trans_logger_brick *brick, struct list_head *head) {
    struct trans_logger_mref_aspect *a, *next;
    (void)brick;
    list_for_each_entry_safe(a, next, head, collect_head) a->is_hashed = false;
}
static void unhash_failed_mref(struct trans_logger_brick *b, struct trans_logger_mref_aspect *a) {
    (void)b; assert(!a->is_collected); a->is_hashed = false;
}
static void pos_complete(struct trans_logger_mref_aspect *a);
static void __trans_logger_ref_put(struct trans_logger_brick *b, struct trans_logger_mref_aspect *a) {
    (void)b; assert(a->object->refs > 0);
    if (!--a->object->refs) {
        assert(!a->is_hashed && list_empty(&a->collect_head));
        if (!list_empty(&a->pos_head)) pos_complete(a);
    }
}
static void backend_put(struct trans_logger_input *input, struct mref_object *m) {
    assert(m->refs > 0 && list_empty(&m->aspect->sub_head));
    --m->refs; ++input->released;
}
#define GENERIC_INPUT_CALL_VOID(input, op, mref) backend_put(input, mref)
static void _complete(struct trans_logger_brick *b, struct trans_logger_mref_aspect *a, int error, bool pre) {
    assert(!pre && a->object->completions == 0 && b->log_fly_count == 1);
    b->log_fly_count--; a->object->completions++; a->object->error = error;
}
static void _crashme(int point, bool pre) { (void)point; (void)pre; }
#define mars_trace(...) ((void)0)
'''

SUBMISSION_MOCK = r'''
static int submissions;
static bool _phase2_startio(struct trans_logger_mref_aspect *a) {
    ++submissions;
    if (submissions == 2) return false;
    phase2_endio(a, 0);
    return true;
}
static int fired;
static void _fire_one(struct list_head *entry, bool update) {
    struct trans_logger_mref_aspect *a = container_of(entry, struct trans_logger_mref_aspect, sub_head);
    assert(!update && !a->is_fired);
    a->is_fired = true;
    struct generic_callback cb = { .cb_private = a, .cb_error = ++fired == 1 ? -EIO : 0 };
    wb_endio(&cb);
}
'''

MAIN = r'''
static void init_aspect(struct trans_logger_mref_aspect *a, struct mref_object *m,
                        struct trans_logger_brick *b, struct trans_logger_input *input) {
    memset(a, 0, sizeof(*a)); memset(m, 0, sizeof(*m));
    a->object = m; a->my_brick = b; a->my_input = a->log_input = input;
    a->shadow_ref = a; m->aspect = a; m->refs = 1; m->ref_len = 4096;
    init_list(&a->collect_head); init_list(&a->sub_head); init_list(&a->pos_head);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    struct trans_logger_brick brick = { .brick_path = "/mars/resource-test" };
    struct trans_logger_input input = { .inf = { .inf_min_pos = 10, .inf_max_pos = 1000 } };
    struct trans_logger_mref_aspect orig, sub[3], later;
    struct mref_object orig_ref, sub_ref[3], later_ref;
    init_list(&input.pos_list);
    init_aspect(&orig, &orig_ref, &brick, &input);
    orig.log_pos = 100; orig.is_hashed = true;
    list_add_tail(&orig.pos_head, &input.pos_list); input.pos_count++;
    if (!strcmp(argv[1], "log_write")) {
        orig_ref.refs = 2; brick.log_fly_count = 1; brick.q_phase[0].q_active = 1;
        phase0_endio(&orig, -ENOSPC);
        assert(orig_ref.refs == 0 && orig_ref.completions == 1 && orig_ref.error == -ENOSPC);
        assert(!orig.is_persistent && !orig.is_hashed && brick.q_phase[1].queued == 0);
        assert(brick.q_phase[0].q_active == 0 && brick.log_fly_count == 0);
    } else {
        struct writeback_info *wb = calloc(1, sizeof(*wb));
        assert(wb);
        wb->w_brick = &brick; wb->w_pos = 0; wb->w_len = 4096;
        init_list(&wb->w_collect_list); init_list(&wb->w_sub_read_list); init_list(&wb->w_sub_write_list);
        orig.is_collected = true; list_add_tail(&orig.collect_head, &wb->w_collect_list);
        for (int i = 0; i < 3; i++) {
            init_aspect(&sub[i], &sub_ref[i], &brick, &input);
            sub[i].wb = wb;
        }
        if (!strcmp(argv[1], "preimage_log") || !strcmp(argv[1], "preimage_submit")) {
            brick.q_phase[2].q_active = 1; wb->w_sub_log_count = 2;
            sub[0].is_fired = sub[1].is_fired = true;
            list_add_tail(&sub[0].sub_head, &wb->w_sub_read_list);
            list_add_tail(&sub[1].sub_head, &wb->w_sub_read_list);
            list_add_tail(&sub[2].sub_head, &wb->w_sub_write_list);
            if (!strcmp(argv[1], "preimage_submit")) {
                brick.log_reads = true;
                assert(phase2_startio(wb));
                assert(submissions == 2);
            } else {
                phase2_endio(&sub[0], -EIO);
                assert(freed_batches == 0 && brick.q_phase[2].q_active == 1);
                phase2_endio(&sub[1], 0);
            }
            assert(brick.q_phase[2].q_active == 0 && brick.q_phase[3].queued == 0);
            assert(input.released == 3);
        } else {
            bool fire = !strcmp(argv[1], "preimage_fire");
            bool read = !strcmp(argv[1], "preimage_read") || fire;
            int phase = read ? 1 : 3;
            brick.q_phase[phase].q_active = 1;
            wb->read_endio = phase1_endio; wb->write_endio = phase3_endio;
            if (read) {
                wb->w_sub_read_count = 2;
                list_add_tail(&sub[0].sub_head, &wb->w_sub_read_list);
                list_add_tail(&sub[1].sub_head, &wb->w_sub_read_list);
                list_add_tail(&sub[2].sub_head, &wb->w_sub_write_list);
            } else {
                wb->w_sub_write_count = 2;
                sub[0].orig_flags = sub[1].orig_flags = MREF_WRITE;
            }
            if (fire) {
                fire_writeback(&wb->w_sub_read_list, false);
                assert(fired == 2);
            } else {
                sub[0].is_fired = sub[1].is_fired = true;
                struct generic_callback first = { .cb_private = &sub[0], .cb_error = -EIO };
                struct generic_callback last = { .cb_private = &sub[1], .cb_error = 0 };
                wb_endio(&first);
                assert(freed_batches == 0);
                wb_endio(&last); /* final success must not mask the first failure */
            }
            assert(brick.q_phase[phase].q_active == 0);
            assert(brick.q_phase[2].queued == 0);
            if (read) assert(input.released == 3);
        }
        assert(freed_batches == 1 && orig_ref.refs == 0 && orig.wb_error == -EIO);
    }
    assert(brick.fatal_error < 0 && input.inf.inf_min_pos == 10 && input.pos_count == 0);
    /* An unrelated later success cannot retire the failed log prefix. */
    init_aspect(&later, &later_ref, &brick, &input);
    later.log_pos = 200; list_add_tail(&later.pos_head, &input.pos_list); input.pos_count++;
    pos_complete(&later);
    assert(input.inf.inf_min_pos == 10 && input.pos_count == 0 && list_empty(&input.pos_list));
    return 0;
}
'''


class LoggerFailures(unittest.TestCase):
    def test_production_error_completions(self):
        functions = ("logger_set_error", "pos_complete", "release_writeback_list",
                     "free_writeback", "phase0_endio", "phase1_endio",
                     "_phase2_endio", "phase2_endio", "phase3_endio", "wb_endio")
        code = (MOCKS + "\n".join(function_source(name) for name in functions)
                + SUBMISSION_MOCK + function_source("phase2_startio")
                + function_source("fire_writeback") + MAIN)
        with tempfile.TemporaryDirectory(prefix="mars-logger-test-") as directory:
            source = Path(directory) / "logger.c"
            binary = Path(directory) / "logger"
            source.write_text(code)
            subprocess.run(
                shlex.split(os.environ.get("CC", "cc"))
                + ["-std=gnu11", "-Wall", "-Wextra", "-Werror",
                   "-Wno-unused-function", "-Wno-unused-parameter",
                   "-fsanitize=undefined", str(source), "-o", str(binary)],
                check=True,
            )
            for scenario in ("log_write", "preimage_read", "preimage_fire", "preimage_log", "preimage_submit", "data_write"):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), scenario], check=True)


if __name__ == "__main__":
    unittest.main()
