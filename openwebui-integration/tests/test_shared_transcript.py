#!/usr/bin/env python3
"""Garantit que plaud_transcript.py ne diverge pas de la source racine."""

import unittest
from pathlib import Path


class SharedTranscriptTests(unittest.TestCase):
    def test_openwebui_transcript_matches_repo_root(self):
        integration_root = Path(__file__).resolve().parents[1]
        repo_root = integration_root.parent
        canonical = repo_root / "plaud_transcript.py"
        local_copy = integration_root / "plaud_transcript.py"
        self.assertTrue(
            canonical.is_file(),
            f"Source canonique manquante : {canonical}",
        )
        self.assertTrue(
            local_copy.is_file(),
            f"Copie openwebui-integration manquante : {local_copy}",
        )
        self.assertEqual(
            canonical.read_bytes(),
            local_copy.read_bytes(),
            "plaud_transcript.py diverge : relancer "
            "openwebui-integration/prepare_distribution.py",
        )


if __name__ == "__main__":
    unittest.main()
