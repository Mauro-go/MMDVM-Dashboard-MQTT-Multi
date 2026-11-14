"""Canale di origine: nessun fallback a produzione."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import update_origin
import update_release

class OriginTests(unittest.TestCase):
    def test_allowed_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'origin'
            for repository in sorted(update_origin.ALLOWED_REPOSITORIES):
                path.write_text(repository + '\n')
                self.assertEqual(update_origin.get_repository(path), repository)

    def test_missing_or_invalid_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'origin'
            with self.assertRaises(RuntimeError):
                update_origin.get_repository(path)
            path.write_text('Mauro-go/other-repo')
            with self.assertRaises(RuntimeError):
                update_origin.get_repository(path)

    def test_archive_url_follows_origin(self):
        for repository in sorted(update_origin.ALLOWED_REPOSITORIES):
            with patch.object(update_release, 'get_repository', return_value=repository):
                self.assertEqual(update_release.archive_url('v1.2.3'), f'https://github.com/{repository}/archive/refs/tags/v1.2.3.tar.gz')

if __name__ == '__main__':
    unittest.main()
