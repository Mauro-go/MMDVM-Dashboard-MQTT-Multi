"""Regression tests for preserving local frontend assets."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import update_live


class PreserveLocalAssets(unittest.TestCase):
    def test_keeps_custom_images_and_icons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / 'active' / 'frontend'
            staged = root / 'stage' / 'frontend'
            (active / 'images' / 'IR3ZZO').mkdir(parents=True)
            staged.mkdir(parents=True)
            (active / 'images' / 'IR3ZZO' / 'photo.png').write_bytes(b'personal')
            (active / 'favicon.ico').write_bytes(b'icon')
            (active / 'old.html.backup').write_bytes(b'old')
            update_live.preserve_local_frontend(active.parent, staged.parent)
            self.assertEqual((staged / 'images' / 'IR3ZZO' / 'photo.png').read_bytes(), b'personal')
            self.assertEqual((staged / 'favicon.ico').read_bytes(), b'icon')
            self.assertFalse((staged / 'old.html.backup').exists())

    def test_release_asset_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / 'active' / 'frontend'
            staged = root / 'stage' / 'frontend'
            active.mkdir(parents=True)
            staged.mkdir(parents=True)
            (active / 'favicon.ico').write_bytes(b'local')
            (staged / 'favicon.ico').write_bytes(b'release')
            update_live.preserve_local_frontend(active.parent, staged.parent)
            self.assertEqual((staged / 'favicon.ico').read_bytes(), b'release')

if __name__ == '__main__':
    unittest.main()
