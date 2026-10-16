#!/bin/bash
# Malformed network messages must be rejected without corrupting the kernel.
set -Eeuo pipefail
export PATH=/usr/bin:/bin:/sbin LC_ALL=C
mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /run
trap 'echo MARS_QEMU_NETWORK_FAIL; dmesg; poweroff' ERR
hostname mars-test
modprobe virtio_mmio
modprobe virtio_blk
modprobe ext4
ip link set lo up
mkfs.ext4 -q -F /dev/vda
mount /dev/vda /mars
chmod 0700 /mars
marsadm create-cluster
# A non-WireGuard interface must not satisfy authenticated transport policy.
if modprobe mars mars_listen_addr=0.0.0.0 mars_interface=lo; then
    echo 'accepted non-WireGuard interface'
    false
fi
# Bare modprobe must not expose privileged commands to local TCP clients.
modprobe mars
perl -MIO::Socket::INET -e 'exit(IO::Socket::INET->new(PeerAddr => "127.0.0.1", PeerPort => 7777, Proto => "tcp", Timeout => 2) ? 1 : 0)'
rmmod mars
# The parser tests use an explicitly isolated, unauthenticated loopback socket.
modprobe mars mars_allow_insecure=1 mars_exports=allowed
mkdir -p /mars/resource-probe /mars/resource-allowed /mars/ips
ln -s /tmp /mars/escape
ln -s /dev/vdb /mars/resource-probe/data-mars-test
ln -s /dev/vdb /mars/resource-allowed/data-mars-test
mkdir -p /mars/resource-allowed/todo-mars-test
ln -s 1 /mars/resource-allowed/todo-mars-test/attach
sleep 3
perl <<'PERL'
use strict;
use warnings;
use IO::Socket::INET;
use Socket qw(SHUT_WR);
$SIG{ALRM} = sub { die "network test timeout\n" };
sub header { pack('Q<Q<s<s<x4', 0x73f0a2ec6148f48d, 0x1234, $_[0], $_[1] // 0) }
sub field { pack('a32l<l<l<l<', $_[0], $_[1], $_[2], 0x7ffffff0, $_[3] // 0x7ffffff0) }
sub descriptor { pack('Q<Q<l<x4', 0x1234, 0xfeed, scalar(@_)) . join('', @_) }
sub frame {
    my ($fields, $payload) = @_;
    my $desc = descriptor(@$fields);
    return header(length($desc)) . $desc . $payload;
}
sub command {
    my ($op, @strings) = @_;
    my @fields = (field('.cmd_code', 5, 4));
    my $payload = pack('l<', $op);
    for my $i (0 .. $#strings) {
        push @fields, field('.cmd_str'.($i + 1), 3, 8);
        $payload .= pack('l<', length($strings[$i]) + 1) . $strings[$i] . "\0";
    }
    return frame(\@fields, $payload);
}
sub exchange {
    my ($name, $bytes, $expect_data) = @_;
    alarm 15;
    my $s = IO::Socket::INET->new(PeerAddr => '127.0.0.1', PeerPort => 7777,
                                 Proto => 'tcp', Timeout => 5) or die "$name connect: $!";
    $s->autoflush(1);
    print $s $bytes or die "$name write: $!";
    my $n = sysread($s, my $reply, 4096);
    # ECONNRESET is also a valid rejection when unread payload remains.
    die "$name unexpectedly accepted\n" if !$expect_data && defined($n) && $n > 0;
    die "$name valid request rejected: $!\n" if $expect_data && (!defined($n) || !$n);
    close $s;
    alarm 0;
    print "PASS $name\n";
}
sub exact {
    my ($s, $len) = @_;
    my $buf = '';
    while (length($buf) < $len) {
        my $n = sysread($s, my $part, $len - length($buf));
        die "short response\n" unless defined($n) && $n > 0;
        $buf .= $part;
    }
    return $buf;
}
sub denied_connect {
    my ($name, $path) = @_;
    alarm 15;
    my $s = IO::Socket::INET->new(PeerAddr => '127.0.0.1', PeerPort => 7777,
                                 Proto => 'tcp', Timeout => 5) or die "$name connect: $!";
    $s->autoflush(1);
    print $s command(2, $path) or die "$name write: $!";
    my ($magic, $cookie, $bytes, $index) = unpack('Q<Q<s<s<', exact($s, 24));
    die 'bad response header' unless $magic == 0x73f0a2ec6148f48d && $bytes > 24;
    die 'kernel address exposed in cookie' unless $cookie > 0 && $cookie <= 16;
    my $desc = exact($s, $bytes);
    my ($sender, $receiver, $count) = unpack('Q<Q<l<', $desc);
    die 'kernel address exposed in descriptor' unless $sender <= 16 && !$receiver;
    my $status;
    for my $i (0 .. $count - 1) {
        my ($field, $type, $size) = unpack('Z32l<l<', substr($desc, 24 + 48 * $i, 48));
        next if $type == 2;
        $size = unpack('l<', exact($s, 4)) if $type == 3;
        my $value = exact($s, $size);
        $status = unpack('l<', $value) if $field eq '.cmd_int1';
    }
    die "$name did not return EACCES\n" unless defined($status) && $status == -13;
    close $s;
    alarm 0;
    print "PASS $name\n";
}
sub mref_request {
    my ($label, $flags, $payload, $path) = @_;
    $path //= '/mars/resource-allowed/data-mars-test';
    alarm 15;
    my $s = IO::Socket::INET->new(PeerAddr => '127.0.0.1', PeerPort => 7777,
                                 Proto => 'tcp', Timeout => 5) or die "$label connect: $!";
    $s->autoflush(1);
    print $s command(2, $path) or die $!;
    my (undef, undef, $bytes) = unpack('Q<Q<s<s<', exact($s, 24));
    my $desc = exact($s, $bytes);
    my (undef, undef, $count) = unpack('Q<Q<l<', $desc);
    my $status;
    for my $i (0 .. $count - 1) {
        my ($f, $type, $size) = unpack('Z32l<l<', substr($desc, 24 + 48*$i, 48));
        next if $type == 2;
        $size = unpack('l<', exact($s, 4)) if $type == 3;
        my $value = exact($s, $size);
        $status = unpack('l<', $value) if $f eq '.cmd_int1';
    }
    die "$label backing CONNECT failed\n" unless defined($status) && $status == 0;
    # Reuse the first command descriptor, then introduce a distinct mref descriptor.
    my $m = descriptor(field('.ref_pos', 5, 8), field('.ref_len', 5, 4), field('.ref_flags', 6, 4));
    print $s header(0, 0), pack('l<l<', 5 | (defined($payload) ? 256 : 0), 0),
             header(length($m), 1), $m, pack('q<l<L<', 0, 512, $flags),
             ($payload // '') or die $!;
    if (defined $payload) {
        my %cache = (0 => $desc);
        my %values;
        # A successful write returns a command followed by a callback mref.
        for my $structure (0 .. 1) {
            my (undef, undef, $len, $slot) = unpack('Q<Q<s<s<', exact($s, 24));
            $cache{$slot} = exact($s, $len) if $len;
            my $d = $cache{$slot} // die 'missing callback descriptor';
            my $nfields = unpack('l<', substr($d, 16, 4));
            for my $i (0 .. $nfields - 1) {
                my ($f, $type, $size) = unpack('Z32l<l<', substr($d, 24 + 48*$i, 48));
                next if $type == 2;
                $size = unpack('l<', exact($s, 4)) if $type == 3;
                my $value = exact($s, $size);
                $values{$f} = unpack('l<', $value) if $size == 4;
            }
        }
        die 'missing write callback' unless $values{'.cmd_code'} == 6;
        die 'remote SKIP_SYNC retained' if $values{'.ref_flags'} & 32;
        die 'write completion failed' unless defined($values{'._object_cb.cb_error'}) && $values{'._object_cb.cb_error'} >= 0;
        open(my $backing, '<', $path) or die "backing open: $!";
        die 'completed write data mismatch' unless exact($backing, 512) eq $payload;
        close $backing;
    } else {
        my $n = sysread($s, my $reply, 4096);
        die "$label unexpectedly accepted\n" if defined($n) && $n > 0;
    }
    close $s;
    alarm 0;
    print "PASS $label\n";
}
mref_request('payload-free write rejected', 8 | 16 | 64);
mref_request('read discard rejected', 128);
mref_request('unknown request flag rejected', 1 << 15);
mref_request('remote block write cannot skip durability', 8 | 16 | 32, 'D' x 512);
mref_request('remote log write cannot skip durability', 8 | 16 | 32, 'L' x 512,
             '/mars/resource-allowed/log-000001-mars-test');
denied_connect('arbitrary backing path denied', '/dev/vdb');
denied_connect('resource outside exports denied', '/mars/resource-probe/data-mars-test');
denied_connect('another node data export denied', '/mars/resource-allowed/data-other');
exchange('valid metadata', command(4, '/mars'), 1);
exchange('oversized descriptor', header(8192), 0);
exchange('negative descriptor length', header(-1), 0);
exchange('invalid cache index', header(0, 16), 0);
exchange('invalid negative cache index', header(0, -2), 0);
exchange('end marker is not command', header(0, -1), 0);
exchange('oversized field count', header(24) . pack('Q<Q<l<x4', 0, 0, 2147483647), 0);
exchange('unterminated field name', frame([field('x' x 32, 5, 4)], pack('l<', 4)), 0);
exchange('wrong local field size', frame([field('.cmd_code', 5, 128)], 'x' x 128), 0);
exchange('duplicate field', frame([field('.cmd_code', 5, 4), field('.cmd_code', 5, 4)], pack('l<l<', 0, 4)), 0);
exchange('unknown command bits', command(1024), 0);
exchange('oversized string', frame([field('.cmd_str1', 3, 8)], pack('l<', 67108864)), 0);
exchange('unterminated string', frame([field('.cmd_str1', 3, 8)], pack('l<', 4) . 'abcd'), 0);
exchange('embedded string NUL', frame([field('.cmd_str1', 3, 8)], pack('l<', 4) . "a\0b\0"), 0);
exchange('unknown field consumed and receiver offsets ignored',
         frame([field('.future', 5, 4, 0), field('.cmd_code', 5, 4, 0)], pack('l<l<', 0x7fffffff, 4)), 1);
exchange('metadata arbitrary root', command(4, '/etc'), 0);
exchange('metadata traversal', command(4, '/mars|/mars/../etc/'), 0);
exchange('metadata symlink', command(4, '/mars|/mars/escape/'), 0);
exchange('push traversal', command(7, 'value', '/mars/../tmp/escaped'), 0);
exchange('push symlink parent', command(7, 'value', '/mars/escape/escaped'), 0);
exchange('push local device retarget', command(7, '/dev/vda', '/mars/resource-probe/data-mars-test'), 0);
exchange('push transaction log retarget', command(7, '/dev/vda', '/mars/resource-probe/log-000001-mars-test'), 0);
exchange('push check arbitrary path', command(8, 'peer', '/etc/passwd'), 0);
exchange('incomplete command deadline', substr(header(0), 0, 8), 0);
exchange('valid metadata after attacks', command(4, '/mars'), 1);
PERL
test ! -e /tmp/escaped
test ! -L /tmp/escaped
test "$(readlink /mars/resource-probe/data-mars-test)" = /dev/vdb
# Exercise the opposite direction: a server sends malicious GETENTS replies.
# Admission must protect the host even when a peer has the correct cluster UUID.
perl <<'PULL'
use strict;
use warnings;
use IO::Socket::INET;
use POSIX qw(_exit);
$SIG{PIPE} = 'IGNORE';
sub exact {
    my ($s, $len) = @_;
    my $out = '';
    while (length($out) < $len) {
        my $n = sysread($s, my $part, $len - length($out));
        return undef unless defined($n) && $n > 0;
        $out .= $part;
    }
    return $out;
}
sub field { pack('a32l<l<l<l<', $_[0], $_[1], $_[2], 0, -1) }
my @fields = (field('.d_name', 3, 8), field('.d_path', 3, 8), field('.new_link', 3, 8),
              field('.new_stat', 2, 0), field('.new_stat.mode', 5, 2),
              field('.new_stat.mtime', 2, 0), field('.new_stat.mtime.tv_sec', 5, 8),
              field('.new_stat.mtime.tv_nsec', 5, 8));
my $desc = pack('Q<Q<l<x4', 1, 0, scalar(@fields)) . join('', @fields);
sub dent {
    my ($path, $target, $initial, $name) = @_;
    $name //= (split('/', $path))[-1];
    my $payload = join('', map { pack('l<', length($_) + 1) . $_ . "\0" } ($name, $path, $target));
    $payload .= pack('S<q<q<', 0120777, time() + 5, 0);
    return pack('Q<Q<s<s<x4', 0x73f0a2ec6148f48d, 1, $initial ? length($desc) : 0, 0)
         . ($initial ? $desc : '') . $payload;
}
my @cases = (
    ['outside root', '/tmp/pull-escaped', 'value', 1],
    ['traversal', '/mars/../tmp/pull-escaped', 'value', 1],
    ['basename mismatch', '/mars/pull-mismatch', 'value', 1, 'different-name'],
    ['local data retarget', '/mars/resource-probe/data-mars-test', '/dev/vda', 0],
    ['transaction log retarget', '/mars/resource-probe/log-000001-mars-test', '/dev/vda', 0],
    ['symlink parent', '/mars/escape/pull-escaped', 'value', 0],
);
my $uuid = readlink('/mars/uuid') or die 'missing cluster UUID';
for my $index (0 .. $#cases) {
    my ($label, $path, $target, $reject, $name) = @{$cases[$index]};
    my $ip = '127.0.0.'.($index + 2);
    my $listener = IO::Socket::INET->new(LocalAddr => $ip, LocalPort => 7777,
                                        Proto => 'tcp', Listen => 4, ReuseAddr => 1) or die "listen: $!";
    my $marker = "/mars/pull-valid-$index";
    my $result = "/tmp/pull-rejected-$index";
    my $pid = fork();
    die "fork: $!" unless defined $pid;
    if (!$pid) {
        $SIG{ALRM} = sub { die "pull $label timeout\n" };
        alarm 30;
        my $s = $listener->accept() or die "accept: $!";
        $s->autoflush(1);
        my %cache;
        # The first command can be NOTIFY; wait for GETENTS.
        while (1) {
            my $header = exact($s, 24) // die 'missing request';
            my ($magic, $cookie, $len, $slot) = unpack('Q<Q<s<s<', $header);
            die 'bad request magic' unless $magic == 0x73f0a2ec6148f48d;
            $cache{$slot} = exact($s, $len) if $len;
            my $d = $cache{$slot} // die 'missing descriptor';
            my $count = unpack('l<', substr($d, 16, 4));
            my $op;
            for my $i (0 .. $count - 1) {
                my ($f, $type, $size) = unpack('Z32l<l<', substr($d, 24 + 48*$i, 48));
                next if $type == 2;
                $size = unpack('l<', exact($s, 4)) if $type == 3;
                my $value = exact($s, $size);
                $op = unpack('l<', $value) if $f eq '.cmd_code';
            }
            last if defined($op) && $op == 4;
        }
        print $s dent('/mars/uuid', $uuid, 1), dent($marker, 'valid', 0),
                 dent($path, $target, 0, $name), pack('Q<Q<s<s<x4', 0x73f0a2ec6148f48d, 0, 0, -1)
            or die "send reply: $!";
        if ($reject) {
            my $n = sysread($s, my $next, 4096);
            die 'invalid snapshot accepted' if defined($n) && $n > 0;
            open(my $out, '>', $result) or die $!;
            close $out;
        } else {
            sleep 25;
        }
        _exit(0);
    }
    close $listener;
    my $peer = "/mars/ips/ip-pull-$index";
    symlink($ip, $peer) or die $!;
    my $ready = 0;
    for (1 .. 200) {
        if ($reject ? -e $result : -l $marker) { $ready = 1; last; }
        select(undef, undef, undef, .1);
    }
    unlink $peer;
    kill 'TERM', $pid;
    waitpid($pid, 0);
    die "pull $label not processed\n" unless $ready;
    die 'outside path modified' if -e '/tmp/pull-escaped' || -l '/tmp/pull-escaped';
    die 'local backing retargeted' unless readlink('/mars/resource-probe/data-mars-test') eq '/dev/vdb';
    die 'log retargeted' if -l '/mars/resource-probe/log-000001-mars-test';
    die 'invalid snapshot partly applied' if $reject && -l $marker;
    unlink $marker if -l $marker;
    print "PASS pulled metadata $label\n";
}
PULL
rm /mars/resource-probe/data-mars-test
rmmod mars
modprobe mars mars_allow_insecure=1 mars_listen_addr=::1
perl <<'PERL6'
use strict;
use warnings;
use Socket qw(AF_INET6 SOCK_STREAM IPPROTO_TCP inet_pton pack_sockaddr_in6);
$SIG{ALRM} = sub { die "IPv6 timeout\n" };
alarm 15;
socket(my $s, AF_INET6, SOCK_STREAM, IPPROTO_TCP) or die "IPv6 socket: $!";
connect($s, pack_sockaddr_in6(7777, inet_pton(AF_INET6, '::1'))) or die "IPv6 connect: $!";
my $h = pack('Q<Q<s<s<x4', 0x73f0a2ec6148f48d, 0x1234, 72, 0);
my $d = pack('Q<Q<l<x4', 0x1234, 0, 1) . pack('a32l<l<l<l<', '.cmd_code', 5, 4, 0, -1);
my $packet = $h . $d . pack('l<', 4);
syswrite($s, $packet) == length($packet) or die "IPv6 write: $!";
sysread($s, my $reply, 4096) > 0 or die "IPv6 metadata reply: $!";
close $s;
alarm 0;
print "PASS IPv6 metadata exchange\n";
PERL6
rmmod mars
if dmesg | grep -E 'BUG:|KASAN:|Oops:|general protection fault|kernel BUG'; then
    false
fi
echo MARS_QEMU_NETWORK_PASS
echo MARS_QEMU_PASS
poweroff
