#!/usr/bin/env python3
"""Tests de conservation de la langue par fichier en file d'attente."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import plaud_gui


class PendingLanguageTests(unittest.TestCase):
    def test_stored_language_wins_over_menu(self):
        self.assertEqual(
            plaud_gui.resolve_pending_item_language({"language": "en"}, "fr"),
            "en",
        )
        self.assertEqual(
            plaud_gui.resolve_pending_item_language({"language": "fr"}, "en"),
            "fr",
        )

    def test_menu_used_only_when_missing(self):
        self.assertEqual(
            plaud_gui.resolve_pending_item_language({}, "en"),
            "en",
        )

    def test_batch_queue_en_then_fr_keeps_order(self):
        pending = [
            {"id": "1", "title": "English talk", "language": "en"},
            {"id": "2", "title": "Parler FR", "language": "fr"},
        ]
        self.assertEqual(
            plaud_gui.pending_batch_whisper_languages(pending, "auto"),
            ["en", "fr"],
        )

    def test_process_pending_item_passes_stored_language_to_whisper(self):
        app = MagicMock()
        app.process = None
        app.selected_language_code.return_value = "auto"
        app.current_pending_context = None
        calls = []

        def start_process(*args, **kwargs):
            calls.append(kwargs.get("whisper_language"))

        app.start_process.side_effect = start_process
        app.add_log = MagicMock()
        app.refresh_pending_sidebar = MagicMock()

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            recordings = workspace / "recordings"
            recordings.mkdir()
            (recordings / "clip.wav").write_bytes(b"RIFF")
            item = {
                "id": "abc",
                "title": "EN file",
                "audio_name": "clip.wav",
                "language": "en",
            }
            method = plaud_gui.PlaudApp.process_pending_item
            with patch.object(plaud_gui, "messagebox"):
                method(app, workspace, item)

        self.assertEqual(calls, ["en"])
        self.assertEqual(item["language"], "en")

    def test_batch_process_launches_whisper_en_then_fr(self):
        """File en + fr traitée en lot → Whisper reçoit en puis fr."""
        app = MagicMock()
        app.process = None
        app.selected_language_code.return_value = "auto"
        app.current_pending_context = None
        launched = []

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            recordings = workspace / "recordings"
            recordings.mkdir()
            (recordings / "en.wav").write_bytes(b"RIFF")
            (recordings / "fr.wav").write_bytes(b"RIFF")
            queue_items = [
                {
                    "id": "1",
                    "title": "EN",
                    "audio_name": "en.wav",
                    "language": "en",
                },
                {
                    "id": "2",
                    "title": "FR",
                    "audio_name": "fr.wav",
                    "language": "fr",
                },
            ]

            def start_process(*args, **kwargs):
                launched.append(kwargs.get("whisper_language"))

            app.start_process.side_effect = start_process
            app.add_log = MagicMock()
            app.refresh_pending_sidebar = MagicMock()

            with patch.object(plaud_gui, "messagebox"):
                for item in queue_items:
                    plaud_gui.PlaudApp.process_pending_item(
                        app, workspace, item
                    )

        self.assertEqual(launched, ["en", "fr"])


if __name__ == "__main__":
    unittest.main()
