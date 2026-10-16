#!/usr/bin/perl
# SPDX-License-Identifier: GPL-2.0-or-later
# External-build evaluator for the deliberately small subset in kernel/Kconfig.
# Reject unsupported directives instead of silently ignoring dependencies.
use strict;
use warnings;
use Getopt::Long qw(GetOptions);

my $kernel_config;
GetOptions('kernel-config=s' => \$kernel_config) or die "invalid arguments\n";
my (%kernel, %options, @order);
if (defined $kernel_config) {
    open my $fh, '<', $kernel_config or die "read $kernel_config: $!\n";
    while (<$fh>) {
        $kernel{$1} = $2 if /^CONFIG_(\w+)=(.*)$/;
    }
    for my $name (qw(64BIT MODULES BLOCK SYSCTL PROC_FS HIGH_RES_TIMERS)) {
        die "MARS requires CONFIG_$name=y in $kernel_config\n"
            unless ($kernel{$name} // 'n') eq 'y';
    }
}
my ($name, $help);
while (<STDIN>) {
    chomp;
    if (/^config\s+(\w+)\s*$/) {
        $name = $1;
        die "duplicate option $name\n" if exists $options{$name};
        $options{$name} = { depends => [], selects => [] };
        push @order, $name;
        $help = 0;
    } elsif (/^\s*(?:#.*)?$/ || $help) {
        next;
    } elsif (!defined $name) {
        die "unsupported Kconfig syntax at line $.: $_\n";
    } elsif (/^\s+(?:help|---help---)\s*$/) {
        $help = 1;
    } elsif (/^\s+(bool|tristate|int|string)\b.*$/) {
        $options{$name}{type} = $1;
    } elsif (/^\s+depends on\s+(.+)$/) {
        push @{$options{$name}{depends}}, $1;
    } elsif (/^\s+select\s+(\w+)\s*$/) {
        push @{$options{$name}{selects}}, $1;
    } elsif (/^\s+default\s+(.+)$/) {
        die "multiple defaults for $name are unsupported\n" if exists $options{$name}{default};
        $options{$name}{default} = $1;
    } elsif (/^\s+range\s+(\d+)\s+(\d+)\s*$/) {
        $options{$name}{range} = [$1, $2];
    } else {
        die "unsupported Kconfig syntax at line $.: $_\n";
    }
}

my (%values, %active);
sub enabled { return defined $_[0] && $_[0] ne 'n' && $_[0] ne '0' && $_[0] ne ''; }
sub resolve;
sub expression {
    my ($expr) = @_;
    my @tokens;
    pos($expr) = 0;
    while ($expr =~ /\G\s*(&&|\|\||!|\(|\)|\w+)\s*/gc) { push @tokens, $1; }
    die "unsupported dependency expression: $expr\n" unless (pos($expr) // 0) == length($expr);
    my $at = 0;
    my ($atom, $and, $or);
    $atom = sub {
        my $token = $tokens[$at++] // die "incomplete expression: $expr\n";
        return !$atom->() if $token eq '!';
        if ($token eq '(') {
            my $value = $or->();
            die "missing closing parenthesis: $expr\n" unless ($tokens[$at++] // '') eq ')';
            return $value;
        }
        return 1 if $token eq 'y' || $token eq 'm';
        return 0 if $token eq 'n';
        die "invalid expression: $expr\n" unless $token =~ /^\w+$/;
        return enabled(resolve($token));
    };
    $and = sub {
        my $value = $atom->();
        while (($tokens[$at] // '') eq '&&') { $at++; my $rhs = $atom->(); $value = $value && $rhs; }
        return $value;
    };
    $or = sub {
        my $value = $and->();
        while (($tokens[$at] // '') eq '||') { $at++; my $rhs = $and->(); $value = $value || $rhs; }
        return $value;
    };
    my $value = $or->();
    die "trailing tokens in dependency: $expr\n" unless $at == @tokens;
    return $value;
}
sub resolve {
    my ($key) = @_;
    return $values{$key} if exists $values{$key};
    if (!exists $options{$key}) {
        # Without a kernel config, only the MARS options are evaluated.
        return defined $kernel_config ? ($kernel{$key} // 'n') : 'y';
    }
    die "dependency cycle involving $key\n" if $active{$key}++;
    my $opt = $options{$key};
    my $type = $opt->{type} // die "missing type for $key\n";
    my $value = $ENV{$key} // $opt->{default} // ($type eq 'bool' ? 'n' : die "missing default for $key\n");
    $value = 'm' if $key eq 'MARS';
    if ($type eq 'bool' || $type eq 'tristate') {
        die "$key must be " . ($type eq 'bool' ? 'y or n' : 'y, m or n') . "\n"
            unless $value =~ ($type eq 'bool' ? qr/^[yn]$/ : qr/^[ymn]$/);
    } elsif ($type eq 'int') {
        die "$key must be a nonnegative decimal integer\n" unless $value =~ /^(?:0|[1-9][0-9]*)$/ && $value <= 2147483647;
        if ($opt->{range}) {
            my ($low, $high) = @{$opt->{range}};
            die "$key must be in range $low..$high\n" if $value < $low || $value > $high;
        }
    } elsif ($type eq 'string') {
        die "$key must be a quoted C string without control characters\n"
            unless $value =~ /^"(?:[^"\\\x00-\x1f]|\\["\\])*"$/;
    }
    for my $dep (@{$opt->{depends}}) {
        next if expression($dep);
        die "$key=$value requires $dep\n" if exists $ENV{$key} && enabled($value);
        $value = 'n';
    }
    if (enabled($value) && defined $kernel_config) {
        for my $selected (@{$opt->{selects}}) {
            die "MARS requires CONFIG_$selected=y or m in $kernel_config\n"
                unless enabled($kernel{$selected});
        }
    }
    delete $active{$key};
    return $values{$key} = $value;
}
resolve($_) for @order;
print "/* Generated from kernel/Kconfig; do not edit. */\n#ifndef MARS_CONFIG_H\n#define MARS_CONFIG_H\n";
print "#ifndef CONFIG_MARS_HAVE_BIGMODULE\n#define CONFIG_MARS_HAVE_BIGMODULE 1\n#endif\n";
for my $key (@order) {
    my $value = $values{$key};
    next if $options{$key}{type} eq 'tristate';
    if ($value eq 'n') { print "/* CONFIG_$key is unset */\n"; next; }
    $value = 1 if $options{$key}{type} eq 'bool';
    print "#ifndef CONFIG_$key\n#define CONFIG_$key $value\n#endif\n";
}
print "#endif /* MARS_CONFIG_H */\n";
