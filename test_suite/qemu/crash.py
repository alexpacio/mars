#!/usr/bin/env python3
"""Kill a VM with delayed data writeback, then recover from its durable log."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
BAD = re.compile(r'BUG:|WARNING: CPU:|Oops:|Kernel panic|MARS_QEMU_FAIL')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='mars-crash-', dir=os.environ.get('TMPDIR', '/var/tmp'))) / 'guest'
    subprocess.run([sys.executable, str(HERE / 'run.py'), args.release,
                    '--prepare-only', '--workdir', str(work),
                    '--guest-script', str(HERE / 'crash.sh'),
                    '--extra-module', 'dm-delay', '--extra-binary', 'dmsetup'], check=True)
    cmd = json.loads((work / 'command.json').read_text())
    with (work / 'write.log').open('w') as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                text = (work / 'write.log').read_text(errors='replace')
                if BAD.search(text) or proc.poll() is not None:
                    raise RuntimeError(f'Write stage failed: {work}/write.log')
                if 'MARS_QEMU_CRASH_ARMED' in text:
                    break
                time.sleep(.05)
            else:
                raise RuntimeError(f'Write stage timed out: {work}/write.log')
        finally:
            proc.kill()  # Deliberately no guest sync, shutdown, or QEMU drain.
            proc.wait()
    cmd[cmd.index('-append') + 1] += ' mars_crash=recover'
    with (work / 'recover.log').open('w') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
    text = (work / 'recover.log').read_text(errors='replace')
    if result.returncode != 1 or BAD.search(text) or 'MARS_QEMU_CRASH_RECOVERY_PASS' not in text:
        raise RuntimeError(f'Recovery failed: {work}/recover.log')
    print(f'PASS: crash recovery, {args.release}; artifacts: {work}', flush=True)


if __name__ == '__main__':
    main()
