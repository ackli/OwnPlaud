#!/usr/bin/env python3
"""Tests unitaires de l'export Open WebUI (HTTP mocké)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import plaud_openwebui as owui


class OpenWebUIExportTests(unittest.TestCase):
    def test_yaml_frontmatter_contains_metadata(self):
        text = owui.yaml_frontmatter(
            "Titre",
            "2026-09-29T12:00:00+02:00",
            "source.mp4",
            "en",
        )
        self.assertTrue(text.startswith("---\n"))
        self.assertIn('title: "Titre"', text)
        self.assertIn('language: "en"', text)
        self.assertIn('source: "source.mp4"', text)

    def test_incomplete_env_returns_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "OPENWEBUI_URL=http://localhost:3000\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                config, warning = owui.load_openwebui_config(root)
            self.assertIsNone(config)
            self.assertIn("OPENWEBUI_API_KEY", warning)
            self.assertIn("OPENWEBUI_KNOWLEDGE_ID", warning)

    def test_dedupe_skips_identical_content(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            md = workspace / "note.md"
            md.write_text("# hello\n", encoding="utf-8")
            fingerprint = owui.content_fingerprint(md)
            owui.remember_sent(
                workspace,
                fingerprint,
                file_id="file-123",
                knowledge_id="kb-1",
                path=md,
            )
            self.assertEqual(
                owui.already_sent(workspace, fingerprint, "kb-1"),
                "file-123",
            )

    def test_reprocess_same_body_ignored_despite_new_frontmatter_date(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            body = "# Même contenu\n\nParagraphe stable.\n"
            first = workspace / "first.md"
            second = workspace / "second.md"
            first.write_text(
                owui.yaml_frontmatter(
                    "Titre",
                    "2026-01-01T00:00:00+00:00",
                    "audio.mp4",
                    "en",
                )
                + body,
                encoding="utf-8",
            )
            second.write_text(
                owui.yaml_frontmatter(
                    "Titre",
                    "2026-09-29T12:00:00+00:00",
                    "audio.mp4",
                    "en",
                )
                + body,
                encoding="utf-8",
            )
            fingerprint = owui.content_fingerprint(first)
            owui.remember_sent(
                workspace,
                fingerprint,
                file_id="file-abc",
                knowledge_id="kb-1",
                path=first,
            )
            self.assertNotEqual(
                first.read_text(encoding="utf-8"),
                second.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                owui.content_fingerprint(first),
                owui.content_fingerprint(second),
            )
            config = {
                "url": "http://localhost:3000",
                "api_key": "secret",
                "knowledge_id": "kb-1",
            }
            with patch.object(owui, "upload_file") as upload, patch.object(
                owui, "force_resend", return_value=False
            ):
                file_id = owui.push_markdown_file(config, workspace, second)
            self.assertEqual(file_id, "file-abc")
            upload.assert_not_called()

    def test_push_uploads_then_waits_then_adds(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            md = workspace / "doc.md"
            md.write_text("---\ntitle: x\n---\nbody\n", encoding="utf-8")
            config = {
                "url": "http://localhost:3000",
                "api_key": "secret-key-do-not-log",
                "knowledge_id": "kb-42",
            }
            calls = []

            def fake_upload(cfg, path):
                calls.append("upload")
                return "fid-1"

            def fake_wait(cfg, file_id):
                calls.append("wait")
                self.assertEqual(file_id, "fid-1")

            def fake_add(cfg, file_id):
                calls.append("add")
                return {"ok": True}

            with patch.object(owui, "upload_file", side_effect=fake_upload), patch.object(
                owui, "wait_until_processed", side_effect=fake_wait
            ), patch.object(owui, "add_file_to_knowledge", side_effect=fake_add), patch.object(
                owui, "force_resend", return_value=False
            ):
                file_id = owui.push_markdown_file(config, workspace, md)
            self.assertEqual(file_id, "fid-1")
            self.assertEqual(calls, ["upload", "wait", "add"])
            tracking = json.loads(
                (workspace / "queue" / "openwebui_sent.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                tracking["entries"][owui.content_fingerprint(md)]["file_id"],
                "fid-1",
            )

    def test_failed_status_stops_immediately(self):
        config = {
            "url": "http://localhost:3000",
            "api_key": "secret",
            "knowledge_id": "kb",
        }
        with patch.object(
            owui,
            "_request",
            return_value={"status": "failed", "error": "parse error"},
        ):
            with self.assertRaises(owui.OpenWebUIError) as ctx:
                owui.wait_until_processed(config, "fid")
        self.assertIn("échoué", str(ctx.exception).casefold())

    def test_export_warns_when_requested_but_misconfigured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            md = root / "a.md"
            md.write_text("x\n", encoding="utf-8")
            with patch.object(owui, "export_requested", return_value=True), patch.object(
                owui,
                "load_openwebui_config",
                return_value=(None, "Open WebUI : configuration incomplète."),
            ):
                ok, message = owui.export_files(root, root, [md])
            self.assertFalse(ok)
            self.assertIn("incomplète", message)


if __name__ == "__main__":
    unittest.main()
