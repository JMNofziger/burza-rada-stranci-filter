"""GitHub workflow YAML must not use inline `run:` values that contain a colon."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# `run: cmd "foo: bar"` is invalid YAML: the second colon is parsed as a mapping.
_INLINE_RUN = re.compile(r"^(\s*)run:\s+(\S.*)$")


class WorkflowYamlTests(unittest.TestCase):
    def test_inline_run_steps_have_no_embedded_colon(self):
        failures: list[str] = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for i, line in enumerate(path.read_text().splitlines(), 1):
                match = _INLINE_RUN.match(line)
                if match and ":" in match.group(2):
                    failures.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
        self.assertEqual(
            failures,
            [],
            "Put the command under `run: |` so YAML does not treat `:` as a mapping",
        )


if __name__ == "__main__":
    unittest.main()
