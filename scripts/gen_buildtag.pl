#!/usr/bin/perl
# SPDX-License-Identifier: GPL-2.0-or-later
# No wall-clock, hostname, username or absolute path enters the module.
use strict;
use warnings;
use POSIX qw(strftime);
my ($source, $release) = @ARGV;
die "usage: $0 SOURCE KERNELRELEASE\n" unless defined $release;
chdir $source or die "chdir $source: $!\n";
my $version = $ENV{MARSVERSION};
if (!defined $version || !length $version) {
    for my $path ('DISTVERSION', '../DISTVERSION') {
        if (open my $fh, '<', $path) { $version = <$fh>; chomp $version; last; }
    }
}
if (!defined $version || !length $version) {
    # Avoid picking up an unrelated parent repository around an exported tree.
    if (-e '../.git') {
        if (open my $git, '-|', 'git', 'describe', '--tags', '--always', '--dirty') {
            local $/; $version = <$git>; close $git; $version =~ s/\s+$// if defined $version;
        }
    }
}
$version = 'unversioned' unless defined $version && length $version;
my $epoch = $ENV{SOURCE_DATE_EPOCH} // '';
$epoch = 0 unless length $epoch;
die "SOURCE_DATE_EPOCH must be a nonnegative integer\n" unless $epoch =~ /^\d+$/;
my $date = $epoch ? strftime('%Y-%m-%d %H:%M:%S UTC', gmtime($epoch)) : 'unspecified';
sub quoted {
    my ($value) = @_;
    die "build metadata contains a control character\n" if $value =~ /[\x00-\x1f\x7f]/;
    $value =~ s/(["\\])/\\$1/g;
    return '"' . $value . '"';
}
print "/* Generated reproducible build metadata; do not edit. */\n";
print '#define BUILDTAG ' . quoted("$version-for-$release") . "\n";
print "#define BUILDHOST \"reproducible\"\n";
print '#define BUILDDATE ' . quoted($date) . "\n";
