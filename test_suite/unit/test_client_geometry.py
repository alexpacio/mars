#!/usr/bin/env python3
"""Verify received geometry is validated atomically and legacy fields reset."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_client_timeouts import ROOT


def function_source(name):
    source = (ROOT / "kernel/mars_client.c").read_text()
    match = re.search(rf"(?:int|bool) {name}\([^;]*?\)\n\{{", source)
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
#include <stddef.h>
#include <string.h>
#include <errno.h>
#define PAGE_SIZE 4096
#define max(a, b) ((a) > (b) ? (a) : (b))
#define max_t(type, a, b) ((type)(a) > (type)(b) ? (type)(a) : (type)(b))
#define WRITE_ONCE(p, value) ((p) = (value))
struct mars_info {
    long long current_size;
    int tf_align, tf_min_size, xf_physical_block_size, xf_logical_block_size;
    int xf_alignment_offset, xf_io_min, xf_io_opt;
    int xf_discard_enabled, xf_write_same_capable, xf_nonrot;
};
struct client_output { struct mars_info info; int mutex, info_event; bool got_info; };
struct client_channel { int socket; };
static struct mars_info received;
static int recv_status = 1, mars_info_meta;
static bool legacy;
static bool is_power_of_2(unsigned int n) { return n && !(n & (n - 1)); }
static void mutex_lock(int *mutex) { assert(!*mutex); *mutex = 1; }
static void mutex_unlock(int *mutex) { assert(*mutex); *mutex = 0; }
static void brick_wake_smp(int *event) { ++*event; }
static int mars_recv_struct(int *socket, struct mars_info *info, int meta) {
    (void)socket; (void)meta;
    memcpy(info, &received, legacy ? offsetof(struct mars_info, xf_physical_block_size) : sizeof(*info));
    return recv_status;
}
'''

MAIN = r'''
int main(void) {
    struct mars_info valid = { .current_size = 1024 * 1024, .tf_align = 4096,
        .tf_min_size = 512, .xf_physical_block_size = 4096, .xf_logical_block_size = 512,
        .xf_alignment_offset = 512, .xf_io_min = 4096, .xf_io_opt = 65536,
        .xf_discard_enabled = 1, .xf_nonrot = 1 };
    assert(client_valid_info(&valid));
    struct mars_info regular = { .tf_align = 1, .tf_min_size = 1 };
    assert(client_valid_info(&regular));
    struct mars_info zero = {0}; assert(client_valid_info(&zero));
#define BAD(field, value) do { struct mars_info bad = valid; bad.field = value; \
    assert(!client_valid_info(&bad)); } while (0)
    BAD(current_size, -1); BAD(tf_align, -1); BAD(tf_min_size, -1);
    BAD(tf_align, 1000); BAD(tf_min_size, 513);
    BAD(xf_logical_block_size, -512); BAD(xf_logical_block_size, 256);
    BAD(xf_logical_block_size, 8192); BAD(xf_logical_block_size, 768);
    BAD(xf_physical_block_size, -4096); BAD(xf_physical_block_size, 256);
    BAD(xf_physical_block_size, 1000); BAD(xf_alignment_offset, -1);
    BAD(xf_alignment_offset, 4096); BAD(xf_io_min, -512); BAD(xf_io_min, 1);
    BAD(xf_io_opt, -512); BAD(xf_io_opt, 513);
    BAD(xf_discard_enabled, -1); BAD(xf_discard_enabled, 2);
    BAD(xf_write_same_capable, 2); BAD(xf_nonrot, 2);
    struct client_output output = { .info = valid };
    struct client_channel channel = {0};
    received = regular; legacy = true;
    assert(!client_recv_info(&output, &channel));
    assert(output.got_info && output.info_event == 1 && !output.mutex);
    assert(output.info.tf_align == 1 && !output.info.current_size);
    assert(!output.info.xf_logical_block_size && !output.info.xf_physical_block_size);
    assert(!output.info.xf_discard_enabled && !output.info.xf_alignment_offset);
    assert(!output.info.xf_io_min && !output.info.xf_io_opt && !output.info.xf_nonrot);
    legacy = false; received = valid; output.got_info = false;
    struct mars_info before = output.info;
    recv_status = 0;
    assert(client_recv_info(&output, &channel) == -EBADMSG);
    assert(!memcmp(&before, &output.info, sizeof(before)) && !output.got_info && output.info_event == 1);
    recv_status = -EPIPE;
    assert(client_recv_info(&output, &channel) == -EPIPE);
    assert(!memcmp(&before, &output.info, sizeof(before)) && !output.got_info);
    recv_status = 1; received.xf_io_min = 3;
    assert(client_recv_info(&output, &channel) == -EBADMSG);
    assert(!memcmp(&before, &output.info, sizeof(before)) && !output.got_info);
    received = valid;
    assert(!client_recv_info(&output, &channel));
    assert(!memcmp(&valid, &output.info, sizeof(valid)) && output.got_info && output.info_event == 2);
    return 0;
}
'''


class ClientGeometry(unittest.TestCase):
    def test_received_geometry_validation(self):
        code = MOCKS + function_source("client_valid_info") + function_source("client_recv_info") + MAIN
        with tempfile.TemporaryDirectory(prefix="mars-geometry-test-") as directory:
            source = Path(directory) / "geometry.c"
            binary = Path(directory) / "geometry"
            source.write_text(code)
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
