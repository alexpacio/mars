#!/usr/bin/env python3
"""Stage a clean module source tree for DKMS, CI or an isolated build."""
import argparse
from pathlib import Path
import shutil


def stage(source, destination, version=None):
    if any(destination == source / d or source / d in destination.parents
           for d in ('kernel', 'scripts')):
        raise ValueError('destination must not be inside kernel/ or scripts/')
    destination.mkdir(parents=True, exist_ok=False)
    for directory in ('kernel', 'scripts'):
        shutil.copytree(source / directory, destination / directory,
                        ignore=shutil.ignore_patterns(
                            '*.o', '*.ko', '*.cmd', '*.mod', '*.mod.c', '*.pyc',
                            '__pycache__', '.tmp_versions', 'Module.symvers',
                            'modules.order', 'buildtag.h', 'mars_config.h', '*.tmp'))
    shutil.copy2(source / 'contrib/mars-dkms.dkms', destination / 'dkms.conf')
    if version:
        (destination / 'DISTVERSION').write_text(version + '\n')
        # Historical release tarballs also carry a kernel-local version.
        (destination / 'kernel/DISTVERSION').write_text(version + '\n')
        config = destination / 'dkms.conf'
        config.write_text(config.read_text().replace('#MODULE_VERSION#', version))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--version')
    args = parser.parse_args()
    if args.version and any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.+_~-' for c in args.version):
        parser.error('version must contain only letters, digits, ., +, _, ~ or -')
    stage(args.source.resolve(), args.destination.resolve(), args.version)
