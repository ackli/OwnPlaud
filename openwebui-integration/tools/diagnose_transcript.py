#!/usr/bin/env python3
"""Diagnostique les répétitions Whisper sans modifier le moteur PLAUD."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zlib
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "diagnostics"
WHISPER_CANDIDATES = (
    ROOT / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe",
    ROOT / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe",
    ROOT / "whisper.cpp" / "build" / "bin" / "whisper-cli",
)
WHISPER = next((path for path in WHISPER_CANDIDATES if path.is_file()), WHISPER_CANDIDATES[0])
MODEL = ROOT / "whisper.cpp" / "models" / "ggml-large-v3-turbo.bin"
VAD_MODEL = ROOT / "whisper.cpp" / "ggml-silero-v6.2.0.bin"

KNOWN_HALLUCINATIONS = (
    "Sous-titrage Société Radio-Canada",
    "Sous-titres réalisés par la communauté d'Amara.org",
    "Sous-titrage ST' 501",
    "Merci d'avoir regardé cette vidéo",
    "Abonnez-vous",
)

WORD_RE = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*", re.UNICODE)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(WORD_RE.findall(text))


def format_timestamp(milliseconds: int | float) -> str:
    total_seconds = max(0, int(round(float(milliseconds) / 1000)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_timestamp(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+):(\d+):(\d+)[,.](\d+)\s*", value or "")
    if not match:
        return 0
    hours, minutes, seconds, fraction = match.groups()
    milliseconds = int(fraction[:3].ljust(3, "0"))
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + milliseconds


def load_segments(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    raw_segments = data.get("transcription", data.get("segments", []))
    segments = []
    for raw in raw_segments if isinstance(raw_segments, list) else []:
        if not isinstance(raw, dict):
            continue
        offsets = raw.get("offsets", {})
        timestamps = raw.get("timestamps", {})
        start_ms = offsets.get("from")
        end_ms = offsets.get("to")
        if not isinstance(start_ms, (int, float)):
            start_ms = parse_timestamp(str(timestamps.get("from", "")))
        if not isinstance(end_ms, (int, float)):
            end_ms = parse_timestamp(str(timestamps.get("to", "")))
        text = str(raw.get("text", "")).strip()
        segments.append(
            {
                "start_ms": int(start_ms or 0),
                "end_ms": int(end_ms or start_ms or 0),
                "text": text,
            }
        )
    return segments


def repeated_ngrams(text: str) -> list[dict]:
    words = WORD_RE.findall(text)
    normalized = [normalize_text(word) for word in words]
    findings = []
    index = 0
    while index < len(words):
        best = None
        for size in range(1, min(8, (len(words) - index) // 3) + 1):
            pattern = normalized[index:index + size]
            count = 1
            while (
                index + (count + 1) * size <= len(words)
                and normalized[index + count * size:index + (count + 1) * size] == pattern
            ):
                count += 1
            if count >= 3:
                coverage = count * size
                candidate = {
                    "word_start": index,
                    "word_end": index + coverage,
                    "text": " ".join(words[index:index + size]),
                    "repetitions": count,
                    "coverage": coverage,
                }
                if (
                    best is None
                    or coverage > best["coverage"]
                    or (
                        coverage == best["coverage"]
                        and size < len(best["text"].split())
                    )
                ):
                    best = candidate
        if best:
            findings.append(best)
            index = best["word_end"]
        else:
            index += 1
    return findings


def compression_ratio(text: str) -> float:
    encoded = text.encode("utf-8")
    if not encoded:
        return 0.0
    compressed = zlib.compress(encoded)
    return len(encoded) / max(1, len(compressed))


def detect_loops(segments: list[dict], compression_threshold: float = 2.4) -> list[dict]:
    findings = []
    known = {normalize_text(text) for text in KNOWN_HALLUCINATIONS}

    for index, segment in enumerate(segments):
        text = segment["text"]
        inspection_text = re.sub(
            r"\[passage incertain [^\]]+\]",
            "",
            text,
            flags=re.IGNORECASE,
        )
        for repetition in repeated_ngrams(inspection_text):
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

        ratio = compression_ratio(inspection_text)
        if len(inspection_text) >= 80 and ratio > compression_threshold:
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
        reference = normalize_text(segments[index]["text"])
        end = index + 1
        if reference:
            while end < len(segments):
                candidate = normalize_text(segments[end]["text"])
                if not candidate or difflib.SequenceMatcher(None, reference, candidate).ratio() <= 0.9:
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
                    "text": segments[index]["text"],
                    "repetitions": end - index,
                }
            )
            index = end
        else:
            index += 1

    return sorted(findings, key=lambda item: (item["start_ms"], item["end_ms"], item["type"]))


def print_findings(label: str, findings: list[dict]) -> None:
    print(f"\n{label} : {len(findings)} signalement(s)")
    if not findings:
        print("  Aucune boucle détectée.")
        return
    for finding in findings:
        start = format_timestamp(finding["start_ms"])
        end = format_timestamp(finding["end_ms"])
        count = finding.get("repetitions", 1)
        text = " ".join(str(finding.get("text", "")).split())
        if len(text) > 120:
            text = text[:117] + "..."
        extra = ""
        if "compression_ratio" in finding:
            extra = f", ratio={finding['compression_ratio']}"
        print(f"  - {start}–{end} | {finding['type']} | ×{count}{extra} | {text}")


def probe_media(source: Path, output_dir: Path) -> dict:
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"ffprobe a échoué :\n{result.stderr.strip()}")
    data = json.loads(result.stdout)
    (output_dir / "ffprobe.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    audio_streams = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"]
    print(f"\nFlux audio : {len(audio_streams)}")
    for stream in audio_streams:
        tags = stream.get("tags") or {}
        duration = stream.get("duration") or data.get("format", {}).get("duration") or "?"
        print(
            "  - piste {index} : {codec}, {channels} canal(aux), langue={language}, durée={duration}s".format(
                index=stream.get("index", "?"),
                codec=stream.get("codec_name", "?"),
                channels=stream.get("channels", "?"),
                language=tags.get("language", "non précisée"),
                duration=duration,
            )
        )
    if not audio_streams:
        raise RuntimeError("Aucune piste audio détectée.")
    return data


def convert_to_wav(source: Path, destination: Path) -> None:
    if destination.is_file():
        return
    print("\nConversion de la première piste audio en WAV 16 kHz mono...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(destination),
        ],
        check=True,
    )


def whisper_arguments(wav: Path, prefix: Path, corrected: bool) -> list[str]:
    arguments = [
        str(WHISPER), "-m", str(MODEL), "-f", str(wav), "-l", "fr",
        "-t", "8", "-nt", "--vad", "--vad-model", str(VAD_MODEL),
        "-ojf", "-osrt", "--output-file", str(prefix),
    ]
    if corrected:
        arguments.extend(
            [
                "-mc", "0", "-sns", "-bs", "5", "-bo", "5",
                "-et", "2.8", "-nth", "0.6", "-vt", "0.6",
                "-vmsd", "30", "-vp", "150", "-pp",
            ]
        )
    return arguments


def whisper_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if os.name != "nt":
        return environment
    runtime_candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs" / "Ollama" / "lib" / "ollama" / "cuda_v13",
        Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib",
    ]
    runtime_dirs = [
        str(path) for path in runtime_candidates
        if path.is_dir() and (path / "cublas64_13.dll").is_file()
    ]
    if runtime_dirs:
        environment["PATH"] = os.pathsep.join(runtime_dirs + [environment.get("PATH", "")])
    return environment


def run_whisper(wav: Path, output_dir: Path, label: str, corrected: bool, reuse: bool) -> Path:
    prefix = output_dir / label
    json_path = prefix.with_suffix(".json")
    log_path = prefix.with_suffix(".whisper.log")
    if reuse and json_path.is_file():
        print(f"\nRéutilisation de {json_path}")
        return json_path

    command = whisper_arguments(wav, prefix, corrected)
    (output_dir / f"{label}.command.json").write_text(
        json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nTranscription {label}...")
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=whisper_environment(),
        )
        assert process.stderr is not None
        for line in process.stderr:
            log.write(line)
            log.flush()
            if "progress =" in line or "error" in line.casefold():
                print(line.rstrip())
        return_code = process.wait()
    if return_code:
        windows_code = f"0x{return_code & 0xFFFFFFFF:08X}"
        raise RuntimeError(
            f"whisper-cli a échoué ({return_code}, {windows_code}). Journal : {log_path}"
        )
    if not json_path.is_file():
        raise RuntimeError(f"Whisper n'a pas produit le JSON attendu : {json_path}")
    return json_path


def analyze_json(path: Path) -> int:
    findings = detect_loops(load_segments(path))
    print_findings(path.name, findings)
    return 1 if findings else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Localise les boucles d'une transcription Whisper et compare les réglages."
    )
    parser.add_argument("media", nargs="?", type=Path, help="Fichier audio ou vidéo à diagnostiquer")
    parser.add_argument(
        "--mode", choices=("baseline", "after", "compare"), default="compare",
        help="baseline = paramètres actuels ; after = anti-boucle ; compare = les deux",
    )
    parser.add_argument("--reuse", action="store_true", help="Réutiliser les JSON déjà produits")
    parser.add_argument("--analyze-json", type=Path, help="Analyser un JSON existant sans lancer Whisper")
    args = parser.parse_args()

    if args.analyze_json:
        return analyze_json(args.analyze_json.expanduser().resolve())
    if not args.media:
        parser.error("media est requis sauf avec --analyze-json")
    source = args.media.expanduser().resolve()
    if not source.is_file():
        parser.error(f"fichier introuvable : {source}")
    for executable in ("ffprobe", "ffmpeg"):
        if shutil.which(executable) is None:
            parser.error(f"{executable} est introuvable dans PATH")
    for required in (WHISPER, MODEL, VAD_MODEL):
        if not required.is_file():
            parser.error(f"fichier requis introuvable : {required}")

    output_dir = WORK / source.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_media(source, output_dir)
    wav = output_dir / "source.wav"
    convert_to_wav(source, wav)

    results = {}
    labels = []
    if args.mode in {"baseline", "compare"}:
        labels.append(("avant", False))
    if args.mode in {"after", "compare"}:
        labels.append(("apres", True))
    for label, corrected in labels:
        json_path = run_whisper(wav, output_dir, label, corrected, args.reuse)
        findings = detect_loops(load_segments(json_path))
        results[label] = findings
        print_findings(label.upper(), findings)
        (output_dir / f"{label}.loops.json").write_text(
            json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.mode == "compare":
        before = len(results.get("avant", []))
        after = len(results.get("apres", []))
        print(f"\nComparatif : {before} signalement(s) avant, {after} après, différence {after - before:+d}.")
    print(f"\nRésultats : {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nDiagnostic interrompu.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nERREUR : {error}", file=sys.stderr)
        raise SystemExit(1)
