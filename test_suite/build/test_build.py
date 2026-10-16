"""Regression tests for external configuration, metadata and package staging."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
KCONFIG = (REPO / 'kernel/Kconfig').read_text()


class BuildConfiguration(unittest.TestCase):
    def generate(self, overrides=None, text=KCONFIG, config=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('MARS_')}
        env.update(overrides or {})
        command = ['perl', str(REPO / 'scripts/gen_config.pl')]
        if config:
            command += ['--kernel-config', str(config)]
        return subprocess.run(command, input=text, text=True, env=env, capture_output=True)

    def test_defaults_respect_transitive_dependencies(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('#define CONFIG_MARS_CHECKS 1', result.stdout)
        self.assertNotIn('#define CONFIG_MARS_DEBUG_MEM_STRONG', result.stdout)
        self.assertNotIn('#define CONFIG_MARS_EXPECTED_RESOURCES', result.stdout)

    def test_explicit_impossible_option_fails(self):
        result = self.generate({'MARS_DEBUG_MEM': 'y', 'MARS_DEBUG': 'n'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires MARS_DEBUG', result.stderr)

    def test_dependencies_and_overrides(self):
        result = self.generate({'MARS_DEBUG': 'y', 'MARS_DEBUG_MEM': 'y'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('#define CONFIG_MARS_DEBUG_MEM_STRONG 1', result.stdout)

    def test_invalid_values_fail(self):
        for option, value in [('MARS_DEBUG', 'maybe'), ('MARS_DEFAULT_PORT', '65535'),
                              ('MARS_SCAN_INTERVAL', '-1'), ('MARS_LOGDIR', 'unquoted'),
                              ('MARS_LOGDIR', '"foo"\n#error bad')]:
            with self.subTest(option=option, value=value):
                self.assertNotEqual(self.generate({option: value}).returncode, 0)

    def test_boolean_expression_and_cycle(self):
        text = 'config A\n bool "a"\n default y\n depends on (B || C) && !D\n'
        text += 'config B\n bool "b"\n default n\nconfig C\n bool "c"\n default y\nconfig D\n bool "d"\n default n\n'
        result = self.generate(text=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('#define CONFIG_A 1', result.stdout)
        self.assertNotEqual(self.generate(text=text + ' depends on A\n').returncode, 0)

    def test_unknown_directive_fails(self):
        self.assertNotEqual(self.generate(text='config A\n bool "a"\n default y if B\n').returncode, 0)

    def test_missing_kernel_dependency_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / '.config'
            config.write_text('CONFIG_64BIT=y\nCONFIG_MODULES=y\n')
            result = self.generate(config=config)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('CONFIG_BLOCK', result.stderr)

    def test_empty_epoch_is_normalized_before_kernel_compiler_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel = Path(directory)
            (kernel / '.config').write_text('')
            (kernel / 'Module.symvers').write_text('')
            (kernel / 'Makefile').write_text(
                "modules:\n\t@printf '%s\\n' \"$${SOURCE_DATE_EPOCH-unset}\"\n")
            for epoch, expected in [('', 'unset'), ('1', '1')]:
                result = subprocess.run(['make', '-s', '--no-print-directory', '-C',
                                         str(REPO / 'kernel'), 'KDIR=' + str(kernel)],
                                        env=dict(os.environ, SOURCE_DATE_EPOCH=epoch),
                                        text=True, capture_output=True, check=True)
                self.assertEqual(result.stdout.strip(), expected)

    def test_metadata_is_reproducible_and_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, MARSVERSION='review"version', SOURCE_DATE_EPOCH='1')
            cmd = ['perl', str(REPO / 'scripts/gen_buildtag.pl'), directory, 'test-kernel']
            first = subprocess.check_output(cmd, env=dict(env, USER='alice', HOSTNAME='first'))
            second = subprocess.check_output(cmd, env=dict(env, USER='bob', HOSTNAME='second'))
            self.assertEqual(first, second)
            self.assertIn(b'review\\"version-for-test-kernel', first)
            self.assertIn(b'1970-01-01 00:00:01 UTC', first)
            self.assertNotIn(directory.encode(), first)

    def test_staged_package_uses_canonical_build(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'source'
            subprocess.run(['python3', str(REPO / 'scripts/stage-source.py'), str(dest),
                            '--version', '1.2-test'], check=True)
            for name in ('Kbuild', 'Makefile'):
                self.assertEqual((dest / 'kernel' / name).read_bytes(),
                                 (REPO / 'kernel' / name).read_bytes())
            self.assertEqual((dest / 'DISTVERSION').read_text(), '1.2-test\n')
            self.assertIn('PACKAGE_VERSION="1.2-test"', (dest / 'dkms.conf').read_text())
            self.assertFalse(list(dest.rglob('*.o')))
            self.assertFalse((dest / 'kernel/buildtag.h').exists())


if __name__ == '__main__':
    unittest.main()
