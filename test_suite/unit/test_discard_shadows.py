#!/usr/bin/env python3
"""Discard and ordinary writes must never share a mutable logger shadow."""

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
    match = re.search(rf"^(?:struct \w+ \*|(?:int|bool|void) ){name}\(", source, re.M)
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
#define KEEP_UNIQUE
#define CONFIG_MARS_DEBUG
#define MREF_WRITE 1
#define MREF_MAY_WRITE 2
#define MREF_DISCARD 4
#define HASH_PER_PAGE 1
#define TL_INPUT_READ 0
#define unlikely(x) (x)
#define MARS_ERR(...) ((void)0)
#define atomic_inc(p) (++*(p))
#define atomic64_add(n, p) (*(p) += (n))
struct trans_logger_hash_anchor { int hash_mutex; struct list_head hash_anchor; };
struct trans_logger_input { int unused; };
struct trans_logger_brick {
    struct trans_logger_hash_anchor **hash_table;
    struct trans_logger_input *inputs[1];
    long long shadow_mem_used;
};
struct trans_logger_output { struct trans_logger_brick *brick; };
struct mref_object {
    int ref_len, ref_flags, refs;
    loff_t ref_pos;
    void *ref_data;
};
struct trans_logger_mref_aspect {
    struct mref_object *object;
    struct trans_logger_brick *my_brick;
    struct trans_logger_mref_aspect *shadow_ref;
    struct list_head hash_head, collect_head;
    int alloc_len;
    void *shadow_data;
    bool do_dealloc, do_buffered, is_stable;
};
static int allocations, global_mshadow_count;
static long long global_mshadow_used;
static void *brick_block_alloc(loff_t pos, int len) {
    (void)pos; void *p = malloc(len); assert(p); allocations++; return p;
}
static unsigned int hash_fn(loff_t pos) { (void)pos; return 0; }
static void down_read(int *lock) { assert(!*lock); *lock = 1; }
static void up_read(int *lock) { assert(*lock); *lock = 0; }
static void _mref_check(struct mref_object *m) { assert(m->refs > 0); }
static void _mref_get(struct mref_object *m) { _mref_check(m); m->refs++; }
static void _mref_get_first(struct mref_object *m) { assert(!m->refs); m->refs++; }
static int backend_get(struct trans_logger_input *input, struct mref_object *m) {
    (void)input; (void)m; assert(0 && "overlapping read should use the newest shadow"); return -EIO;
}
#define GENERIC_INPUT_CALL(input, op, m) backend_get(input, m)
'''

MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    bool existing_discard = !strcmp(argv[1], "write_over_discard") ||
        !strcmp(argv[1], "read_over_discard") || !strcmp(argv[1], "discard_barrier");
    bool incoming_discard = !strcmp(argv[1], "discard_over_write");
    bool reading = !strcmp(argv[1], "read_over_discard");
    bool stable = !strcmp(argv[1], "stable_write");
    bool reuse = reading || !strcmp(argv[1], "write_over_write");
    struct trans_logger_hash_anchor bucket = {0};
    struct trans_logger_hash_anchor *table[] = { &bucket };
    struct trans_logger_input input = {0};
    struct trans_logger_brick brick = { .hash_table = table, .inputs = { &input } };
    struct trans_logger_output output = { .brick = &brick };
    unsigned char master_data[64], older_data[64];
    memset(master_data, existing_discard ? 0 : 0x5a, sizeof(master_data));
    memset(older_data, 0x33, sizeof(older_data));
    struct mref_object master = { .ref_pos = 4096, .ref_len = 64, .refs = 1,
        .ref_flags = MREF_WRITE | MREF_MAY_WRITE | (existing_discard ? MREF_DISCARD : 0) };
    struct trans_logger_mref_aspect master_a = { .object = &master,
        .my_brick = &brick, .shadow_data = master_data, .is_stable = stable };
    master_a.shadow_ref = &master_a;
    struct mref_object older = { .ref_pos = 4096, .ref_len = 64, .refs = 1,
        .ref_flags = MREF_WRITE | MREF_MAY_WRITE };
    struct trans_logger_mref_aspect older_a = { .object = &older,
        .my_brick = &brick, .shadow_data = older_data };
    older_a.shadow_ref = &older_a;
    struct mref_object next = { .ref_pos = 4104, .ref_len = 16,
        .ref_flags = reading ? 0 : MREF_WRITE | MREF_MAY_WRITE | (incoming_discard ? MREF_DISCARD : 0) };
    struct trans_logger_mref_aspect next_a = { .object = &next };
    init_list(&bucket.hash_anchor); init_list(&master_a.hash_head);
    init_list(&older_a.hash_head);
    list_add_tail(&master_a.hash_head, &bucket.hash_anchor);
    if (!strcmp(argv[1], "discard_barrier")) list_add_tail(&older_a.hash_head, &bucket.hash_anchor);
    int status = reading ? _read_ref_get(&output, &next_a) : _write_ref_get(&output, &next_a);
    assert(status == 16 && next.refs == 1 && !bucket.hash_mutex);
    assert(older.refs == 1);
    if (reuse) {
        assert(!allocations && next_a.shadow_ref == &master_a && !next_a.do_dealloc);
        assert(next.ref_data == master_data + 8 && master.refs == 2);
        if (reading) {
            for (int i = 0; i < next.ref_len; i++) assert(!((unsigned char *)next.ref_data)[i]);
        } else {
            memset(next.ref_data, 0xa5, next.ref_len);
            for (int i = 8; i < 24; i++) assert(master_data[i] == 0xa5);
        }
    } else {
        assert(allocations == 1 && next_a.shadow_ref == &next_a && next_a.do_dealloc);
        assert(next.ref_data != master_data + 8 && master.refs == 1);
        memset(next.ref_data, incoming_discard ? 0 : 0xa5, next.ref_len);
        for (int i = 0; i < 64; i++) {
            assert(master_data[i] == (existing_discard ? 0 : 0x5a));
            assert(older_data[i] == 0x33);
        }
        assert(brick.shadow_mem_used == 16 && global_mshadow_used == 16 && global_mshadow_count == 1);
        free(next_a.shadow_data);
    }
    return 0;
}
'''


class DiscardShadows(unittest.TestCase):
    def test_production_shadow_reuse(self):
        code = MOCKS + "\n".join(function_source(f) for f in [
            "_hash_find", "hash_find", "_make_sshadow", "_read_ref_get", "_write_ref_get"]) + MAIN
        with tempfile.TemporaryDirectory(prefix="mars-discard-shadow-test-") as directory:
            source = Path(directory) / "shadows.c"
            binary = Path(directory) / "shadows"
            source.write_text(code)
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(source), "-o", str(binary)], check=True)
            for scenario in ["write_over_discard", "discard_over_write", "write_over_write",
                             "read_over_discard", "stable_write", "discard_barrier"]:
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), scenario], check=True)


if __name__ == "__main__":
    unittest.main()
