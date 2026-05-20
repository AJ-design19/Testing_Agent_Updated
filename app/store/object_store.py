"""
Object store (§12.1).

Stores screenshots, DOM snapshots, and Playwright traces.
URIs are stored in ESMEvent.artifact_uris.

v1 implementation: local filesystem under  artifacts/
with a URI scheme  local://artifacts/<run_id>/<filename>

Designed so that the URI scheme can be swapped to  s3://  or  gcs://
without changing any other code — just swap the ObjectStore implementation.
"""

import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", "artifacts")


class ObjectStore:
    """
    Stores and retrieves binary artifacts for a test run.
    Returns a URI string for each stored artifact.
    """

    def __init__(self):
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    def _run_dir(self, run_id: str) -> str:
        d = os.path.join(ARTIFACTS_DIR, run_id)
        os.makedirs(d, exist_ok=True)
        return d

    def store_screenshot(self, run_id: str, source_path: str,
                         label: str = "") -> Optional[str]:
        """Copy a screenshot into the artifact store and return its URI."""
        if not os.path.exists(source_path):
            logger.warning("[ObjectStore] Screenshot not found: %s", source_path)
            return None
        run_dir = self._run_dir(run_id)
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        filename = f"{ts}_{label}_{os.path.basename(source_path)}" if label else os.path.basename(source_path)
        dest = os.path.join(run_dir, filename)
        try:
            shutil.copy2(source_path, dest)
            uri = f"local://artifacts/{run_id}/{filename}"
            logger.debug("[ObjectStore] Stored screenshot: %s", uri)
            return uri
        except Exception as e:
            logger.error("[ObjectStore] store_screenshot failed: %s", e)
            return None

    def store_dom_snapshot(self, run_id: str, html_content: str,
                           label: str = "dom") -> Optional[str]:
        """Write a DOM HTML snapshot to the artifact store and return its URI."""
        run_dir = self._run_dir(run_id)
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        filename = f"{ts}_{label}.html"
        dest = os.path.join(run_dir, filename)
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(html_content)
            uri = f"local://artifacts/{run_id}/{filename}"
            logger.debug("[ObjectStore] Stored DOM snapshot: %s", uri)
            return uri
        except Exception as e:
            logger.error("[ObjectStore] store_dom_snapshot failed: %s", e)
            return None

    def store_trace(self, run_id: str, trace_path: str) -> Optional[str]:
        """Store a Playwright trace zip and return its URI."""
        return self.store_screenshot(run_id, trace_path, label="trace")

    def list_artifacts(self, run_id: str) -> list[str]:
        """Return all URIs stored for a given run."""
        run_dir = os.path.join(ARTIFACTS_DIR, run_id)
        if not os.path.exists(run_dir):
            return []
        return [
            f"local://artifacts/{run_id}/{f}"
            for f in sorted(os.listdir(run_dir))
        ]

    def resolve_uri(self, uri: str) -> Optional[str]:
        """Convert a local:// URI back to an absolute filesystem path."""
        if uri.startswith("local://"):
            return os.path.abspath(uri[len("local://"):])
        return None
