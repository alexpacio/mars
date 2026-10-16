"""Offline comparison must report differences and fail closed on read errors."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRUB = REPO / 'userspace/mars-scrub'


class OfflineComparison(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.left = Path(self.temp.name) / 'left'
        self.right = Path(self.temp.name) / 'right'
        self.left.write_bytes(b'a' * 1025)
        self.right.write_bytes(self.left.read_bytes())

    def run_scrub(self, *args):
        result = subprocess.run(['perl', str(SCRUB), *args, str(self.left), str(self.right)],
                                text=True, capture_output=True)
        return result.returncode, json.loads(result.stdout)

    def test_help_succeeds_without_comparing(self):
        result = subprocess.run(['perl', str(SCRUB), '--help'], text=True,
                                capture_output=True, check=True)
        self.assertIn('usage:', result.stdout)
        self.assertNotIn('schema_version', result.stdout)

    def test_equal_including_partial_final_chunk(self):
        status, data = self.run_scrub('--quiescent', '--chunk-bytes=512')
        self.assertEqual(status, 0, data)
        self.assertTrue(data['equal'])
        self.assertEqual(data['compared_bytes'], 1025)

    def test_mismatch_offsets_and_hashes(self):
        self.right.write_bytes(b'a' * 512 + b'b' * 513)
        status, data = self.run_scrub('--quiescent', '--chunk-bytes=512')
        self.assertEqual(status, 1)
        self.assertEqual(data['mismatch_count'], 2)
        self.assertEqual([r['offset_bytes'] for r in data['mismatches']], [512, 1024])
        self.assertEqual(data['mismatches'][1]['right_sha256'], hashlib.sha256(b'b').hexdigest())
        self.assertEqual(self.left.read_bytes(), b'a' * 1025)

    def test_mismatch_output_is_bounded(self):
        self.right.write_bytes(b'b' * 1025)
        status, data = self.run_scrub('--quiescent', '--chunk-bytes=512', '--max-records=1')
        self.assertEqual(status, 1)
        self.assertEqual(data['mismatch_count'], 3)
        self.assertEqual(len(data['mismatches']), 1)
        self.assertEqual(data['omitted_mismatches'], 2)

    def test_size_failure(self):
        self.right.write_bytes(b'short')
        status, data = self.run_scrub('--quiescent')
        self.assertEqual(status, 2)
        self.assertIn('sizes differ', data['error'])
        self.assertNotIn('equal', data)

    def test_acknowledgement_and_same_object_required(self):
        status, data = self.run_scrub()
        self.assertEqual(status, 2)
        self.assertIn('--quiescent', data['error'])
        self.right.unlink()
        self.right.symlink_to(self.left)
        status, data = self.run_scrub('--quiescent')
        self.assertEqual(status, 2)
        self.assertIn('same object', data['error'])

    def test_fifo_is_rejected_without_blocking(self):
        self.left.unlink()
        os.mkfifo(self.left)
        result = subprocess.run(['perl', str(SCRUB), '--quiescent', str(self.left),
                                 str(self.right)], text=True, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 2)
        self.assertIn('regular files or Linux block devices', json.loads(result.stdout)['error'])

    def test_short_read_is_an_error_not_a_matching_empty_hash(self):
        result = subprocess.run(['perl', '-e',
            'require $ARGV[0]; open(my $fh, "<", $ARGV[1]) or die $!; '
            'eval { mars_scrub_read_exact($fh, 2048); }; die "missing error" unless $@; print $@;',
            str(SCRUB), str(self.left)], text=True, capture_output=True, check=True)
        self.assertIn('short read', result.stdout)


if __name__ == '__main__':
    unittest.main()
