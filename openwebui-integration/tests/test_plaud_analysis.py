import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import plaud


class PlaudAnalysisTests(unittest.TestCase):
    def test_whisper_arguments_lock_selected_language(self):
        params = plaud.WHISPER_PARAMS.copy()
        params["language"] = "en"

        arguments = plaud.build_whisper_arguments(
            Path("source.wav"),
            Path("result"),
            params,
        )

        language_index = arguments.index("-l")
        self.assertEqual(arguments[language_index + 1], "en")

    def test_markdown_annex_keeps_source_language(self):
        document = plaud.normalize_document(
            {
                "document_type": "note",
                "summary": "Synthèse en français.",
                "key_points": ["Point français"],
                "sections": [],
            },
            "Titre",
        )
        english_transcript = (
            "By the end of this video, you're going to know exactly "
            "how to build your own AI operating system."
        )
        markdown = plaud.render_markdown(
            "Titre",
            Path("audio.wav"),
            Path("transcript.txt"),
            english_transcript,
            document,
            60,
        )
        self.assertIn("## Synthèse", markdown)
        self.assertIn("Synthèse en français.", markdown)
        self.assertIn(english_transcript, markdown)
        self.assertIn("## Annexe - Transcription complète", markdown)

    def test_local_consolidation_deduplicates_without_llm(self):
        first = plaud.normalize_extraction(
            {
                "topics": [{"name": "Projet", "details": ["Budget validé"]}],
                "facts": ["La réunion est mardi"],
                "actions": [{"action": "Envoyer le devis", "owner": "Léa"}],
            }
        )
        second = plaud.normalize_extraction(
            {
                "topics": [
                    {
                        "name": "projet",
                        "details": ["Budget validé", "Livraison en octobre"],
                    }
                ],
                "facts": ["La réunion est mardi"],
                "actions": [{"action": "Envoyer le devis", "owner": "Léa"}],
            }
        )

        merged = plaud.reduce_extractions([first, second])

        self.assertEqual(merged["facts"], ["La réunion est mardi"])
        self.assertEqual(len(merged["actions"]), 1)
        self.assertEqual(len(merged["topics"]), 1)
        self.assertEqual(
            merged["topics"][0]["details"],
            ["Budget validé", "Livraison en octobre"],
        )

    def test_context_grows_for_long_prompt(self):
        self.assertEqual(plaud.ollama_context_for_prompt("court"), 8192)
        self.assertEqual(
            plaud.ollama_context_for_prompt("x" * 30_000),
            16384,
        )

    def test_long_document_uses_compact_editorial_json(self):
        extraction = plaud.normalize_extraction(
            {
                "topics": [
                    {
                        "name": "Sujet principal",
                        "details": [
                            f"Détail {index} " + "x" * 200
                            for index in range(120)
                        ],
                    }
                ],
                "facts": [f"Fait {index} " + "y" * 200 for index in range(120)],
                "decisions": [{"text": "Décision conservée", "context": ""}],
                "actions": [{"action": "Action conservée", "owner": "Léa"}],
            }
        )

        with patch.object(
            plaud,
            "ask_ollama_json",
            return_value={
                "document_type": "cours",
                "summary": "Synthèse compacte.",
                "key_points": ["Point essentiel"],
            },
        ) as request:
            document = plaud.create_structured_document("Titre", extraction)

        self.assertEqual(request.call_count, 1)
        self.assertEqual(document["summary"], "Synthèse compacte.")
        self.assertEqual(document["decisions"][0]["text"], "Décision conservée")
        self.assertEqual(document["actions"][0]["action"], "Action conservée")
        self.assertTrue(document["sections"])

    def test_invalid_structuring_falls_back_without_failure(self):
        extraction = plaud.normalize_extraction(
            {
                "topics": [{"name": "Sujet", "details": ["Détail fiable"]}],
                "facts": ["Fait fiable"],
            }
        )

        with patch.object(
            plaud,
            "ask_ollama_json",
            side_effect=ValueError("JSON tronqué"),
        ):
            document = plaud.create_structured_document("Titre", extraction)

        self.assertIn("Sujet", document["summary"])
        self.assertTrue(document["sections"])

    def test_pdf_splits_many_decisions_across_pages(self):
        document = plaud.normalize_document(
            {
                "document_type": "compte_rendu",
                "summary": "Résumé de validation.",
                "decisions": [
                    {
                        "text": (
                            f"Décision {index} : "
                            + "Cette décision contient un contexte détaillé. " * 8
                        ),
                        "context": "Contexte confirmé.",
                    }
                    for index in range(30)
                ],
                "open_questions": [
                    f"Question {index} : " + "Précision nécessaire. " * 6
                    for index in range(20)
                ],
                "sections": [],
            },
            "Document long",
        )

        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory)
            with patch.object(plaud, "REPORTS", reports):
                output = plaud.generate_pdf(
                    "Document long",
                    "long-decisions",
                    Path("audio.wav"),
                    Path("transcript.txt"),
                    "Transcription courte.",
                    document,
                    60,
                )

            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
