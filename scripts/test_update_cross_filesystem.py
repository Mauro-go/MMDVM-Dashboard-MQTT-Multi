import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from update_transaction_engine import swap_component, restore_snapshot, copy_snapshot, fingerprint

class CrossFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.live, self.staging, self.tx, self.backup = (base / s for s in ('live','staging','transaction','backup'))
        self.live.mkdir(); self.staging.mkdir(); self.tx.mkdir()
        for root, text in ((self.live,'old'),(self.staging,'new')):
            (root/'backend').mkdir(); (root/'backend'/'app.py').write_text(text)
            (root/'frontend').mkdir(); (root/'frontend'/'index.html').write_text(text)
            (root/'admin').mkdir(); (root/'admin'/'admin.html').write_text(text)
            (root/'VERSION').write_text(text)
        copy_snapshot(self.live, self.backup)
    def test_swap_never_directly_renames_from_staging(self):
        actual = os.replace
        def guarded(src, dst):
            if str(src).startswith(str(self.staging)):
                raise OSError(errno.EXDEV, 'cross-device link')
            return actual(src, dst)
        with patch('update_transaction_engine.os.replace', side_effect=guarded):
            swap_component(self.staging, self.live, self.tx, 'backend')
        self.assertEqual((self.live/'backend'/'app.py').read_text(),'new')
    def test_rollback_after_update(self):
        swap_component(self.staging, self.live, self.tx, 'backend')
        restore_snapshot(self.backup, self.live, self.tx)
        self.assertEqual(fingerprint(self.backup),fingerprint(self.live))
    def test_failed_rename_restores_current(self):
        actual = os.replace
        def failing(src,dst):
            if Path(src).name=='.mmdvm-incoming-backend':
                raise OSError(errno.EIO,'synthetic copy failure')
            return actual(src,dst)
        with patch('update_transaction_engine.os.replace',side_effect=failing):
            with self.assertRaises(OSError):
                swap_component(self.staging,self.live,self.tx,'backend')
        self.assertEqual((self.live/'backend'/'app.py').read_text(),'old')

if __name__ == '__main__': unittest.main()
