#!/usr/bin/env python3
"""Exercise the production client timeout routine with checked kernel mocks.

This is a userspace regression test for queue/lifetime logic, not a substitute
for kernel fault injection. The function body is extracted unchanged from the
driver so that these tests detect regressions in the shipped implementation.
"""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def function_source(name):
    source = (ROOT / "kernel/mars_client.c").read_text()
    start = source.index(f"void {name}(")
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(name) struct list_head name = { &(name), &(name) }
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define list_entry(ptr, type, member) container_of(ptr, type, member)
#define list_for_each_entry_safe(pos, n, head, member) \
    for (pos = list_entry((head)->next, __typeof__(*pos), member), \
         n = list_entry(pos->member.next, __typeof__(*pos), member); \
         &pos->member != (head); \
         pos = n, n = list_entry(n->member.next, __typeof__(*n), member))
static void init_list(struct list_head *h) { h->prev = h->next = h; }
static bool list_empty(const struct list_head *h) { return h->next == h; }
static void list_del_init(struct list_head *h) {
    h->next->prev = h->prev;
    h->prev->next = h->next;
    init_list(h);
}
static void list_add_tail(struct list_head *h, struct list_head *head) {
    h->prev = head->prev; h->next = head;
    head->prev->next = h; head->prev = h;
}
static void list_move_tail(struct list_head *h, struct list_head *head) {
    list_del_init(h); list_add_tail(h, head);
}
typedef int atomic_t;
#define atomic_inc(p) (++*(p))
#define atomic_sub(n, p) (*(p) -= (int)(n))
#define READ_ONCE(x) (x)
#define mb() ((void)0)
#define cond_resched() ((void)0)
#define HZ 100
#define MAX_CLIENT_CHANNELS 4
#define trace_mars_request(...) ((void)0)
static unsigned long jiffies = 1000;
#define time_is_before_jiffies(a) ((long)((a) - jiffies) < 0)
struct mutex { bool held; };
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = true; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = false; }
static void brick_wake_smp(int *event) { ++*event; }
struct client_mref_aspect;
struct mref_object { struct client_mref_aspect *aspect; int refs, calls, error; };
struct client_mref_aspect {
    struct list_head io_head, hash_head;
    unsigned long submit_jiffies;
    bool has_completed, is_hashed;
    struct mref_object *object;
};
struct client_channel { struct { int s_send_abort, s_recv_abort; } socket; };
struct client_brick { atomic_t fly_count, timeout_count; long io_timeout; };
struct client_output {
    struct client_brick *brick;
    struct mutex mutex;
    struct { struct client_channel channel[MAX_CLIENT_CHANNELS]; int sender_event; } bundle;
};
static bool mars_net_is_alive = true;
static int mars_client_abort = 10;
static atomic_t mars_global_io_flying;
static long _compute_timeout(struct client_brick *b, bool info) {
    (void)info; return b->io_timeout;
}
static struct client_output *active_output;
static struct list_head *active_queue, *active_hash;
static struct client_mref_aspect reentered_aspect;
static struct mref_object reentered;
static bool reenter;
static void enqueue(struct client_mref_aspect *a, struct mref_object *m, unsigned long age) {
    memset(a, 0, sizeof(*a)); memset(m, 0, sizeof(*m));
    init_list(&a->io_head); init_list(&a->hash_head);
    a->object = m; a->submit_jiffies = age; a->is_hashed = true;
    m->aspect = a; m->refs = 1;
    list_add_tail(&a->io_head, active_queue); list_add_tail(&a->hash_head, active_hash);
    active_output->brick->fly_count++; mars_global_io_flying++;
}
static void callback(struct mref_object *m, int error) {
    assert(!active_output->mutex.held);
    assert(list_empty(&m->aspect->io_head));
    assert(list_empty(&m->aspect->hash_head));
    assert(!m->aspect->is_hashed && m->aspect->has_completed);
    assert(m->refs == 1 && m->calls == 0);
    m->calls++; m->error = error;
    if (reenter) {
        reenter = false;
        mutex_lock(&active_output->mutex);
        enqueue(&reentered_aspect, &reentered, jiffies);
        mutex_unlock(&active_output->mutex);
    }
}
#define SIMPLE_CALLBACK(mref, error) callback(mref, error)
static void client_ref_put(struct client_output *output, struct mref_object *m) {
    assert(output == active_output && m->refs == 1 && m->calls == 1);
    m->refs--;
}
'''

MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    struct client_brick brick = { .io_timeout = 3 };
    struct client_output output = { .brick = &brick };
    LIST_HEAD(queue); LIST_HEAD(hash);
    struct client_mref_aspect aspects[3];
    struct mref_object refs[3];
    active_output = &output; active_queue = &queue; active_hash = &hash;
    /* Deliberately reordered after a reconnect: newest, expired, newest. */
    enqueue(&aspects[0], &refs[0], 900);
    enqueue(&aspects[1], &refs[1], 600);
    enqueue(&aspects[2], &refs[2], 950);
    bool force = !strcmp(argv[1], "force");
    if (!strcmp(argv[1], "disabled")) brick.io_timeout = 0;
    if (!strcmp(argv[1], "network_down")) mars_net_is_alive = false;
    if (!strcmp(argv[1], "reentrant")) reenter = true;
    if (!strcmp(argv[1], "wrap")) {
        jiffies = 100;
        aspects[0].submit_jiffies = 50;
        aspects[1].submit_jiffies = (unsigned long)-250;
        aspects[2].submit_jiffies = 90;
    }
    _do_timeout(&output, &queue, force);
    assert(!output.mutex.held);
    if (force || !mars_net_is_alive) {
        for (int i = 0; i < 3; i++)
            assert(refs[i].calls == 1 && refs[i].refs == 0 && refs[i].error == -ESHUTDOWN);
        assert(list_empty(&queue) && list_empty(&hash));
        assert(brick.fly_count == 0 && brick.timeout_count == 3);
    } else if (!strcmp(argv[1], "disabled")) {
        for (int i = 0; i < 3; i++) assert(refs[i].calls == 0 && refs[i].refs == 1);
        assert(brick.fly_count == 3 && brick.timeout_count == 0);
        assert(output.bundle.channel[0].socket.s_send_abort == mars_client_abort);
    } else {
        assert(refs[0].calls == 0 && refs[2].calls == 0);
        assert(refs[1].calls == 1 && refs[1].refs == 0 && refs[1].error == -ETIMEDOUT);
        assert(brick.timeout_count == 1);
        assert(brick.fly_count == (!strcmp(argv[1], "reentrant") ? 3 : 2));
        /* A repeated pass must not complete a timed-out request twice. */
        _do_timeout(&output, &queue, false);
        assert(refs[1].calls == 1 && brick.timeout_count == 1);
    }
    assert(mars_global_io_flying == brick.fly_count);
    return 0;
}
'''


class ClientTimeouts(unittest.TestCase):
    def test_production_timeout_routine(self):
        with tempfile.TemporaryDirectory(prefix="mars-timeout-test-") as directory:
            source = Path(directory) / "timeout.c"
            binary = Path(directory) / "timeout"
            source.write_text(PRELUDE + function_source("_do_timeout") + MAIN)
            subprocess.run(
                shlex.split(os.environ.get("CC", "cc"))
                + ["-std=gnu11", "-Wall", "-Wextra", "-Werror",
                   "-fsanitize=undefined", str(source), "-o", str(binary)],
                check=True,
            )
            for scenario in ("reordered", "force", "disabled", "network_down", "reentrant", "wrap"):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), scenario], check=True)


if __name__ == "__main__":
    unittest.main()
