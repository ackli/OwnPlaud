import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "diagnose_transcript.py"
SPEC = importlib.util.spec_from_file_location("diagnose_transcript", MODULE_PATH)
diagnose = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnose)


class DiagnoseTranscriptTests(unittest.TestCase):
    def test_detects_three_repeated_ngrams(self):
        segments = [
            {
                "start_ms": 10_000,
                "end_ms": 20_000,
                "text": "Le CRM, le CRM ! le crm. La suite est normale.",
            }
        ]

        findings = diagnose.detect_loops(segments)

        repetitions = [
            finding for finding in findings
            if finding["type"] == "repetition_dans_segment"
        ]
        self.assertEqual(len(repetitions), 1)
        self.assertEqual(repetitions[0]["text"].casefold(), "le crm")
        self.assertEqual(repetitions[0]["repetitions"], 3)
        self.assertEqual(repetitions[0]["start_ms"], 10_000)

    def test_keeps_two_repetitions(self):
        segments = [
            {
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "Très bien, très bien.",
            }
        ]

        findings = diagnose.detect_loops(segments)

        self.assertFalse(
            any(finding["type"] == "repetition_dans_segment" for finding in findings)
        )

    def test_detects_three_nearly_identical_segments(self):
        segments = [
            {"start_ms": 0, "end_ms": 1_000, "text": "Radio Canada."},
            {"start_ms": 1_000, "end_ms": 2_000, "text": "Radio Canada !"},
            {"start_ms": 2_000, "end_ms": 3_000, "text": "radio canada"},
        ]

        findings = diagnose.detect_loops(segments)

        repeated = [
            finding for finding in findings
            if finding["type"] == "repetition_entre_segments"
        ]
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0]["repetitions"], 3)
        self.assertEqual(repeated[0]["end_ms"], 3_000)

    def test_known_hallucination_must_fill_whole_segment(self):
        exact = [
            {
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "Sous-titrage Société Radio-Canada",
            }
        ]
        sentence = [
            {
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "On parle du sous-titrage Société Radio-Canada aujourd'hui.",
            }
        ]

        self.assertTrue(
            any(item["type"] == "hallucination_connue" for item in diagnose.detect_loops(exact))
        )
        self.assertFalse(
            any(item["type"] == "hallucination_connue" for item in diagnose.detect_loops(sentence))
        )


if __name__ == "__main__":
    unittest.main()
