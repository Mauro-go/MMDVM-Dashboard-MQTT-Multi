"""Test di regressione per l'attribuzione del reflector D-Star."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


class DStarReflectorTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "events.sqlite3"
        with sqlite3.connect(self.db) as con:
            con.execute("""
                CREATE TABLE gateway_links (
                    gateway TEXT PRIMARY KEY,
                    action TEXT,
                    target TEXT,
                    reason TEXT,
                    event_timestamp TEXT,
                    received_at TEXT,
                    payload TEXT
                )
            """)

    def tearDown(self):
        self.temp.cleanup()

    def set_link(self, action, target="XRF934 D"):
        with sqlite3.connect(self.db) as con:
            con.execute("""
                INSERT OR REPLACE INTO gateway_links
                (gateway, action, target)
                VALUES ('dstargateway', ?, ?)
            """, (action, target))

    def resolve(self, source, reflector=""):
        from dstar_reflector import resolve_dstar_reflector
        return resolve_dstar_reflector(
            source=source,
            reflector=reflector,
            db=self.db,
        )

    def test_network_without_reflector(self):
        self.set_link("linking")
        self.assertEqual(self.resolve("network"), "XRF934 D")

    def test_explicit_reflector_preserved(self):
        self.set_link("linking")
        self.assertEqual(
            self.resolve("network", "REF001 C"),
            "REF001 C",
        )

    def test_rf_without_reflector(self):
        self.set_link("linking")
        self.assertEqual(self.resolve("rf"), "")

    def test_disconnected_gateway(self):
        for action in ("failed", "unlinked", "disconnected", "relinking"):
            with self.subTest(action=action):
                self.set_link(action)
                self.assertEqual(self.resolve("network"), "")

    def test_unknown_reflector(self):
        self.assertEqual(self.resolve("network"), "")

    def test_last_known_link_survives_reopen(self):
        self.set_link("linking")
        self.assertEqual(self.resolve("network"), "XRF934 D")
        self.assertEqual(self.resolve("network"), "XRF934 D")


    def test_historical_reflector(self):
        """Usa il reflector collegato all'inizio della trasmissione."""
        from dstar_reflector import resolve_dstar_reflector_at

        self.assertEqual(
            resolve_dstar_reflector_at(
                source="network",
                timestamp="2026-11-11T12:00:00Z",
                db=self.db,
            ),
            "",
        )

    def test_historical_explicit_reflector(self):
        """Il reflector esplicito ha sempre precedenza."""
        from dstar_reflector import resolve_dstar_reflector_at

        self.assertEqual(
            resolve_dstar_reflector_at(
                source="network",
                reflector="REF001 C",
                timestamp="2026-11-11T12:00:00Z",
                db=self.db,
            ),
            "REF001 C",
        )

    def test_historical_rf(self):
        """Non attribuisce automaticamente reflector alle chiamate RF."""
        from dstar_reflector import resolve_dstar_reflector_at

        self.assertEqual(
            resolve_dstar_reflector_at(
                source="rf",
                timestamp="2026-11-11T12:00:00Z",
                db=self.db,
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
