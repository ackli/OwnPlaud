import tempfile
import unittest
from pathlib import Path

from plaud_transcript import (
    clean_transcript_segments,
    detect_loops,
    load_whisper_json,
    transcript_text,
    write_json,
    write_srt,
)


class PlaudTranscriptTests(unittest.TestCase):
    def test_cleaning_preserves_two_repetitions(self):
        segments = [
            {"start_ms": 0, "end_ms": 1_000, "text": "Oui, oui, puis on continue."}
        ]

        cleaned, events = clean_transcript_segments(segments)

        self.assertEqual(transcript_text(cleaned), segments[0]["text"])
        self.assertEqual(events, [])

    def test_cleaning_collapses_three_repetitions(self):
        segments = [
            {
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "Le CRM, le CRM, le CRM, puis on continue.",
            }
        ]

        cleaned, events = clean_transcript_segments(segments)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "repetition_dans_segment")
        self.assertEqual(transcript_text(cleaned), "Le CRM, puis on continue.")
        self.assertFalse(detect_loops(cleaned))

    def test_cleaning_collapses_long_single_word_loop(self):
        segments = [
            {
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "Avant, " + ", ".join(["jamais"] * 96) + ", après.",
            }
        ]

        cleaned, _events = clean_transcript_segments(segments)

        self.assertEqual(transcript_text(cleaned), "Avant, jamais, après.")
        self.assertFalse(detect_loops(cleaned))

    def test_cleaning_removes_only_exact_known_hallucination(self):
        segments = [
            {
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "Sous-titrage Société Radio-Canada",
            },
            {
                "start_ms": 1_000,
                "end_ms": 2_000,
                "text": "La Société Radio-Canada produit cette émission.",
            },
        ]

        cleaned, events = clean_transcript_segments(segments)

        self.assertEqual(len(cleaned), 1)
        self.assertIn("produit cette émission", cleaned[0]["text"])
        self.assertTrue(any(event["type"] == "hallucination_connue" for event in events))

    def test_json_and_srt_round_trip_offsets(self):
        segments = [
            {"start_ms": 3_661_250, "end_ms": 3_663_500, "text": "Bonjour."}
        ]
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "result.json"
            srt_path = Path(directory) / "result.srt"

            write_json(json_path, segments)
            write_srt(srt_path, segments)
            loaded = load_whisper_json(json_path)

            self.assertEqual(loaded, segments)
            self.assertIn("01:01:01,250 --> 01:01:03,500", srt_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
