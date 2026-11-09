"""Utilitaires légers pour fusionner et nettoyer les segments Whisper."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
import zlib
from pathlib import Path


WORD_RE = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*", re.UNICODE)
DEFAULT_HALLUCINATIONS = (
    "Sous-titrage Société Radio-Canada",
    "Sous-titres réalisés par la communauté d'Amara.org",
    "Sous-titrage ST' 501",
    "Merci d'avoir regardé cette vidéo",
    "Abonnez-vous",
)


def normalize_text(text):
    text = unicodedata.normalize("NFKD", str(text).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(WORD_RE.findall(text))


def format_timestamp(milliseconds, separator="."):
    milliseconds = max(0, int(round(milliseconds)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def parse_timestamp(value):
    match = re.fullmatch(r"\s*(\d+):(\d+):(\d+)[,.](\d+)\s*", str(value or ""))
    if not match:
        return 0
    hours, minutes, seconds, fraction = match.groups()
    millis = int(fraction[:3].ljust(3, "0"))
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + millis


def _segment_offsets(raw):
    offsets = raw.get("offsets") or {}
    timestamps = raw.get("timestamps") or {}
    start_ms = offsets.get("from")
    end_ms = offsets.get("to")
    if not isinstance(start_ms, (int, float)):
        start_ms = parse_timestamp(timestamps.get("from"))
    if not isinstance(end_ms, (int, float)):
        end_ms = parse_timestamp(timestamps.get("to"))
    return int(start_ms or 0), int(end_ms or start_ms or 0)


def load_whisper_json(path, offset_ms=0):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    raw_segments = data.get("transcription", data.get("segments", []))
    segments = []
    for raw in raw_segments if isinstance(raw_segments, list) else []:
        if not isinstance(raw, dict):
            continue
        start_ms, end_ms = _segment_offsets(raw)
        segments.append(
            {
                "start_ms": start_ms + int(offset_ms),
                "end_ms": end_ms + int(offset_ms),
                "text": str(raw.get("text", "")).strip(),
            }
        )
    return segments


def repeated_ngrams(text):
    matches = list(WORD_RE.finditer(text))
    normalized = [normalize_text(match.group()) for match in matches]
    findings = []
    index = 0
    while index < len(matches):
        best = None
        for size in range(1, min(8, (len(matches) - index) // 3) + 1):
            pattern = normalized[index:index + size]
            count = 1
            while (
                index + (count + 1) * size <= len(matches)
                and normalized[index + count * size:index + (count + 1) * size] == pattern
            ):
                count += 1
            if count < 3:
                continue
            coverage = count * size
            candidate = {
                "word_start": index,
                "word_end": index + coverage,
                "size": size,
                "text": " ".join(match.group() for match in matches[index:index + size]),
                "repetitions": count,
                "coverage": coverage,
            }
            if (
                best is None
                or coverage > best["coverage"]
                or (coverage == best["coverage"] and size < best["size"])
            ):
                best = candidate
        if best:
            findings.append(best)
            index = best["word_end"]
        else:
            index += 1
    return findings


def compression_ratio(text):
    encoded = str(text).encode("utf-8")
    if not encoded:
        return 0.0
    return len(encoded) / max(1, len(zlib.compress(encoded)))


def detect_loops(segments, hallucinations=None, compression_threshold=2.4):
    known = {
        normalize_text(text)
        for text in (hallucinations or DEFAULT_HALLUCINATIONS)
        if normalize_text(text)
    }
    findings = []
    for index, segment in enumerate(segments):
        text = segment.get("text", "")
        for repetition in repeated_ngrams(text):
            findings.append(
                {
                    "type": "repetition_dans_segment",
                    "segment": index,
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "text": repetition["text"],
                    "repetitions": repetition["repetitions"],
                }
            )
        normalized = normalize_text(text)
        if normalized and normalized in known:
            findings.append(
                {
                    "type": "hallucination_connue",
                    "segment": index,
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "text": text,
                    "repetitions": 1,
                }
            )
        ratio = compression_ratio(text)
        if len(text) >= 80 and ratio > compression_threshold:
            findings.append(
                {
                    "type": "compression_suspecte",
                    "segment": index,
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "text": text,
                    "repetitions": 1,
                    "compression_ratio": round(ratio, 3),
                }
            )

    index = 0
    while index < len(segments):
        reference = normalize_text(segments[index].get("text", ""))
        end = index + 1
        if reference:
            while end < len(segments):
                candidate = normalize_text(segments[end].get("text", ""))
                if not candidate:
                    break
                ratio = difflib.SequenceMatcher(None, reference, candidate).ratio()
                if ratio <= 0.9:
                    break
                end += 1
        if end - index >= 3:
            findings.append(
                {
                    "type": "repetition_entre_segments",
                    "segment_start": index,
                    "segment_end": end - 1,
                    "start_ms": segments[index]["start_ms"],
                    "end_ms": segments[end - 1]["end_ms"],
                    "text": segments[index].get("text", ""),
                    "repetitions": end - index,
                }
            )
            index = end
        else:
            index += 1
    return sorted(findings, key=lambda item: (item["start_ms"], item["end_ms"], item["type"]))


def _collapse_repetitions(text):
    matches = list(WORD_RE.finditer(text))
    repetitions = repeated_ngrams(text)
    changes = []
    for repetition in reversed(repetitions):
        first_end_index = repetition["word_start"] + repetition["size"] - 1
        repeated_end_index = repetition["word_end"] - 1
        remove_start = matches[first_end_index].end()
        remove_end = matches[repeated_end_index].end()
        original = text[remove_start:remove_end]
        text = text[:remove_start] + text[remove_end:]
        changes.append(
            {
                "text": repetition["text"],
                "repetitions": repetition["repetitions"],
                "removed": original,
            }
        )
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip(), list(reversed(changes))


def clean_transcript_segments(segments, hallucinations=None):
    known = {
        normalize_text(text)
        for text in (hallucinations or DEFAULT_HALLUCINATIONS)
        if normalize_text(text)
    }
    cleaned = [dict(segment) for segment in segments]
    events = []
    dropped = set()

    index = 0
    while index < len(cleaned):
        reference = normalize_text(cleaned[index].get("text", ""))
        end = index + 1
        if reference:
            while end < len(cleaned):
                candidate = normalize_text(cleaned[end].get("text", ""))
                if not candidate or difflib.SequenceMatcher(None, reference, candidate).ratio() <= 0.9:
                    break
                end += 1
        if end - index >= 3:
            dropped.update(range(index + 1, end))
            events.append(
                {
                    "type": "repetition_entre_segments",
                    "start_ms": cleaned[index]["start_ms"],
                    "end_ms": cleaned[end - 1]["end_ms"],
                    "original": [item.get("text", "") for item in cleaned[index:end]],
                    "action": "segments répétés réduits à une occurrence",
                }
            )
            index = end
        else:
            index += 1

    result = []
    for index, segment in enumerate(cleaned):
        if index in dropped:
            continue
        original = segment.get("text", "")
        normalized = normalize_text(original)
        if normalized and normalized in known:
            events.append(
                {
                    "type": "hallucination_connue",
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "original": original,
                    "action": "segment entier supprimé",
                }
            )
            continue

        collapsed, changes = _collapse_repetitions(original)
        if changes:
            events.append(
                {
                    "type": "repetition_dans_segment",
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "original": original,
                    "resultat": collapsed,
                    "details": changes,
                    "action": "répétitions ramenées à une occurrence",
                }
            )
            segment["text"] = collapsed
        ratio = compression_ratio(original)
        if len(original) >= 80 and ratio > 2.4:
            events.append(
                {
                    "type": "compression_suspecte",
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "original": original,
                    "compression_ratio": round(ratio, 3),
                    "action": "signalé sans suppression automatique",
                }
            )
        if segment.get("text", "").strip():
            result.append(segment)
    return result, sorted(events, key=lambda item: (item["start_ms"], item["end_ms"], item["type"]))


def loop_score(findings):
    score = 0
    for finding in findings:
        score += 10
        score += max(0, int(finding.get("repetitions", 1)) - 1)
        if finding["type"] == "compression_suspecte":
            score += 5
    return score


def uncertain_ranges(findings):
    ranges = []
    for finding in sorted(findings, key=lambda item: item["start_ms"]):
        start = finding["start_ms"]
        end = finding["end_ms"]
        if ranges and start <= ranges[-1][1] + 1_000:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])
    return ranges


def mark_uncertain_segments(segments, findings):
    marked = [dict(segment) for segment in segments]
    for start, end in uncertain_ranges(findings):
        marker = (
            f"[passage incertain {format_timestamp(start)[:8]}"
            f"–{format_timestamp(end)[:8]}]"
        )
        target = next(
            (segment for segment in marked if segment["end_ms"] >= start),
            None,
        )
        if target is not None:
            target["text"] = f"{marker} {target.get('text', '')}".strip()
    return marked


def transcript_text(segments):
    return "\n".join(
        segment.get("text", "").strip()
        for segment in segments
        if segment.get("text", "").strip()
    )


def write_srt(path, segments):
    blocks = []
    for number, segment in enumerate(segments, start=1):
        blocks.extend(
            [
                str(number),
                (
                    f"{format_timestamp(segment['start_ms'], ',')} --> "
                    f"{format_timestamp(segment['end_ms'], ',')}"
                ),
                segment.get("text", "").strip(),
                "",
            ]
        )
    Path(path).write_text("\n".join(blocks), encoding="utf-8")


def write_json(path, segments, metadata=None):
    data = dict(metadata or {})
    data["transcription"] = [
        {
            "timestamps": {
                "from": format_timestamp(segment["start_ms"]),
                "to": format_timestamp(segment["end_ms"]),
            },
            "offsets": {
                "from": int(segment["start_ms"]),
                "to": int(segment["end_ms"]),
            },
            "text": segment.get("text", ""),
        }
        for segment in segments
    ]
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
