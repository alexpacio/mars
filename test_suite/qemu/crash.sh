#!/bin/bash
# The host kills QEMU after acknowledged writes while dm-delay holds writeback.
set -Eeuo pipefail
export PATH=/usr/bin:/bin:/sbin LC_ALL=C
mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /run
trap 'echo MARS_QEMU_FAIL line=$LINENO; dmesg; poweroff' ERR
hostname mars-test
modprobe virtio_mmio
modprobe virtio_blk
modprobe ext4
modprobe dm-delay
ip link set lo up
for i in {1..50}; do [ -b /dev/vdb ] && break; sleep .1; done
stage=write
case "$(cat /proc/cmdline)" in *mars_crash=recover*) stage=recover;; esac
perl -e 'print pack("N*", 0..262143)' >/tmp/pattern
cp /tmp/pattern /tmp/expected
dd if=/dev/zero of=/tmp/expected bs=4096 seek=64 count=64 conv=notrunc status=none
dd if=/tmp/pattern of=/tmp/expected bs=4096 skip=80 seek=80 count=2 conv=notrunc status=none
if [ "$stage" = write ]; then
    mkfs.ext4 -q -F /dev/vda
    mount /dev/vda /mars
    chmod 0700 /mars
    # Delay data writes/flushes but not reads. Log I/O uses a separate disk.
    dmsetup create delayed --noudevsync --table '0 262144 delay /dev/vdb 0 0 /dev/vdb 0 60000'
    dmsetup mknodes
    marsadm --ip=127.0.0.1 create-cluster
    modprobe mars
    marsadm create-resource test /dev/mapper/delayed
    for i in {1..60}; do [ -b /dev/mars/test ] && break; sleep 1; done
    test -b /dev/mars/test
    # Persist the resource metadata before testing data-write acknowledgments.
    sync
    dd if=/tmp/pattern of=/dev/mars/test bs=4096 oflag=direct status=none
    blkdiscard -o 262144 -l 262144 /dev/mars/test
    dd if=/tmp/pattern of=/dev/mars/test bs=4096 skip=80 seek=80 count=2 oflag=direct conv=notrunc status=none
    dd if=/dev/vdb of=/tmp/backing bs=4096 count=256 iflag=direct status=none
    if cmp -s /tmp/expected /tmp/backing; then
        echo 'writeback completed before crash; test is inconclusive'
        false
    fi
    echo MARS_QEMU_CRASH_ARMED
    sleep 180
    false
else
    dmsetup create delayed --noudevsync --table '0 262144 linear /dev/vdb 0'
    dmsetup mknodes
    mount /dev/vda /mars
    modprobe mars
    for i in {1..90}; do [ -b /dev/mars/test ] && break; sleep 1; done
    test -b /dev/mars/test
    dd if=/dev/mars/test of=/tmp/readback bs=4096 count=256 iflag=direct status=none
    cmp /tmp/expected /tmp/readback
    timeout 90 marsadm secondary test
    timeout 90 marsadm down test
    rmmod mars
    dd if=/dev/vdb of=/tmp/backing bs=4096 count=256 iflag=direct status=none
    cmp /tmp/expected /tmp/backing
    echo MARS_QEMU_CRASH_RECOVERY_PASS
    echo MARS_QEMU_PASS
    poweroff
fi
