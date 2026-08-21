"""Reproduce the full-scrape persist failure: push rejected because main moved."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PERSIST_SCRIPT = REPO_ROOT / ".github" / "scripts" / "persist-state.sh"


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None, check: bool = True):
    merged = os.environ.copy()
    merged.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged,
        check=check,
        capture_output=True,
        text=True,
    )


def _git(cwd: Path, *args: str, check: bool = True):
    return _run(["git", *args], cwd=cwd, check=check)


class PersistStateRebaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.runner = self.root / "runner"
        self.other = self.root / "other"

        _run(["git", "init", "--bare", "-b", "main", str(self.remote)], cwd=self.root)
        _run(["git", "clone", str(self.remote), str(self.runner)], cwd=self.root)
        _git(self.runner, "config", "user.name", "runner")
        _git(self.runner, "config", "user.email", "runner@example.com")

        (self.runner / "data").mkdir()
        (self.runner / "docs").mkdir()
        (self.runner / "data" / "hzz_jobs.sqlite3").write_text("sqlite-v1\n")
        (self.runner / "docs" / "jobs.json").write_text("{}\n")
        _git(self.runner, "add", "data/hzz_jobs.sqlite3", "docs/jobs.json")
        _git(self.runner, "commit", "-m", "init")
        _git(self.runner, "push", "-u", "origin", "HEAD:main")

        _run(["git", "clone", str(self.remote), str(self.other)], cwd=self.root)
        _git(self.other, "config", "user.name", "other")
        _git(self.other, "config", "user.email", "other@example.com")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _move_main_on_remote(self) -> None:
        (self.other / "README.md").write_text("pages + readme landed while scrape ran\n")
        _git(self.other, "add", "README.md")
        _git(self.other, "commit", "-m", "docs: board and readme")
        _git(self.other, "push", "origin", "HEAD:main")

    def test_naive_push_rejected_when_main_moved(self):
        """Same failure as Actions run 32489361516: commit then push, no fetch."""
        self._move_main_on_remote()
        (self.runner / "data" / "hzz_jobs.sqlite3").write_text("sqlite-list-1174\n")
        _git(self.runner, "add", "data/hzz_jobs.sqlite3")
        _git(self.runner, "commit", "-m", "chore: full-scrape list checkpoint [skip ci]")
        result = _git(self.runner, "push", check=False)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stderr, r"rejected|non-fast-forward|fetch first")

    def test_persist_script_rebases_and_pushes(self):
        self._move_main_on_remote()
        (self.runner / "data" / "hzz_jobs.sqlite3").write_text("sqlite-list-1174\n")
        env = {
            "GITHUB_REF_NAME": "main",
            "GIT_PERSIST_REMOTE": "origin",
            "PERSIST_PUSH_ATTEMPTS": "3",
            "PERSIST_RETRY_SLEEP": "0",
        }
        result = _run(
            ["bash", str(PERSIST_SCRIPT), "chore: full-scrape list checkpoint [skip ci]"],
            cwd=self.runner,
            env=env,
        )
        self.assertIn("persist: pushed", result.stdout, result.stdout + result.stderr)

        probe = self.root / "probe"
        _run(["git", "clone", str(self.remote), str(probe)], cwd=self.root)
        sqlite = (probe / "data" / "hzz_jobs.sqlite3").read_text()
        readme = (probe / "README.md").read_text()
        self.assertEqual(sqlite, "sqlite-list-1174\n")
        self.assertIn("pages + readme", readme)

    def test_persist_keeps_this_run_sqlite_on_binary_conflict(self):
        self._move_main_on_remote()
        (self.other / "data" / "hzz_jobs.sqlite3").write_text("sqlite-from-other-workflow\n")
        _git(self.other, "add", "data/hzz_jobs.sqlite3")
        _git(self.other, "commit", "-m", "chore: other persist [skip ci]")
        _git(self.other, "push", "origin", "HEAD:main")

        (self.runner / "data" / "hzz_jobs.sqlite3").write_text("sqlite-this-run\n")
        env = {
            "GITHUB_REF_NAME": "main",
            "GIT_PERSIST_REMOTE": "origin",
            "PERSIST_PUSH_ATTEMPTS": "3",
            "PERSIST_RETRY_SLEEP": "0",
        }
        result = _run(
            ["bash", str(PERSIST_SCRIPT), "chore: full-scrape details batch [skip ci]"],
            cwd=self.runner,
            env=env,
        )
        self.assertIn("persist: pushed", result.stdout, result.stdout + result.stderr)

        probe = self.root / "probe-conflict"
        _run(["git", "clone", str(self.remote), str(probe)], cwd=self.root)
        self.assertEqual(
            (probe / "data" / "hzz_jobs.sqlite3").read_text(),
            "sqlite-this-run\n",
        )


if __name__ == "__main__":
    unittest.main()
