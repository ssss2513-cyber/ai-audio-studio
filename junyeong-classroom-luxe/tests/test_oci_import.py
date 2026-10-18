"""Offline migration test; uses only synthetic files and a temporary SQLite DB."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "oci" / "import_video.py"
ID = "0123456789abcdef0123456789abcdef"


class ImportVideoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "media").mkdir()
        (self.base / "import-videos").mkdir()
        self.media = self.base / "import-videos" / "sample.mp4"
        self.media.write_bytes(b"\x00\x00\x00\x14ftypisom" + b"\x00" * 512)
        self.db = self.base / "classroom.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.execute(
                "CREATE TABLE videos("
                "id TEXT PRIMARY KEY,source TEXT,size INTEGER,mime TEXT,"
                "state TEXT,revision INTEGER)"
            )
            c.execute(
                "INSERT INTO videos VALUES(?,?,?,?,?,?)",
                (ID, "drive", self.media.stat().st_size, "video/mp4", "public", 1),
            )

    def run_cmd(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env={**os.environ, "CLASSROOM_DATA_DIR": str(self.base)},
            capture_output=True, text=True,
        )

    def test_dry_run_is_read_only(self):
        result = self.run_cmd("attach", "--id", ID, "--file", str(self.media))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.base / "media" / ID).exists())
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT source FROM videos").fetchone()[0], "drive")

    def test_apply_switches_only_storage_source(self):
        result = self.run_cmd(
            "attach", "--id", ID, "--file", str(self.media), "--apply"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "media" / ID).read_bytes(), self.media.read_bytes())
        with sqlite3.connect(self.db) as c:
            row = c.execute("SELECT source,revision,state FROM videos").fetchone()
            self.assertEqual(row, ("local", 2, "public"))

    def test_wrong_size_aborts_without_update(self):
        self.media.write_bytes(self.media.read_bytes() + b"x")
        result = self.run_cmd(
            "attach", "--id", ID, "--file", str(self.media), "--apply"
        )
        self.assertNotEqual(result.returncode, 0)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT source FROM videos").fetchone()[0], "drive")


if __name__ == "__main__":
    unittest.main()
