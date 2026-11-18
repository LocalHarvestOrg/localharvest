"""The sensitive data check, run against throwaway git repositories."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "check_sensitive.py"

# Assembled here so this file does not contain anything the check refuses
GITHUB_TOKEN = "gh" + "p_" + "a1B2" * 9
PRIVATE_KEY = "-----BEGIN RSA " + "PRIVATE KEY-----"


class SensitiveCheckTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "--quiet", "--initial-branch=main")
        self.commit("README.md", "# Project\n")

    def git(self, *args):
        command = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                   "-c", "commit.gpgsign=false", *args]
        return subprocess.run(command, cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def stage(self, path, content):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content if isinstance(content, bytes) else content.encode())
        self.git("add", "--force", path)

    def commit(self, path, content):
        self.stage(path, content)
        self.git("commit", "--quiet", "-m", f"Add {path}")

    def check(self, *args):
        return subprocess.run([sys.executable, str(CHECKER), *args], cwd=self.repo,
                              capture_output=True, text=True)

    def assert_blocked(self, rule, path, content):
        self.stage(path, content)
        result = self.check("--staged")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(rule, result.stderr)

    def assert_passes(self, path, content):
        self.stage(path, content)
        result = self.check("--staged")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blocks_secrets_without_printing_them(self):
        self.stage("config.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
        result = self.check("--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("github-token", result.stderr)
        self.assertIn("config.py:1", result.stderr)
        self.assertNotIn(GITHUB_TOKEN, result.stdout + result.stderr)

    def test_blocks_private_key(self):
        self.assert_blocked("private-key", "notes.txt", PRIVATE_KEY + "\n")

    def test_blocks_documents_by_name_and_by_content(self):
        self.assert_blocked("file-type", "report.PDF", "x")
        self.assert_blocked("file-content", "renamed.txt", b"%PDF-1.7\n\x00binary")

    def test_blocks_data_folders_and_credential_files(self):
        self.assert_blocked("data-folder", "annotations/oihho-manual/page.json", "{}")
        self.assert_blocked("credential-file", "app/.env.local", "A=1\n")

    def test_blocks_notebook_outputs(self):
        notebook = {"cells": [{"cell_type": "code", "source": [], "outputs": [{"text": ["row"]}]}]}
        self.assert_blocked("notebook-output", "a.ipynb", json.dumps(notebook))

    def test_blocks_personal_details(self):
        self.assert_blocked("personal-path", "a.py", "/Users" + "/jane/leak/a.csv\n")
        self.assert_blocked("email", "b.md", "Ask source" + "@newsroom.org\n")
        self.assert_blocked("private-address", "c.md", "Server at 192.168" + ".1.20\n")

    def test_passes_ordinary_content(self):
        self.assert_passes("a.py", 'url = "http://127.0.0.1:8000"\n')
        self.assert_passes("b.md", "Open /Users/me/doc and write to someone@example.com\n")
        self.assert_passes(".env.example", "TOKEN=\n")
        self.assert_passes("c.vue", '<img src="logo@2x.png">\n')

    def test_range_finds_a_secret_removed_by_a_later_commit(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.commit("config.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
        self.commit("config.py", "TOKEN = None\n")
        head = self.git("rev-parse", "HEAD").strip()
        result = self.check("--range", f"{base}..{head}")
        self.assertEqual(result.returncode, 1)
        self.assertIn("github-token", result.stderr)

    def test_reads_git_not_the_working_tree(self):
        self.stage("config.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
        (self.repo / "config.py").write_text("TOKEN = None\n")
        self.assertEqual(self.check("--staged").returncode, 1)

    def test_escapes_file_names_in_reports(self):
        self.stage("a\n::error::x.pdf", "x")
        result = self.check("--staged")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(line.startswith("::") for line in result.stderr.splitlines()))

    def test_allowlist_is_specific_to_rule_and_path(self):
        (self.repo / ".github").mkdir()
        (self.repo / ".github/sensitive-allowlist").write_text("file-type icons/*  # app icons\n")
        self.assert_passes("icons/a.png", "x")
        self.assert_blocked("file-type", "docs/a.png", "x")


if __name__ == "__main__":
    unittest.main()
