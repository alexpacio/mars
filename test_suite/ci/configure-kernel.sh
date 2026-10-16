#!/bin/sh
# Configure a vanilla x86-64 target for compile tests and optional VM tests.
set -eu
kdir=${1:?usage: configure-kernel.sh KDIR [normal|kasan|kcsan] [gcc|clang]}
profile=${2:-normal}
compiler=${3:-gcc}
case "$compiler" in
    gcc) set -- CC=gcc ;;
    clang) set -- LLVM=1 ;;
    *) exit 2 ;;
esac
make -C "$kdir" "$@" x86_64_defconfig
"$kdir/scripts/config" --file "$kdir/.config" \
    -e MODULES -e BLOCK -e SYSCTL -e PROC_FS -e HIGH_RES_TIMERS \
    -e LZO_COMPRESS -e LZO_DECOMPRESS -e LZ4_COMPRESS -e LZ4_DECOMPRESS \
    -e ZLIB_INFLATE -e ZLIB_DEFLATE -e CRYPTO_MD5 -e CRYPTO_CRC32 \
    -e CRYPTO_CRC32C -e CRYPTO_SHA1 -e VIRTIO_MMIO -e VIRTIO_MMIO_CMDLINE_DEVICES \
    -e VIRTIO_BLK -e VIRTIO_NET -e DEVTMPFS -e BLK_DEV_INITRD -e RD_GZIP \
    -e EXT4_FS -e BLK_DEV_DM -e DM_DELAY -e NET_NS -e WIREGUARD \
    -e DEBUG_KERNEL -e DEBUG_FS -e TRACING -e FTRACE \
    -e STACKTRACE -e FAULT_INJECTION -e FAILSLAB -e FAIL_PAGE_ALLOC \
    -e FAULT_INJECTION_DEBUG_FS -e PROVE_LOCKING -e DEBUG_ATOMIC_SLEEP \
    -d MODULE_SIG_FORCE -d WERROR --set-str SYSTEM_TRUSTED_KEYS ''
case "$profile" in
    normal) ;;
    kasan) "$kdir/scripts/config" --file "$kdir/.config" -e KASAN -e KASAN_GENERIC -e KASAN_INLINE ;;
    kcsan) "$kdir/scripts/config" --file "$kdir/.config" -e KCSAN -e KCSAN_STRICT ;;
    *) echo "unknown profile: $profile" >&2; exit 2 ;;
esac
make -C "$kdir" "$@" olddefconfig
