"""Tests for install.py — the parts that can run without FL Studio.

The installer runs on the user's machine before anything else works, so a
bug here means the whole project looks broken. These tests cover the two
platform-specific decisions:

  * which Documents folders we search for FL Studio's settings
    (Windows machines often have Documents redirected into OneDrive)
  * the server entry we write into Claude Desktop's config, which only
    supports command/args/env — never a "cwd" key
"""

import shutil
import unittest
from pathlib import Path
from unittest import mock

import install


class TestDocumentsDirs(unittest.TestCase):
    def test_macos_uses_home_documents(self):
        dirs = install.documents_dirs("Darwin")
        self.assertEqual(dirs, [Path.home() / "Documents"])

    def test_windows_includes_onedrive_fallback(self):
        # On non-Windows hosts the winreg import fails and is skipped, but
        # the plain and OneDrive Documents guesses must both be present.
        dirs = install.documents_dirs("Windows")
        self.assertIn(Path.home() / "Documents", dirs)
        self.assertIn(Path.home() / "OneDrive" / "Documents", dirs)

    def test_no_duplicate_candidates(self):
        dirs = install.documents_dirs("Windows")
        self.assertEqual(len(dirs), len(set(dirs)))


class TestServerEntry(unittest.TestCase):
    def test_uv_entry_when_uv_available(self):
        with mock.patch.object(shutil, "which", return_value="/usr/bin/uv"):
            entry = install.server_entry()
        self.assertEqual(entry["command"], "uv")
        self.assertIn(str(install.REPO_ROOT), entry["args"])

    def test_python_fallback_has_no_cwd_key(self):
        # Claude Desktop ignores unknown keys like "cwd", so the fallback
        # must locate the package via PYTHONPATH instead.
        with mock.patch.object(shutil, "which", return_value=None):
            entry = install.server_entry()
        self.assertNotIn("cwd", entry)
        self.assertEqual(entry["args"], ["-m", "src.main"])
        self.assertEqual(entry["env"]["PYTHONPATH"], str(install.REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
