#!/bin/sh
# Use the compiler family selected by the configured target kernel.
set -eu
release=${1:?usage: dkms-build.sh KERNELRELEASE}
shift
kdir=/lib/modules/$release/build
if grep -q '^CONFIG_CC_IS_CLANG=y$' "$kdir/.config"; then
    set -- LLVM=1 "$@"
fi
exec make -C kernel "KDIR=$kdir" "$@"
