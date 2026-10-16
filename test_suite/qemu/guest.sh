#!/bin/bash
set -Eeuo pipefail
export PATH=/usr/bin:/bin:/sbin LC_ALL=C
mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /run
fail() {
    echo "MARS_QEMU_FAIL line=$1"
    ls -l /dev /dev/mars /sys/block || true
    cat /proc/sys/mars/info || true
    dmesg || true
    find /mars -maxdepth 3 -type l -printf '%p -> %l\n' || true
    poweroff
}
trap 'fail "$LINENO"' ERR
hostname mars-test
echo "MARS_QEMU_KERNEL $(uname -r)"
modprobe virtio_mmio
modprobe virtio_blk
modprobe virtio_net
modprobe ext4
ip link set lo up
iface=$(ls /sys/class/net | grep -v '^lo$' | head -1)
ip link set "$iface" up
ip addr add 10.0.2.15/24 dev "$iface"
for i in {1..50}; do [ -b /dev/vdb ] && break; sleep .1; done
mkfs.ext4 -q -F /dev/vda
mount /dev/vda /mars
chmod 0700 /mars
marsadm create-cluster
modprobe mars
cat /proc/sys/mars/version
cat /proc/sys/mars/lamport_clock
marsadm create-resource test /dev/vdb
mkdir -p /dev/mars
for i in {1..60}; do
    if [ -b '/dev/mars!test' ] && [ ! -e /dev/mars/test ]; then
        ln -s '../mars!test' /dev/mars/test
    fi
    [ -b /dev/mars/test ] && break
    sleep 1
done
ls -l /dev/mars /sys/block
test -b /dev/mars/test
chmod 0600 /dev/mars/test
test "$(blockdev --getsize64 /dev/mars/test)" = 134217728
test "$(blockdev --getss /dev/mars/test)" = "$(blockdev --getss /dev/vdb)"
test "$(blockdev --getpbsz /dev/mars/test)" = "$(blockdev --getpbsz /dev/vdb)"
mkdir -p /sys/kernel/tracing
mount -t tracefs tracefs /sys/kernel/tracing
echo 1 >/sys/kernel/tracing/events/mars/enable
dd if=/dev/urandom of=/tmp/pattern bs=4096 count=256 status=none
dd if=/tmp/pattern of=/dev/mars/test bs=4096 conv=fsync status=none
dd if=/dev/mars/test of=/tmp/readback bs=4096 count=256 iflag=direct status=none
cmp /tmp/pattern /tmp/readback
# Exercise deallocation, overlap with a later write, adjacent data and zeroout.
blkdiscard -o 262144 -l 262144 /dev/mars/test
dd if=/dev/zero of=/tmp/pattern bs=4096 seek=64 count=64 conv=notrunc status=none
dd if=/dev/urandom of=/tmp/rewrite bs=4096 count=2 status=none
dd if=/tmp/rewrite of=/dev/mars/test bs=4096 seek=80 conv=fsync,notrunc status=none
dd if=/tmp/rewrite of=/tmp/pattern bs=4096 seek=80 conv=notrunc status=none
blkdiscard -z -o 786432 -l 32768 /dev/mars/test
dd if=/dev/zero of=/tmp/pattern bs=4096 seek=192 count=8 conv=notrunc status=none
if [ "$(blockdev --getss /dev/mars/test)" = 512 ]; then
    blkdiscard -o 5632 -l 1536 /dev/mars/test
    dd if=/dev/zero of=/tmp/pattern bs=512 seek=11 count=3 conv=notrunc status=none
else
    # Use the raw x86 Linux BLKDISCARD ioctl: blkdiscard(8) can clamp ranges.
    perl -MFcntl=O_RDWR -e 'sysopen(my $f,$ARGV[0],O_RDWR) or die $!; my $r=pack("QQ",512,512); ioctl($f,0x1277,$r) and die "unaligned discard accepted"' /dev/mars/test
fi
perl -MFcntl=O_RDWR -e 'sysopen(my $f,$ARGV[0],O_RDWR) or die $!; my $r=pack("QQ",134217728,4096); ioctl($f,0x1277,$r) and die "out-of-range discard accepted"' /dev/mars/test
dd if=/dev/mars/test of=/tmp/readback bs=4096 count=256 iflag=direct status=none
cmp /tmp/pattern /tmp/readback
for i in {1..30}; do
    [ "$(readlink /mars/resource-test/actual-mars-test/open-count)" = 0 ] && break
    sleep 1
done
timeout 90 marsadm secondary test
timeout 90 marsadm down test
if [ "$(cat /sys/block/vdb/queue/discard_max_bytes)" != 0 ]; then
    grep 'phase=discard.*error=0' /sys/kernel/tracing/trace
else
    grep 'phase=discard.*error=-95' /sys/kernel/tracing/trace
fi
rmmod mars
dd if=/dev/vdb of=/tmp/backing bs=4096 count=256 iflag=direct status=none
cmp /tmp/pattern /tmp/backing
echo MARS_QEMU_DISCARD_PASS
echo MARS_QEMU_BLOCK_PASS
modprobe mars
cat /proc/sys/mars/version
timeout 90 marsadm attach test
timeout 90 marsadm resume-fetch-local test
timeout 90 marsadm resume-replay-local test
timeout 90 marsadm primary test
for i in {1..60}; do [ -b /dev/mars/test ] && break; sleep 1; done
test -b /dev/mars/test
chmod 0600 /dev/mars/test
mkfs.ext4 -q -F -E nodiscard /dev/mars/test
mount /dev/mars/test /mnt
dd if=/tmp/pattern of=/mnt/pattern bs=1M conv=fsync status=none
cmp /tmp/pattern /mnt/pattern
dd if=/tmp/pattern of=/mnt/to-delete bs=1M conv=fsync status=none
rm /mnt/to-delete
fstrim -v -l 16777216 /mnt
cmp /tmp/pattern /mnt/pattern
umount /mnt
for i in {1..30}; do
    [ "$(readlink /mars/resource-test/actual-mars-test/open-count)" = 0 ] && break
    sleep 1
done
timeout 90 marsadm secondary test
timeout 90 marsadm down test
rmmod mars
mount -o ro,noload /dev/vdb /mnt
cmp /tmp/pattern /mnt/pattern
umount /mnt
echo MARS_QEMU_FILESYSTEM_PASS
echo MARS_QEMU_FSTRIM_PASS
echo MARS_QEMU_PASS
poweroff
