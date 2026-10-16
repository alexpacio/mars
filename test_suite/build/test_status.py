"""Exercise the public JSON status schema against disposable metadata."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class MachineStatus(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.actual = self.root / 'resource-test' / 'actual-node'
        self.actual.mkdir(parents=True)

    def status(self, *arguments, check=True):
        return subprocess.run(['perl', str(REPO / 'userspace/mars-status'), '--json',
                               '--root', str(self.root), '--host', 'node', *arguments],
                              text=True, capture_output=True, check=check)

    def test_numeric_schema_and_unknown_links(self):
        for name, value in [('is-primary', '1'), ('open-count', '2'),
                            ('log-fatal-error', '-5'), ('physical-block-size', '4096'),
                            ('discard-supported', '1'), ('private-secret', 'sensitive')]:
            (self.actual / name).symlink_to(value)
        data = json.loads(self.status().stdout)
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(data['peer'], 'node')
        status = data['resources'][0]
        self.assertEqual(status['is_primary'], 1)
        self.assertEqual(status['log_fatal_error'], -5)
        self.assertEqual(status['physical_block_size'], 4096)
        self.assertIsNone(status['if_state'])
        self.assertNotIn('sensitive', str(data))

    def test_invalid_values_and_regular_files_are_null(self):
        (self.actual / 'is-primary').symlink_to('yes')
        (self.actual / 'if-flying').symlink_to('99999999999999999999999')
        (self.actual / 'open-count').write_text('1')
        (self.actual / 'discard-supported').symlink_to('2')
        data = json.loads(self.status('test').stdout)['resources'][0]
        for key in ('is_primary', 'if_flying', 'open_count', 'discard_supported'):
            self.assertIsNone(data[key])

    def test_missing_resource_and_path_traversal_fail(self):
        for resource in ('absent', '../test', '/etc', '..'):
            self.assertNotEqual(self.status(resource, check=False).returncode, 0)

    def test_status_directory_symlink_is_not_followed(self):
        other = self.root / 'other'
        other.mkdir()
        (other / 'is-primary').symlink_to('1')
        self.actual.rmdir()
        self.actual.symlink_to(other)
        data = json.loads(self.status('test').stdout)['resources'][0]
        self.assertIsNone(data['is_primary'])


if __name__ == '__main__':
    unittest.main()
