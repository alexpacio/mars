#!/bin/sh
# Build a staged release tree, preserving artifacts and the working checkout.
set -eu
kdir=${1:?usage: build-module.sh KDIR OUTPUT [gcc|clang]}
output=${2:?missing output directory}
compiler=${3:-gcc}
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
version=$(git -C "$root" rev-parse --short=12 HEAD 2>/dev/null || printf unversioned)
python3 "$root/scripts/stage-source.py" "$output/source" --version "ci-$version"
case "$compiler" in
    gcc) set -- CC=gcc ;;
    clang) set -- LLVM=1 ;;
    *) echo "compiler must be gcc or clang" >&2; exit 2 ;;
esac
make -C "$output/source/kernel" "KDIR=$kdir" W=1 -j"${JOBS:-2}" "$@" > "$output/build.log" 2>&1 || {
    cat "$output/build.log" >&2
    exit 1
}
modinfo "$output/source/kernel/mars.ko" > "$output/modinfo.txt"
cat "$output/build.log"
