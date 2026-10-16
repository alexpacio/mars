#!/usr/bin/env python3
"""Exercise real replay admission, checkpoint and allocation cleanup paths."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_client_timeouts import PRELUDE, ROOT


def function_source(name):
    source = (ROOT / "kernel/mars_trans_logger.c").read_text()
    match = re.search(rf"(?:void|bool|int) {name}\([^;]*?\)\n\{{", source)
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
#define REPLAY_DATA
#define HZ 100
#define READ_ONCE(x) (x)
#define likely(x) (x)
#define unlikely(x) (x)
#define atomic_inc(p) (++*(p))
#define atomic_dec(p) (--*(p))
#define atomic_read(p) (*(p))
#define MARS_ERR(...) ((void)0)
#define MARS_IO(...) ((void)0)
#define MARS_FAT(...) assert(0)
#define mars_trace(...) ((void)0)
#define trace_mars_request(...) ((void)0)
#define LAST_CALLBACK(cb) ((void)(cb))
#define CHECK_PTR(p, label) do { if (!(p)) goto label; } while (0)
#define CHECK_ASPECT(a, m, label) do { if ((a)->object != (m)) goto label; } while (0)
#define MREF_WRITE 1
#define MREF_MAY_WRITE 2
#define MREF_DISCARD 4
#define TL_INPUT_WRITEBACK 0
#define TL_INPUT_READ 1
struct trans_logger_brick;
struct mref_object;
struct trans_logger_mref_aspect {
    struct mref_object *object;
    struct trans_logger_brick *my_brick;
    struct list_head replay_head;
};
struct generic_callback { void *cb_private; int cb_error; };
struct mref_object {
    struct trans_logger_mref_aspect aspect;
    loff_t ref_pos; int ref_len, ref_flags, refs;
    void *ref_data;
    char data[4096];
    struct generic_callback cb;
    void (*endio)(struct generic_callback *);
};
struct trans_logger_input {
    bool connect;
    int inf_mutex;
    struct { loff_t inf_min_pos; int inf_min_pos_stamp; } inf;
};
struct trans_logger_brick {
    int replay_count, replay_mutex, disk_io_error, worker_event, worker_flag;
    bool terminate;
    struct list_head replay_list;
    struct trans_logger_input *inputs[2];
};
static int cmpxchg(int *p, int old, int val) {
    int prev = *p; if (prev == old) *p = val; return prev;
}
static void down(int *lock) { assert(!*lock); *lock = 1; }
static void up(int *lock) { assert(*lock); *lock = 0; }
#define down_read down
#define up_read up
#define down_write down
#define up_write up
static bool brick_thread_should_stop(void) { return false; }
#define brick_wait_flagged(event, flag, condition, timeout) \
    do { (void)(event); (void)(flag); (void)(timeout); (void)(condition); } while (0)
#define brick_wake_flagged(event, flag) do { ++*(event); (void)(flag); } while (0)
static void _crashme(int point, bool pre) { (void)point; (void)pre; }
static void list_del(struct list_head *h) { list_del_init(h); }
static void list_add(struct list_head *h, struct list_head *head) {
    h->next = head->next; h->prev = head; head->next->prev = h; head->next = h;
}
static int publishes, allocations, frees, put_count, ios, seen_flags;
static int get_error, io_error, bad_len;
static bool no_data;
static void get_lamport(void *unused, int *stamp) { (void)unused; ++*stamp; }
static void _inf_callback(struct trans_logger_input *input, bool force) {
    assert(input->inf_mutex && !force); publishes++;
}
static struct mref_object *trans_logger_alloc_mref(struct trans_logger_brick *brick) {
    (void)brick;
    struct mref_object *m = calloc(1, sizeof(*m)); assert(m); allocations++;
    m->aspect.object = m; init_list(&m->aspect.replay_head); return m;
}
static struct trans_logger_mref_aspect *trans_logger_mref_get_aspect(
        struct trans_logger_brick *brick, struct mref_object *m) {
    (void)brick; return &m->aspect;
}
static void trans_logger_free_mref(struct mref_object *m) {
    assert(!m->refs && list_empty(&m->aspect.replay_head)); frees++; free(m);
}
static int backend_get(struct trans_logger_input *input, struct mref_object *m) {
    (void)input; assert(!m->refs);
    if (get_error) return get_error;
    m->refs = 1; m->ref_data = no_data ? NULL : m->data;
    m->ref_len = bad_len ? bad_len : (m->ref_len < 4096 ? m->ref_len : 4096);
    return m->ref_len;
}
static void backend_put(struct trans_logger_input *input, struct mref_object *m) {
    (void)input; assert(m->refs > 0); put_count++;
    if (!--m->refs) trans_logger_free_mref(m);
}
static void backend_io(struct trans_logger_input *input, struct mref_object *m) {
    (void)input; assert(m->refs == 1 && !list_empty(&m->aspect.replay_head));
    ios++; seen_flags = m->ref_flags; m->refs++;
    m->cb.cb_error = io_error; m->endio(&m->cb);
    assert(m->refs == 2); m->refs--;
}
#define GENERIC_INPUT_CALL(input, op, m) backend_get(input, m)
#define GENERIC_INPUT_CALL_VOID(input, op, m) backend_##op(input, m)
#define backend_mref_put backend_put
#define backend_mref_io backend_io
#define SETUP_CALLBACK(m, fn, a) do { (m)->endio = fn; (m)->cb.cb_private = a; } while (0)
'''

MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    struct trans_logger_input input = { .connect = true, .inf.inf_min_pos = 10 };
    struct trans_logger_brick b = { .inputs = { &input, &input } };
    struct mref_object previous = { .ref_pos = 0, .ref_len = 4096 };
    struct mref_object candidate = { .ref_pos = 2048, .ref_len = 4096 };
    char payload[4096] = {0};
    init_list(&b.replay_list); init_list(&previous.aspect.replay_head);
    init_list(&candidate.aspect.replay_head);
    previous.aspect.object = &previous; candidate.aspect.object = &candidate;
    if (!strcmp(argv[1], "overlap") || !strcmp(argv[1], "timeout_cleanup")) {
        list_add(&previous.aspect.replay_head, &b.replay_list); b.replay_count = 1;
        if (!strcmp(argv[1], "overlap")) {
            assert(wait_replay(&b, &candidate.aspect) == -ETIMEDOUT);
            assert(list_empty(&candidate.aspect.replay_head) && b.replay_count == 1);
        } else {
            assert(replay_data(&b, 0, payload, sizeof(payload), true) == -ETIMEDOUT);
            assert(allocations == 1 && frees == 1 && put_count == 1 && !ios);
        }
    } else if (!strcmp(argv[1], "capacity")) {
        b.replay_count = 512;
        assert(wait_replay(&b, &candidate.aspect) == -ETIMEDOUT && b.replay_count == 512);
    } else if (!strcmp(argv[1], "stopping")) {
        b.terminate = true;
        assert(wait_replay(&b, &candidate.aspect) == -ESHUTDOWN && !b.replay_count);
    } else if (!strcmp(argv[1], "nonoverlap")) {
        candidate.ref_pos = 8192;
        list_add(&previous.aspect.replay_head, &b.replay_list); b.replay_count = 1;
        assert(!wait_replay(&b, &candidate.aspect) && b.replay_count == 2);
    } else if (!strcmp(argv[1], "checkpoint_busy")) {
        b.replay_count = 1;
        assert(replay_checkpoint(&b, &input, 100, 0) == -ETIMEDOUT);
        assert(input.inf.inf_min_pos == 10 && !publishes);
    } else if (!strcmp(argv[1], "checkpoint_error")) {
        b.disk_io_error = -EIO;
        assert(replay_checkpoint(&b, &input, 100, 0) == -EIO);
        assert(input.inf.inf_min_pos == 10 && !publishes);
    } else if (!strcmp(argv[1], "checkpoint_ok")) {
        assert(!replay_checkpoint(&b, &input, 100, 0));
        assert(input.inf.inf_min_pos == 100 && publishes == 1);
    } else {
        int expected = 0;
        if (!strcmp(argv[1], "get_error")) expected = get_error = -ENOMEM;
        else if (!strcmp(argv[1], "missing_data")) { no_data = true; expected = -ENOMEM; }
        else if (!strcmp(argv[1], "bad_length")) { bad_len = 8192; expected = -EINVAL; }
        else if (!strcmp(argv[1], "io_error")) expected = io_error = -EIO;
        else assert(!strcmp(argv[1], "discard"));
        assert(replay_data(&b, 0, payload, sizeof(payload), true) == expected);
        assert(allocations == 1 && frees == 1 && !b.replay_count);
        if (!expected || io_error) assert(ios == 1 && (seen_flags & MREF_DISCARD));
        else assert(!ios);
    }
    assert(!b.replay_mutex && !input.inf_mutex);
    return 0;
}
'''


class ReplayLifetime(unittest.TestCase):
    def test_production_replay_paths(self):
        functions = ["replay_endio", "_has_conflict", "wait_replay",
                     "replay_checkpoint", "replay_data"]
        code = MOCKS + "\n".join(function_source(f) for f in functions) + MAIN
        with tempfile.TemporaryDirectory(prefix="mars-replay-test-") as directory:
            source = Path(directory) / "replay.c"
            binary = Path(directory) / "replay"
            source.write_text(code)
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(source), "-o", str(binary)], check=True)
            for scenario in ["overlap", "capacity", "stopping", "nonoverlap",
                             "checkpoint_busy", "checkpoint_error", "checkpoint_ok",
                             "get_error", "missing_data", "bad_length",
                             "timeout_cleanup", "io_error", "discard"]:
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), scenario], check=True)


if __name__ == "__main__":
    unittest.main()
