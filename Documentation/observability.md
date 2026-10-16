# MARS diagnostics and performance measurements

MARS exports kernel trace events under `/sys/kernel/tracing/events/mars/`.
Use them to correlate replication requests, transaction log activity, network
reconnections and primary transitions. Events copy resource names into the
trace buffer, so they remain readable after a resource is removed.

## Trace capture

Load the module on a kernel with event tracing enabled. If tracefs is not already
mounted, mount it at `/sys/kernel/tracing`. As root, record a bounded workload:

```sh
trace-cmd record -e 'mars:*' -e block:block_rq_issue -e block:block_rq_complete -- sleep 30
trace-cmd report > mars-trace.txt
```

Keep `trace.dat`, the kernel release, MARS build tag, resource configuration and
the workload parameters with the report. Event capture has overhead; measure
baseline throughput with tracing disabled as well.

| Event | Fields and purpose |
| --- | --- |
| `mars_request` | Resource, phase, request identifier, byte position and length, flags and errno. Client submission/completion pairs expose network request latency; copy completion records carry copy errors. |
| `mars_log` | Resource, phase, byte position and length, errno. Log submission and completion show where durable commit time is spent. |
| `mars_connection` | Resource, peer, channel, channel state and errno. States are initialized `0`, socket open `1`, receiver started `2`, connect command sent `3`. A state of `3` does not certify that the peer accepted the resource. |
| `mars_role` | Resource and primary boolean. Records a change of local primary state. |

Use the live `format` file for field types, for example
`/sys/kernel/tracing/events/mars/mars_request/format`. Request identifiers are
valid for the lifetime of one request; allocation can reuse them later. Negative
`error` fields are Linux errno values. These trace events are diagnostic interfaces
and can gain fields as the module evolves.

## Storage failure state

Logger statistics include `fatal_error`. Zero means no logging or writeback
failure has been latched. A negative value records the first failure and remains
set until that logger instance is reconstructed. New I/O fails with that error,
pending work drains, and the completed replay position stops advancing. A later
successful request cannot hide the failed part of the log.

A failed log append never enters data writeback. If a data writeback fails after
an acknowledged durable append, recovery needs the retained transaction log.
Preserve those logs, resolve the backing-storage error, and follow the resource
recovery procedure before restarting it. Repeatedly resetting statistics does
not clear the failure state.

## Repeatable benchmarks

Install [fio](https://fio.readthedocs.io/en/latest/fio_doc.html). The repository
wrapper defaults to a read workload, saves fio JSON output, and writes a companion
`.meta.json` with the command, kernel, architecture and elapsed time:

```sh
python3 scripts/benchmark.py /dev/mars/test results/read-depth32.json --size 1G
python3 scripts/benchmark.py /dev/mars/test results/read-depth1.json --size 1G --depth 1
```

The existing target must be large enough for the selected working set. For an
isolated disposable resource, writes require an explicit switch because they
overwrite the target:

```sh
python3 scripts/benchmark.py /dev/mars/disposable results/write.json --workload randwrite --allow-write
```

Use `--dry-run` to inspect the command without running fio. Compare the same
block size, working set, queue depth, number of jobs and duration across builds.
Include IOPS, bandwidth, completion latency percentiles and CPU consumption.
Repeat at different resource counts, with and without a replication peer, and
record peer latency, backend type and device geometry. Throughput measurements
do not establish crash durability; use abrupt-crash and replay tests for that.

## Failure regression checks

```sh
python3 -m unittest discover -s test_suite/unit -p 'test_*.py'
```

These tests compile the production timeout, frontend, logger and replay functions
with checked userspace mocks and UndefinedBehaviorSanitizer. They cover reordered
timeouts, shutdown, reentrant completion, counter wraparound, log append failure,
preimage read/log failures, partial preimage submission and data writeback
failure followed by a successful final callback. They also check synchronous
and delayed discard completion, discard snapshot isolation, partial submission
failure, completion only after log I/O, replay overlap timeouts and replay
checkpoints with outstanding
or failed writes, partial logger allocation cleanup and received device geometry.
They verify request ownership, queue drain and preservation
of the last safe replay position. The VM tests below exercise the kernel and
backend I/O integration with real storage faults.

Replay admission stops on an overlap timeout. A checkpoint never advances while
replay writes remain outstanding or after a write fails. If a backend stops
returning callbacks entirely, replay stops issuing new work and retains its
objects until those callbacks return; a timeout cannot safely cancel an
already submitted block operation.

For real I/O faults in an isolated VM, install `dmsetup` and run:

```sh
python3 test_suite/qemu/run.py 7.2.9-2-cachyos --guest-script test_suite/qemu/faults.sh --extra-module dm_mod --extra-binary dmsetup
python3 test_suite/qemu/run.py 7.2.9-2-cachyos --guest-script test_suite/qemu/faults.sh --extra-module dm_mod --extra-binary dmsetup --append mars_fault=log
```

Substitute an installed kernel release. The fixture changes a guest device mapper
target from linear storage to the `error` target, checks that failed I/O finishes,
restores the target, checks that the failure remains latched and the replay
minimum stays fixed, then unloads the module. The log case also exercises an
aborted ext4 journal and shutdown with read-only metadata. The fixture changes
only its generated guest disks.
