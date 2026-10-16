Modern kernel operation and release policy
==========================================

Scope and support
-----------------

This document is the operational reference for the modern compatibility
path in this fork. Historical manuals explain the administration model;
their old pre-patch requirements, SIO warnings and development roadmap do
not describe this path. MARS replicates asynchronously: a durable write on
a primary does not imply that a secondary has received or applied it.

The modern API path applies to Linux >= 6.18. The development build targets
are Linux 6.18 LTS and 7.2 on x86-64, with
7.3 release candidates included in local compatibility testing. CI builds
fixed upstream tags v6.18 and v7.2 with both GCC and Clang and W=1. Those
jobs exercise normal and MARS debugging configurations. A passing build
is not a claim of power-loss correctness or production certification.
The historical 2.6-era path remains available without current validation.
Other architectures and 64 KiB page sizes have not been validated; the
historical log record length and native-ABI format need a separate audit
before expanding architecture support.

The modernization has been built with W=1 in isolated staged trees against
installed 6.18.55 CachyOS LTS and 7.1.5 Arch headers using GCC, and 7.2.9
CachyOS and 7.3-rc6 CachyOS headers using Clang. Existing compiler warnings
remain; W=1 is currently diagnostic rather than a zero-warning gate.
Debian packages build, install/register with DKMS and remove successfully
in an isolated Debian trixie container. The container has no target kernel
headers, so that check does not validate DKMS module compilation or loading.

Local validation on 2026-10-14 uses GCC 16.2.1, Clang 23.1.1 and
QEMU 11.1.2/KVM. The modern changes have passed:

* normal W=1 builds against 6.18.55-2-cachyos-lts, 7.1.5-arch1-2,
  7.2.9-2-cachyos and 7.3.0-rc6-2-cachyos-rc; 7.2 also builds with
  MARS_DEBUG and MARS_DEBUG_MEM enabled;
* 7.2 debug/memory-check guest I/O, partial subpage discard and request
  range checks;
* LTS single-node I/O/lifecycle with native 4K block geometry;
* 7.3 single-node I/O with discard zero fallback on unsupported storage;
* 7.2 abrupt-crash recovery after acknowledged write/discard/overwrite,
  with backing writes delayed for 60 seconds and the guest killed after
  4.2 seconds; reboot replay verified the backing data;
* 38 network parser/path/export/IPv6 cases on 6.18 and 40 on 7.2, including
  hostile pulled metadata and malformed request flags/payloads, repeated
  module reload/unload and default networking disabled; the two additional
  7.2 cases verify incoming block/log writes cannot request skipped sync;
* real WireGuard handshake and encrypted inbound/outbound metadata traffic
  on 7.2, explicit source binding and rejected plaintext local-route bypass;
* modern 7.2-primary to 7.3-secondary replication and mixed-source replication
  to the pre-change 87ac5140 secondary on 7.3, plus reverse cross-kernel
  replication from 7.3 to 7.1;
* production timeout, logger/replay lifetime, frontend completion and log
  durability unit checks with UBSan, network path checks with ASan/UBSan,
  and build/status/offline-scrub regressions;
* Debian package construction, install/DKMS registration/removal in a
  disposable Debian trixie container, and compilation of its extracted
  module sources against 7.2; the container itself has no kernel headers;
* signing a copied module using the target kernel's sign-file, with the
  signer and PKCS#7 metadata verified by modinfo.

See test_suite/qemu/README and retained test artifacts for reproducible
commands and further replication/fault results. None of these checks
establishes production certification, KASAN/KCSAN runtime coverage, or
successful Secure Boot certificate enrollment/signature-enforced loading.
Those remain separate release checks.

Durability and discard
----------------------

The exported disk's write-cache advertisement must agree with completion
semantics: a disk that advertises no volatile cache must make acknowledged
writes durable locally. The modern implementation uses transaction-log
persistence before successful write completion. Replica durability remains
asynchronous. Filesystem fsync guarantees still depend on the underlying
storage honoring cache-flush/FUA commands and its own persistence contract.

Discard is a replicated operation. Discarded ranges are represented by
zero data in the existing transaction log, with an optional deallocation
hint for compatible local backends. Older readers and backends without
native discard support replay the zero payload. Native discard requests
physical space reclamation where supported, but readback of a discarded
range is unspecified by the block contract; applications must not expect
zeros, and peers can return different values for discarded sectors.
WRITE_ZEROES, by contrast, guarantees logical zeroing and retains that
meaning through logging and replay. Neither operation is secure erase.
Monitor log volume and replication traffic when trimming large filesystems;
zero records still incur logging work. See the interface queue limits before
choosing discard request sizes.

Use blkdiscard only on an unmounted disposable resource. For a mounted
filesystem, use fstrim and check its exit status. Validation must cover
unaligned and out-of-range requests, partial backing support, overwritten
discard ranges, primary restart, secondary replay and old/new peer pairs.
Do not infer coverage for a scenario from a successful compile.

Storage and recovery
--------------------

Use dedicated backing devices and a separate ext4 /mars filesystem. Keep
application mounts off the backing devices. Maintain sufficient log space
for the expected network outage; the historic emergency policy can discard
replication history under space pressure and force a subsequent full sync.
Alert before reaching those thresholds. Defaults reserve a total minimum
of 20 GiB across the emergency and operational thresholds, not just the
currently occupied log volume.

After a failure, preserve transaction logs and collect kernel diagnostics,
resource state and the exact loaded module version before attempting
repair. Establish which node has the desired data and keep the other node
secondary. Inspect marsadm status and the manual's recovery procedures;
never promote two independent copies simply because both are reachable.
Do not remove apparent gaps in log sequences by hand. Use full resync when
required history has been lost. Rewind, synchronous replication and
persistent data scrubbing are separate unfinished features.

Network boundary
----------------

The native replication protocol has no built-in cryptographic peer identity.
Networking is disabled by default; bare modprobe still supports local block
operation. Network listening and outgoing connections require an explicit
WireGuard interface or mars_allow_insecure=1. Once enabled, the default
listen address is loopback. For inter-node replication, establish
a dedicated WireGuard interface whose peers and AllowedIPs are provisioned
by the administrator. Bind MARS to that interface and its tunnel address in
/etc/modprobe.d/mars.conf, for example on the first node::

    options mars mars_interface=wg0 mars_listen_addr=10.80.0.1 mars_source_addr=10.80.0.1 mars_exports=myspace

Use a distinct tunnel address for each node. In authenticated mode, peers
must use nonlocal source addresses; local-route attempts cannot bypass the
WireGuard binding. Configure cluster name/IP records to resolve to tunnel addresses, permit the MARS ports only
on that interface, and start the interface before mars.service. For a
wg-quick managed interface, add a service drop-in::

    [Unit]
    Requires=wg-quick@wg0.service
    After=wg-quick@wg0.service

The module validates the interface type and binds its sockets to it. Network
peers are still members of one trust domain; WireGuard key ownership does
not create resource-level roles in the MARS protocol. Give each node only
the intended peer keys/routes. IPv6 literals and bracketed IPv6 address:port
endpoints are supported; one listener serves the configured address family.

For a deliberately isolated test network, mars_allow_insecure=1 permits an
explicit insecure deployment. This is used by disposable QEMU tests. It
must not be mistaken for authenticated transport. Configure an explicit
listen address instead of exposing all interfaces unnecessarily.

mars_exports is a comma-separated list of resource names without the
resource- prefix, for example myspace,archive. Its default "*" exports every
resource; an empty value disables data I/O. Export policy is local and applies
to every admitted peer. Metadata is still exchanged cluster-wide.

Received path operations are constrained to the MARS metadata/export model;
prepare local backing-device mappings through marsadm before permitting peer
access. /mars itself and traversed parent directories must not be symlinks.
The filesystem namespace and local administrative account remain trusted.
Received metadata snapshots are validated before merging. Paths must be
canonical descendants of /mars and carry matching basenames and supported
metadata types. A snapshot is limited to 65,536 entries and 32 MiB of decoded
allocations; malformed snapshots are rejected as a whole. Pulled snapshots
obey the same mutation policy as pushed links, including protection of local
backing-device mappings. Path confinement and malformed-input checks are
needed even with encryption.
The dedicated WireGuard QEMU test exercises a real handshake, encrypted
metadata traffic in both directions, explicit source binding and rejection
of a plaintext local-route bypass; it has passed on the 7.2 test kernel.

Build, packaging and release checks
----------------------------------

The canonical external build is kernel/Makefile plus kernel/Kbuild. Debian
and DKMS use that same tree. scripts/stage-source.py copies a clean source
tree and writes a release DISTVERSION; packaging never replaces build files
or performs a git checkout of source files. External configuration resolves
MARS dependencies, validates explicit overrides and requires the kernel
facilities selected by kernel/Kconfig. Unsupported Kconfig syntax fails
rather than being silently approximated.

Build metadata has no host/user identity or current wall-clock timestamp.
MARSVERSION overrides DISTVERSION, which overrides git describe. An exported
unversioned tree reports "unversioned". SOURCE_DATE_EPOCH, when supplied,
sets a UTC build date; otherwise the date is "unspecified". For reproducible
unsigned artifacts, use the same version, source, configured kernel,
toolchain and build flags; debug paths and signatures may still require
normalization outside these metadata headers. Generated headers are
atomically replaced only when content changes.

The automatic GitHub workflow runs the build-tool regressions, constructs
Debian packages and builds GCC/Clang modules against fixed kernel tags.
The manual QEMU workflow requires a disposable self-hosted runner labelled
mars-qemu with the selected installed kernels, KVM access, dmsetup and the
WireGuard wg tool. Runtime jobs include storage/log fault injection, encrypted
peer transport and abrupt-crash recovery as well as normal replication. It never runs
untrusted pull requests automatically. test_suite/ci/configure-kernel.sh
can configure normal, KASAN or KCSAN targets with lockdep and fault-injection
facilities; those kernels must be fully built and installed on the runner
before runtime jobs select them. Merely enabling those facilities is not
a substitute for exercising relevant failures.

A release should have retained build logs, kernel configurations, module
metadata, package artifacts and console logs for:

* normal and debug builds with GCC and Clang;
* basic lifecycle and ext4 I/O, replication in both version directions;
* discard/rewrite/replay and native 4K geometry;
* abrupt guest crash with verification of all acknowledged writes;
* ENOSPC, injected storage errors, allocation failure and reconnect;
* teardown under load with lockdep and KASAN/KCSAN;
* DKMS add/build/install, new-kernel rebuild and removal;
* enforced module signatures with the intended trusted certificate.

Unexecuted items remain release requirements; their presence here does not
claim that the current harness covers every scenario. Install/signing steps
and service ordering are in INSTALL and debian/README.md.

Observability and remaining development
--------------------------------------

userspace/mars-status provides a versioned read-only monitoring interface::

    mars-status --json
    mars-status --json myspace

It is installed by mars-tools. Schema version 1 contains a top-level peer
and resources array; resource records expose is_primary, if_state,
if_flying, open_count, log_fatal_error, logical_block_size,
physical_block_size and discard_supported. Values are validated integers
or null when absent/invalid. A null is not zero or healthy. Unknown metadata
links are excluded. Use --host to select another node's reported state and
--root for an alternate metadata directory. The default text output is a
tab-separated view. These independently sampled kernel status links do not
form an atomic snapshot and may lag actual I/O; they are monitoring inputs,
not a distributed promotion/fencing decision.

Retain kernel logs and /mars status alongside application measurements.
Trace events can correlate request lifetime, log progress and teardown;
inspect /sys/kernel/tracing/events/mars/ for the events available in the
loaded build. See Documentation/observability.md for event descriptions and
scripts/benchmark.py for reproducible fio workloads. Capture only the relevant events during sustained I/O so
tracing itself does not dominate results.

Before reviving upstream QIO, benchmark SIO on the intended storage and
network using controlled resource counts, queue depths and sync intervals.
Record IOPS, throughput, tail latency, CPU usage, log growth and replica lag.
Compare with direct backing-device I/O only when the resource is detached.
Any destructive fio/blkdiscard workload belongs on disposable devices.

Reviewed upstream fixes 3d23b6b9 (handover condition), 0b9fea5b (SSH/rsync
removal), and 35e99cb6 (historic systemd generator removal) are ported into
marsadm. Native join-cluster now loads MARS if needed; configure module
network policy before invoking it. Deprecated SSH/systemd flags are accepted
with warnings; generated resource units are replaced by explicit service
ordering described in INSTALL.

Other upstream WIP branches remain design references, not independent stable
feature releases. Outstanding larger work includes persistent checksum
scrubbing, stronger per-peer protocol authorization,
QIO performance evaluation, rewind and optional synchronous replication.
Wire/on-disk formats and mixed-version guarantees need explicit versioned
specifications before further protocol extensions become a stable ABI.

Offline replica verification
----------------------------

mars-scrub is a read-only comparison tool using core Perl Digest::SHA. It
does not maintain persistent block checksums and cannot distinguish which
of two disagreeing replicas is correct. Stop application writes, wait for
replication/replay to complete, and detach both resources before comparing
their backing devices or stable snapshots::

    mars-scrub --quiescent /dev/vg/primary-snapshot /dev/vg/secondary-snapshot

The --quiescent flag explicitly acknowledges that the caller has established
stable inputs; the tool cannot enforce distributed quiescence. Both explicit
paths must be distinct, equal-sized regular files or Linux block devices.
It opens them read-only, hashes bounded chunks with SHA-256 and emits JSON
with mismatching byte offsets, chunk lengths and both digests. Exit status
is 0 for equality, 1 for differences and 2 for usage/I/O/size errors. A short
read is an error, never an implicit matching zero-filled region.

The default chunk size is 1 MiB, adjustable with --chunk-bytes (512 bytes to
16 MiB). The tool continues comparing after mismatches but records at most
1024 mismatch entries by default; --max-records sets the bounded report
limit, and omitted_mismatches reports additional mismatching chunks. No
repair or authoritative-copy selection is performed. Comparing live
asynchronous replicas would report expected lag as corruption. Persistent
checksum files, authenticated data history and online scrubbing still need
an explicit format/migration design; the old upstream WIP-checksums brick
has not been transplanted into the modern code.

Wire and transaction-log compatibility
-------------------------------------

The current transaction-log FORMAT_VERSION remains 1. Record scalar fields
are serialized in native byte order with historical type sizes, so peers
and tools must agree on endianness and ABI. An unaligned native load is not
required by the format: the implementation uses memcpy for serialized
fields. Start/end magic, lengths, sequence numbers and checksums retain
their existing positions. kernel/lib_log.h and the corresponding writer
and log_scan code in kernel/lib_log.c are the layout reference.

A discard record stores ordinary zero data and the exact optional value
0x4d445343 in the existing four-byte spare field. The hint requests optional
physical deallocation; zero data is the compatible fallback representation,
not a promise that reads after native discard return zeros. Old readers
ignore the spare and replay the zero data. Unknown hint values must not
trigger deallocation. The hint is not a new durable-data format and must
never allow nonzero payload to be discarded. WRITE_ZEROES is encoded as a
normal zero write without a deallocation hint.

Network protocol level 3 negotiates extended xf_ geometry/capability fields
with peers. Older peers use their previous field set and conservative local
limits. This negotiation does not make the underlying wire protocol
portable across arbitrary architectures or ABIs. Use the same endianness
and compatible ABI throughout a cluster and run both replication directions
before a rolling upgrade. A cross-endian canonical format would require a
separate versioned protocol and transaction-log migration.

Original upstream branch review
-------------------------------

The source of this review is the original project,
https://github.com/schoebel/mars, not branches in this fork. The following
records all 20 reviewed branch tips (available locally as upstream-review/
refs). WIP branches are mutable and several are cumulative. This fork
selectively ports reviewed fixes and adapts geometry/protocol work to the
modern API; it does not merge entire WIP branches or claim their unfinished
features.

.. list-table:: Reviewed upstream branches and implementation decisions
   :header-rows: 1
   :widths: 42 18 40

   * - Branches
     - Reviewed tips
     - Disposition
   * - master, mars0.1.y, mars0.1a.y
     - b96074bb (all three)
     - Stable upstream base already present in the fork.
   * - WIP-next-release
     - 0d328d2e
     - Ported handover fix 3d23b6b9 and removal of historic SSH/rsync
       administration 0b9fea5b. Modern installation docs describe native join.
   * - WIP-remove-systemd
     - 4ffa8298
     - Ported removal of the historic marsadm template generator 35e99cb6;
       added an explicit, disabled-by-default service in this fork.
   * - WIP-next-next-release
     - 8799e547
     - Adapted backing geometry/nonrotational information and protocol-level-3
       field negotiation, including the concerns addressed by 06a00c73,
       c036fd46, 8e0b9fe0, a46e5774, 809fe85f and af411500.
   * - WIP-toolchains; WIP-qio-for-5.4, WIP-qio-for-5.7,
       WIP-qio-for-5.8, WIP-qio-for-5.9, WIP-qio-for-5.10
     - 812cfa68; 41627520, d69d069e, 655c9d04, f5ffac7b, 84810029
     - QIO design/performance references. Deferred because the old kernel
       pre-patches and filesystem internals need a separate modern design
       and benchmarks; the supported regular-file backend remains SIO.
   * - WIP-checksums
     - 45e45f08
     - Persistent checksum storage requires a format and migration plan.
       This fork instead adds an independent read-only offline comparison
       tool, mars-scrub; it does not import the checksum brick.
   * - WIP-prosumer
     - 0f1d19c5
     - Diskless access, epochs and mounted-client handover remain optional
       architectural work. Rewind and synchronous replication are also
       deferred product features, not capabilities of this modernization.
   * - WIP-BASE, WIP-PORTABLE, WIP-PROPOSE-UPSTREAM
     - 48654bff, c8b398b1, 907dd224
     - References for dependency separation, portability, naming and
       upstream preparation; no wholesale transplant of the old tree.
   * - mars0.1b.y, mars0.2.y
     - bbd213b1, f32e1c80
     - Historical scalability/protocol work. Existing compression and
       socket bundling are retained; these branches are not separate
       missing-feature bundles.
   * - gh-pages
     - 1701e606
     - Historical website/roadmap reference. Current support and remaining
       work are documented here instead of repeating old release promises.
