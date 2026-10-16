#!/bin/bash
# Actual WireGuard encryption in an isolated guest, with a peer network namespace.
# run.py needs --extra-module wireguard --extra-binary /path/to/wg.
set -Eeuo pipefail
export PATH=/usr/bin:/bin:/sbin LC_ALL=C
mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /run
trap 'echo MARS_QEMU_WIREGUARD_FAIL; wg show || true; dmesg; poweroff' ERR
hostname mars-test
modprobe virtio_mmio
modprobe virtio_blk
modprobe ext4
modprobe wireguard
ip link set lo up
mkfs.ext4 -q -F /dev/vda
mount /dev/vda /mars
chmod 0700 /mars
marsadm create-cluster
umask 077
wg genkey >/tmp/server.key
wg genkey >/tmp/peer.key
wg pubkey </tmp/server.key >/tmp/server.pub
wg pubkey </tmp/peer.key >/tmp/peer.pub
# Both UDP sockets originate in init_net, so their encrypted transport can use
# loopback even after the peer overlay interface moves to another namespace.
ip link add wg0 type wireguard
ip link add wgpeer type wireguard
wg set wg0 private-key /tmp/server.key listen-port 51820 \
    peer "$(cat /tmp/peer.pub)" allowed-ips 10.99.0.2/32 endpoint 127.0.0.1:51821
wg set wgpeer private-key /tmp/peer.key listen-port 51821 \
    peer "$(cat /tmp/server.pub)" allowed-ips 10.99.0.1/32 endpoint 127.0.0.1:51820
ip addr add 10.99.0.1/32 dev wg0
ip link set wg0 up
ip route add 10.99.0.2/32 dev wg0
mkdir -p /var/run/netns /run/netns
ip netns add peer
ip link set wgpeer netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip addr add 10.99.0.2/32 dev wgpeer
ip netns exec peer ip link set wgpeer up
ip netns exec peer ip route add 10.99.0.1/32 dev wgpeer
modprobe mars mars_interface=wg0 mars_listen_addr=10.99.0.1 mars_source_addr=10.99.0.1
sleep 2
# Packets from loopback must not bypass the authenticated device boundary,
# even if they address the configured WireGuard listener IP.
perl <<'PLAIN'
use strict;
use warnings;
use IO::Socket::INET;
$SIG{ALRM} = sub { die "plaintext bypass timeout\n" };
$SIG{PIPE} = 'IGNORE';
alarm 10;
my $s = IO::Socket::INET->new(PeerAddr => '10.99.0.1', PeerPort => 7777, Proto => 'tcp', Timeout => 2);
if ($s) {
    $s->autoflush(1);
    my $header = pack('Q<Q<s<s<x4', 0x73f0a2ec6148f48d, 1, 72, 0);
    my $desc = pack('Q<Q<l<x4', 1, 0, 1) . pack('a32l<l<l<l<', '.cmd_code', 5, 4, 0, -1);
    print $s $header . $desc . pack('l<', 4);
    my $n = sysread($s, my $reply, 4096);
    die "plaintext bypass accepted\n" if defined($n) && $n > 0;
    close $s;
}
alarm 0;
PLAIN
echo 'PASS plaintext bypass denied'
ip netns exec peer perl <<'PERL'
use strict;
use warnings;
use IO::Socket::INET;
$SIG{ALRM} = sub { die "WireGuard inbound timeout\n" };
alarm 20;
my $s = IO::Socket::INET->new(PeerAddr => '10.99.0.1', PeerPort => 7777, Proto => 'tcp', Timeout => 10)
    or die "authenticated connect: $!";
$s->autoflush(1);
my $header = pack('Q<Q<s<s<x4', 0x73f0a2ec6148f48d, 1, 72, 0);
my $desc = pack('Q<Q<l<x4', 1, 0, 1) . pack('a32l<l<l<l<', '.cmd_code', 5, 4, 0, -1);
print $s $header . $desc . pack('l<', 4) or die "authenticated write: $!";
sysread($s, my $reply, 4096) > 0 or die "authenticated metadata read: $!";
close $s;
alarm 0;
print "PASS encrypted inbound metadata\n";
PERL
wg show wg0 latest-handshakes | awk '$2 > 0 { ok=1 } END { exit !ok }'
# Let MARS establish its own outgoing connection to the authenticated peer.
ip netns exec peer perl <<'PERL' &
use strict;
use warnings;
use IO::Socket::INET;
$SIG{ALRM} = sub { die "WireGuard outbound timeout\n" };
alarm 30;
my $listener = IO::Socket::INET->new(LocalAddr => '10.99.0.2', LocalPort => 7777,
                                    Proto => 'tcp', Listen => 4, ReuseAddr => 1) or die "listen: $!";
open(my $ready, '>', '/tmp/wg-peer-ready') or die $!;
close $ready;
my $s = $listener->accept() or die "accept: $!";
die 'wrong source binding' unless $s->peerhost() eq '10.99.0.1';
my $bytes = '';
while (length($bytes) < 24) {
    my $n = sysread($s, my $part, 24 - length($bytes));
    die 'missing outgoing command' unless defined($n) && $n > 0;
    $bytes .= $part;
}
die 'bad outgoing protocol' unless unpack('Q<', $bytes) == 0x73f0a2ec6148f48d;
close $s;
close $listener;
print "PASS encrypted outbound command and source binding\n";
PERL
peer_pid=$!
for i in {1..50}; do [ -e /tmp/wg-peer-ready ] && break; sleep .1; done
test -e /tmp/wg-peer-ready
ln -s 10.99.0.2 /mars/ips/ip-auth-peer
wait "$peer_pid"
rm /mars/ips/ip-auth-peer
rmmod mars
ip netns del peer
ip link del wg0
if dmesg | grep -E 'BUG:|KASAN:|Oops:|general protection fault|kernel BUG'; then false; fi
echo MARS_QEMU_WIREGUARD_PASS
echo MARS_QEMU_PASS
poweroff
