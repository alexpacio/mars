#!/usr/bin/env python3
"""Completion must follow durable log IO, and errors must not advance the log."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_logger_failures import function_source


MOCKS = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#define MREF_WRITING 1
#define MREF_UPTODATE 2
#define likely(x) (x)
#define MARS_ERR(...) ((void)0)
#define CHECK_PTR(p, label) do { if (!(p)) { assert(p); goto label; } } while (0)
#define atomic_dec(p) (--*(p))
struct mref_object { int ref_flags, completions, error, checksums; };
struct trans_logger_brick { int log_fly_count; };
struct trans_logger_mref_aspect { struct mref_object *object; bool is_completed; };
static int positions;
static bool cmpxchg(bool *p, bool old, bool val) {
    bool prev = *p; if (prev == old) *p = val; return prev;
}
static void mref_checksum(struct mref_object *m) { m->checksums++; }
static void update_max_pos(struct trans_logger_mref_aspect *a) { (void)a; positions++; }
#define CHECKED_CALLBACK(m, status, label) \
    do { (m)->completions++; (m)->error = status; } while (0)
'''

MAIN = r'''
int main(void) {
    struct trans_logger_brick b = { .log_fly_count = 1 };
    struct mref_object m = { .ref_flags = MREF_WRITING };
    struct trans_logger_mref_aspect a = { .object = &m };
    _complete(&b, &a, 0, true);
    assert(!m.completions && !a.is_completed && b.log_fly_count == 1 && !positions);
    _complete(&b, &a, -EIO, false);
    assert(m.completions == 1 && m.error == -EIO && a.is_completed);
    assert(!b.log_fly_count && !positions && !m.checksums && !(m.ref_flags & MREF_UPTODATE));
    _complete(&b, &a, 0, false);
    assert(m.completions == 1 && !positions && !b.log_fly_count);
    m = (struct mref_object) { .ref_flags = MREF_WRITING };
    a.is_completed = false; b.log_fly_count = 1;
    _complete(&b, &a, 0, true);
    assert(!m.completions && !positions);
    _complete(&b, &a, 0, false);
    assert(m.completions == 1 && !m.error && positions == 1 && m.checksums == 1);
    assert(!b.log_fly_count && (m.ref_flags & MREF_UPTODATE) && !(m.ref_flags & MREF_WRITING));
    _complete(&b, &a, 0, false);
    assert(m.completions == 1 && positions == 1 && !b.log_fly_count);
    return 0;
}
'''


class LogDurability(unittest.TestCase):
    def test_completion_after_log_io(self):
        code = MOCKS + function_source("_complete") + MAIN
        with tempfile.TemporaryDirectory(prefix="mars-durability-test-") as directory:
            source = Path(directory) / "durability.c"
            binary = Path(directory) / "durability"
            source.write_text(code)
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
