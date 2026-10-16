#!/bin/bash
# Actual backend/log I/O faults must fail requests and still permit unloading.
set -Eeuo pipefail
export PATH=/usr/bin:/bin:/sbin LC_ALL=C
mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /run
fail() {
    echo "MARS_QEMU_FAIL fault line=$1"
    cat /proc/sys/mars/info || true
    find /mars -maxdepth 3 -type l -printf '%p -> %l\n' || true
    dmesg
    poweroff
}
trap 'fail "$LINENO"' ERR
mode=data
for option in $(cat /proc/cmdline); do
    case "$option" in mars_fault=*) mode=${option#mars_fault=};; esac
done
case "$mode" in data|log) ;; *) false;; esac
hostname mars-test
modprobe virtio_mmio
modprobe virtio_blk
modprobe ext4
modprobe dm_mod
ip link set lo up
for i in {1..50}; do [ -b /dev/vdb ] && break; sleep .1; done
target=/dev/vdb
[ "$mode" != log ] || target=/dev/vda
sectors=$(blockdev --getsz "$target")
dmsetup create fault --noudevsync --table "0 $sectors linear $target 0"
dmsetup mknodes
metadata=/dev/vda
backing=/dev/mapper/fault
if [ "$mode" = log ]; then
    metadata=/dev/mapper/fault
    backing=/dev/vdb
fi
mkfs.ext4 -q -F "$metadata"
mount "$metadata" /mars
chmod 0700 /mars
marsadm create-cluster
modprobe mars
marsadm create-resource test "$backing"
mkdir -p /dev/mars
for i in {1..60}; do
    [ ! -b /dev/mars\!test ] || [ -e /dev/mars/test ] || ln -s ../mars\!test /dev/mars/test
    [ -b /dev/mars/test ] && break
    sleep 1
done
test -b /dev/mars/test
dd if=/dev/urandom of=/tmp/pattern bs=4096 count=1 status=none
dd if=/tmp/pattern of=/dev/mars/test bs=4096 oflag=direct conv=fsync status=none
for i in {1..30}; do
    dd if="$backing" of=/tmp/backing bs=4096 count=1 iflag=direct status=none
    cmp -s /tmp/pattern /tmp/backing && break
    sleep 1
done
cmp /tmp/pattern /tmp/backing
sleep 3
before=$(readlink /mars/resource-test/replay-mars-test | cut -d, -f2)
test -n "$before"
echo "MARS_QEMU_FAULT_BEGIN mode=$mode replay_min=$before"
dmsetup suspend fault --noudevsync --nolockfs
dmsetup reload fault --noudevsync --table "0 $sectors error"
dmsetup resume fault --noudevsync
# A data write may be acknowledged by its durable log before data writeback
# fails. A failed log write itself must be reported to the submitting process.
status=0
timeout 30 dd if=/tmp/pattern of=/dev/mars/test bs=4096 seek=1 oflag=direct conv=fsync status=none || status=$?
test "$status" != 124
test "$status" != 137
if [ "$mode" = log ]; then test "$status" != 0; fi
for i in {1..30}; do
    if dmesg | grep -q 'stopped after storage error'; then break; fi
    sleep 1
done
dmesg | grep 'stopped after storage error'
# Recover the physical path so metadata inspection and cleanup can proceed.
# The logger must retain its failure state despite physical recovery.
dmsetup suspend fault --noudevsync --nolockfs
dmsetup reload fault --noudevsync --table "0 $sectors linear $target 0"
dmsetup resume fault --noudevsync
status=0
timeout 15 dd if=/tmp/pattern of=/dev/mars/test bs=4096 seek=2 oflag=direct conv=fsync status=none || status=$?
test "$status" != 0
test "$status" != 124
test "$status" != 137
after=$(readlink /mars/resource-test/replay-mars-test | cut -d, -f2)
test "$after" = "$before"
echo "MARS_QEMU_FAULT_PINNED mode=$mode replay_min=$after"
# rmmod performs the full in-memory drain even if the fault aborted ext4's
# journal and metadata updates can no longer be persisted.
timeout 90 rmmod mars
echo "MARS_QEMU_FAULT_PASS mode=$mode"
echo MARS_QEMU_PASS
poweroff
