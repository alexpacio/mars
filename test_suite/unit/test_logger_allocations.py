#!/usr/bin/env python3
"""Inject allocation failure into the production logger constructor and shadow."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_client_timeouts import PRELUDE
from test_replay_lifetime import function_source


MOCKS = PRELUDE.split("typedef int atomic_t;")[0] + r'''
#define CONFIG_MARS_DEBUG
#define PAGE_SIZE 128
#define NR_HASH_PAGES 3
#define MAX_HASH_PAGES 3
#define HASH_PER_PAGE 2
#define TL_INPUT_LOG1 2
#define unlikely(x) (x)
#define MARS_ERR(...) ((void)0)
#define INIT_LIST_HEAD init_list
#define CHECK_HEAD_EMPTY(p) assert(list_empty(p))
#define atomic_inc(p) (++*(p))
#define atomic64_add(n, p) (*(p) += (n))
struct trans_logger_hash_anchor { int hash_mutex; struct list_head hash_anchor; };
struct logger_queue { const char *q_insert_info, *q_pushback_info, *q_fetch_info; };
struct trans_logger_brick {
    struct trans_logger_hash_anchor **hash_table;
    int replay_mutex, worker_event, caller_event, new_input_nr, log_input_nr, old_input_nr;
    long long shadow_mem_used;
    struct list_head replay_list, group_head;
    struct logger_queue q_phase[4];
};
struct trans_logger_output { struct trans_logger_brick *brick; };
struct mref_object { int ref_len, refs; long long ref_pos; void *ref_data; };
struct trans_logger_mref_aspect {
    struct mref_object *object;
    struct trans_logger_brick *my_brick;
    struct trans_logger_mref_aspect *shadow_ref;
    int alloc_len;
    void *shadow_data;
    bool do_dealloc, do_buffered;
};
static int attempts, fail_at, outstanding, groups, global_writeback, global_mshadow_count;
static long long global_mshadow_used;
static void *brick_block_alloc(long long pos, int len) {
    (void)pos;
    if (++attempts == fail_at) return NULL;
    void *p = malloc(len); assert(p); outstanding++; return p;
}
static void brick_block_free(void *p, int len) {
    (void)len; assert(p && outstanding > 0); outstanding--; free(p);
}
static void init_rwsem(int *lock) { *lock = 0; }
static void init_waitqueue_head(int *event) { *event = 0; }
static void qq_init(struct logger_queue *q, struct trans_logger_brick *b) { (void)q; (void)b; }
static void add_to_group(int *group, struct trans_logger_brick *brick) {
    (void)group; (void)brick; groups++;
}
static void _mref_get_first(struct mref_object *m) { assert(!m->refs); m->refs++; }
'''

MAIN = r'''
int main(void) {
    for (int failure = 1; failure <= NR_HASH_PAGES + 1; failure++) {
        struct trans_logger_brick brick = {0};
        attempts = 0; fail_at = failure;
        assert(trans_logger_brick_construct(&brick) == -ENOMEM);
        assert(!brick.hash_table && !outstanding && !groups);
        _free_pages(&brick);
        assert(!outstanding);
    }
    struct trans_logger_brick brick = {0};
    attempts = fail_at = 0;
    assert(!trans_logger_brick_construct(&brick));
    assert(outstanding == NR_HASH_PAGES + 1 && groups == 1);
    _free_pages(&brick); _free_pages(&brick);
    assert(!outstanding && !brick.hash_table);
    struct trans_logger_output output = { .brick = &brick };
    struct mref_object mref = { .ref_len = 16 };
    struct trans_logger_mref_aspect aspect = { .object = &mref };
    attempts = 0; fail_at = 1;
    assert(_write_ref_get(&output, &aspect) == -ENOMEM);
    assert(!mref.refs && !mref.ref_data && !aspect.shadow_data && !aspect.do_dealloc);
    assert(!outstanding && !brick.shadow_mem_used && !global_mshadow_used && !global_mshadow_count);
    attempts = fail_at = 0;
    assert(_write_ref_get(&output, &aspect) == 16);
    assert(mref.refs == 1 && mref.ref_data && aspect.shadow_data == mref.ref_data);
    assert(outstanding == 1 && brick.shadow_mem_used == 16 && global_mshadow_used == 16);
    brick_block_free(aspect.shadow_data, aspect.alloc_len);
    assert(!outstanding);
    return 0;
}
'''


class LoggerAllocations(unittest.TestCase):
    def test_allocation_failures_release_partial_state(self):
        code = MOCKS + "\n".join(function_source(f) for f in [
            "_free_pages", "trans_logger_brick_construct", "_write_ref_get"]) + MAIN
        with tempfile.TemporaryDirectory(prefix="mars-alloc-test-") as directory:
            source = Path(directory) / "allocations.c"
            binary = Path(directory) / "allocations"
            source.write_text(code)
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
