import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("update_worker", Path(__file__).with_name("update_worker.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

class WorkerTests(unittest.TestCase):
    def test_missing_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(worker, "REQUEST", Path(tmp)/"request.json"):
                self.assertIsNone(worker.read_request())

    def test_valid_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"request.json"
            path.write_text(json.dumps({"version":"v1.2.3"}))
            with patch.object(worker, "REQUEST", path):
                self.assertEqual(worker.read_request(), "v1.2.3")

    def test_rejects_injected_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"request.json"
            path.write_text(json.dumps({"version":"v1.2.3;touch /tmp/hacked"}))
            with patch.object(worker, "REQUEST", path):
                with self.assertRaises(RuntimeError):
                    worker.read_request()

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"request.json"
            other = Path(tmp)/"target.json"
            other.write_text(json.dumps({"version":"v1.2.3"}))
            path.symlink_to(other)
            with patch.object(worker, "REQUEST", path):
                self.assertIsNone(worker.read_request())

if __name__ == "__main__":
    unittest.main()
