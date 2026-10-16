#!/usr/bin/env python3
"""Run a reproducible fio workload and retain JSON results and host metadata."""

import argparse
import json
import platform
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import time


def positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="existing block device or test file")
    parser.add_argument("output", type=Path, help="new fio JSON output file")
    parser.add_argument("--workload", choices=("read", "randread", "write", "randwrite"), default="randread")
    parser.add_argument("--allow-write", action="store_true", help="allow overwriting the target for write workloads")
    parser.add_argument("--block-size", default="4k")
    parser.add_argument("--depth", type=positive, default=32)
    parser.add_argument("--jobs", type=positive, default=1)
    parser.add_argument("--seconds", type=positive, default=30)
    parser.add_argument("--size", default="1G", help="working set size, at most target size")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    writing = args.workload in ("write", "randwrite")
    if writing and not args.allow_write:
        parser.error("write workloads overwrite target data; select --allow-write explicitly")
    try:
        mode = args.target.stat().st_mode
    except OSError as error:
        parser.error(str(error))
    if not (stat.S_ISREG(mode) or stat.S_ISBLK(mode)):
        parser.error("target must be an existing regular file or block device")
    metadata_path = args.output.with_suffix(args.output.suffix + ".meta.json")
    if args.output.exists() or metadata_path.exists():
        parser.error("output and metadata files must be new")
    command = [
        "fio", "--name=mars", f"--filename={args.target.resolve()}",
        f"--rw={args.workload}", f"--bs={args.block_size}", "--ioengine=libaio",
        "--direct=1", f"--iodepth={args.depth}", f"--numjobs={args.jobs}",
        f"--runtime={args.seconds}", "--time_based=1", f"--size={args.size}",
        "--randrepeat=1", "--group_reporting=1", "--allow_file_create=0",
        "--output-format=json", f"--output={args.output.resolve()}",
    ]
    if not writing:
        command.append("--readonly")
    else:
        command.append("--end_fsync=1")
    if args.dry_run:
        print(shlex.join(command))
        return 0
    if not shutil.which("fio"):
        parser.error("install the fio benchmark tool first")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    completed = subprocess.run(command, check=False)
    metadata_path.write_text(json.dumps({
        "schema_version": 1,
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "started_unix": start,
        "elapsed_seconds": time.time() - start,
        "command": command,
        "exit_code": completed.returncode,
    }, indent=2) + "\n")
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
