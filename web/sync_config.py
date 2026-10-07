"""Write docs/sync-config.js with the public Google OAuth client ID.

Deploy workflows run this right before uploading docs/ to Pages, reading the
repository variable GOOGLE_CLIENT_ID. The committed file keeps an empty ID, which
hides the "Save my list to Google" block.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SYNC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "docs" / "sync-config.js"


def render_sync_config(client_id: str) -> str:
    return f"window.HZZ_GOOGLE_CLIENT_ID = {json.dumps((client_id or '').strip())};\n"


def write_sync_config(client_id: str, path: Path | None = None) -> Path:
    dest = path or SYNC_CONFIG_PATH
    dest.write_text(render_sync_config(client_id), encoding="utf-8")
    return dest


if __name__ == "__main__":
    value = os.environ.get("GOOGLE_CLIENT_ID", "")
    out = write_sync_config(value)
    print(f"Wrote {out} ({'client ID set' if value.strip() else 'no client ID; Google save hidden'})")
