#!/usr/bin/env python3

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import wave
import sys
import urllib.error
import urllib.request
from datetime import datetime
from itertools import islice
from pathlib import Path

from plaud_transcript import (
    clean_transcript_segments,
    detect_loops,
    format_timestamp,
    load_whisper_json,
    loop_score,
    mark_uncertain_segments,
    transcript_text,
    uncertain_ranges,
    write_json,
    write_srt,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
        write_through=True,
    )
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
        write_through=True,
    )

APP_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)

CUDA_RUNTIME_AVAILABLE = False
if os.name == "nt":
    cuda_runtime_candidates = [
        APP_ROOT,
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs" / "Ollama" / "lib" / "ollama" / "cuda_v13",
        Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib",
    ]
    runtime_paths = [
        str(path) for path in cuda_runtime_candidates
        if path.is_dir() and (path / "cublas64_13.dll").is_file()
    ]
    nvidia_driver = (
        Path(os.environ.get("WINDIR", "C:\\Windows"))
        / "System32" / "nvcuda.dll"
    )
    CUDA_RUNTIME_AVAILABLE = bool(runtime_paths) and nvidia_driver.is_file()
    os.environ["PATH"] = os.pathsep.join(
        [str(APP_ROOT), *runtime_paths, os.environ.get("PATH", "")]
    )

WORKSPACE = Path(os.environ.get("PLAUD_WORKSPACE", APP_ROOT)).expanduser().resolve()
RECORDINGS = WORKSPACE / "recordings"
TRANSCRIPTS = WORKSPACE / "transcripts"
NOTES = WORKSPACE / "notes"
WORK = WORKSPACE / "work"
QUEUE = WORKSPACE / "queue"
REPORTS = WORKSPACE / "reports"
STRUCTURED = WORK / "structured"
CUDA_WHISPER = APP_ROOT / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe"
CPU_WHISPER = APP_ROOT / "whisper.cpp" / "build-cpu" / "bin" / "Release" / "whisper-cli.exe"
WHISPER_CANDIDATES = (
    [CUDA_WHISPER, CPU_WHISPER]
    if CUDA_RUNTIME_AVAILABLE
    else [CPU_WHISPER, CUDA_WHISPER]
)
WHISPER_CANDIDATES.extend(
    [
        APP_ROOT / "whisper.cpp" / "build" / "bin" / "whisper-cli",
        APP_ROOT / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe",
    ]
)
WHISPER = next(
    (candidate for candidate in WHISPER_CANDIDATES if candidate.exists()),
    WHISPER_CANDIDATES[0],
)
WHISPER_MODELS = {
    "large-v3-turbo": APP_ROOT / "whisper.cpp" / "models" / "ggml-large-v3-turbo.bin",
    "large-v3": APP_ROOT / "whisper.cpp" / "models" / "ggml-large-v3.bin",
}
WHISPER_MODEL_KEY = os.environ.get(
    "PLAUD_WHISPER_MODEL",
    "large-v3-turbo",
).strip().casefold()
if WHISPER_MODEL_KEY not in WHISPER_MODELS:
    print(
        f"AVERTISSEMENT : modèle Whisper inconnu : {WHISPER_MODEL_KEY}. "
        "Utilisation de large-v3-turbo."
    )
    WHISPER_MODEL_KEY = "large-v3-turbo"
WHISPER_MODEL = WHISPER_MODELS[WHISPER_MODEL_KEY]
VAD_MODEL = APP_ROOT / "whisper.cpp" / "ggml-silero-v6.2.0.bin"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
PLAUD_CONFIG = APP_ROOT / "plaud_config.json"

ALLOWED_OLLAMA_MODELS = {
    "qwen3:1.7b",
    "qwen3:4b",
    "qwen3:8b",
    "qwen3:14b",
}

DEFAULT_WHISPER_PARAMS = {
    "language": "auto",
    "threads": 8,
    "max_context": 0,
    "suppress_nst": True,
    "beam_size": 5,
    "best_of": 5,
    "entropy_thold": 2.4,
    "no_speech_thold": 0.6,
    "vad": True,
    "vad_threshold": 0.5,
    "vad_max_speech_duration_s": 30,
    "vad_speech_pad_ms": 150,
    "print_progress": True,
    "temperature": 0.0,
    "chunk_seconds": 600,
}


def load_plaud_config():
    try:
        if PLAUD_CONFIG.is_file():
            data = json.loads(PLAUD_CONFIG.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except Exception as error:
        print(f"AVERTISSEMENT : configuration PLAUD invalide : {error}")
    return {}


def load_whisper_params(config):
    params = DEFAULT_WHISPER_PARAMS.copy()
    overrides = config.get("whisper_params", {})
    if not isinstance(overrides, dict):
        print("AVERTISSEMENT : whisper_params doit être un objet JSON.")
        return params
    for key, value in overrides.items():
        if key not in params:
            print(f"AVERTISSEMENT : paramètre Whisper inconnu ignoré : {key}")
            continue
        expected = type(params[key])
        if expected is bool:
            valid = isinstance(value, bool)
        elif expected is int:
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif expected is float:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            valid = isinstance(value, expected)
        if valid:
            params[key] = value
        else:
            print(f"AVERTISSEMENT : valeur invalide pour whisper_params.{key}")
    return params


PLAUD_SETTINGS = load_plaud_config()
DEFAULT_OLLAMA_MODEL = "qwen3:8b"
configured_ollama_model = str(
    PLAUD_SETTINGS.get("ollama_model", DEFAULT_OLLAMA_MODEL)
).strip()
if configured_ollama_model not in ALLOWED_OLLAMA_MODELS:
    print(f"AVERTISSEMENT : modèle Ollama non autorisé : {configured_ollama_model}")
    configured_ollama_model = DEFAULT_OLLAMA_MODEL
OLLAMA_MODEL = configured_ollama_model
WHISPER_PARAMS = load_whisper_params(PLAUD_SETTINGS)
requested_whisper_language = os.environ.get(
    "PLAUD_WHISPER_LANGUAGE",
    "",
).strip().casefold()
if requested_whisper_language:
    if re.fullmatch(r"(?:auto|[a-z]{2,3})", requested_whisper_language):
        WHISPER_PARAMS["language"] = requested_whisper_language
    else:
        print(
            f"AVERTISSEMENT : langue Whisper invalide : "
            f"{requested_whisper_language}. Détection automatique utilisée."
        )
        WHISPER_PARAMS["language"] = "auto"

def stop(message):
    print(f"\nERREUR : {message}\n")
    sys.exit(1)


def prepare_workspace():
    for folder in [WORKSPACE, RECORDINGS, TRANSCRIPTS, NOTES, WORK, QUEUE, REPORTS, STRUCTURED]:
        folder.mkdir(parents=True, exist_ok=True)


def check_installation():
    prepare_workspace()
    if not WHISPER.exists():
        stop(f"Whisper est introuvable : {WHISPER}")
    if not WHISPER_MODEL.exists():
        stop(
            f"Le modèle Whisper sélectionné ({WHISPER_MODEL_KEY}) "
            f"est introuvable : {WHISPER_MODEL}"
        )
    if not VAD_MODEL.exists():
        stop(f"Le modèle VAD est introuvable : {VAD_MODEL}")


def clean_name(text):
    text = text.lower().strip()
    replacements = {
        "à": "a", "â": "a", "ä": "a", "é": "e", "è": "e", "ê": "e", "ë": "e",
        "î": "i", "ï": "i", "ô": "o", "ö": "o", "ù": "u", "û": "u", "ü": "u", "ç": "c",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "note-vocale")[:60]


def make_name(title):
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stamp}_{clean_name(title)}"


def record_audio(name):
    output = RECORDINGS / f"{name}.wav"

    print("\n========================================")
    print("ENREGISTREMENT")
    print("========================================\n")
    print("L'enregistrement a commencé.")
    print("Arrête-le depuis l'interface PLAUD.\n")

    if os.name == "nt":
        try:
            import sounddevice as sd
        except ImportError:
            stop("Le module Python sounddevice est introuvable.")

        stop_event = threading.Event()

        def handle_stop(signum, frame):
            stop_event.set()

        def listen_for_gui_stop():
            try:
                command = sys.stdin.readline()
                if command.strip() == "PLAUD_STOP":
                    stop_event.set()
            except Exception:
                pass

        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)
        threading.Thread(target=listen_for_gui_stop, daemon=True).start()

        try:
            with wave.open(str(output), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)

                def audio_callback(indata, frames, time_info, status):
                    if status:
                        print(status, file=sys.stderr)
                    wav_file.writeframes(bytes(indata))

                with sd.RawInputStream(
                    samplerate=16000,
                    channels=1,
                    dtype="int16",
                    blocksize=1600,
                    callback=audio_callback,
                ):
                    while not stop_event.wait(0.1):
                        pass
        except Exception as error:
            stop(f"L'enregistrement audio a échoué : {error}")

    else:
        if shutil.which("pw-record") is None:
            stop("La commande pw-record est introuvable.")

        process = subprocess.Popen(
            [
                "pw-record",
                "--rate=16000",
                "--channels=1",
                "--channel-map=mono",
                "--format=s16",
                str(output),
            ],
            start_new_session=True,
        )

        try:
            process.wait()
        except KeyboardInterrupt:
            print("\nArrêt de l'enregistrement...")
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            process.wait()

    if not output.exists() or output.stat().st_size < 1000:
        stop("Le fichier audio est introuvable ou semble vide.")

    print(f"\nAudio enregistré :\n{output}\n")
    return output

def store_imported_audio(source, name):
    source = source.expanduser().resolve()
    if not source.is_file():
        stop(f"Fichier introuvable : {source}")
    extension = source.suffix or ".audio"
    stored = RECORDINGS / f"{name}{extension}"
    shutil.copy2(source, stored)
    print(f"\nAudio importé :\n{stored}\n")
    return stored


def probe_audio_streams(source, name):
    if shutil.which("ffprobe") is None:
        stop("ffprobe est introuvable.")
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_streams", "-show_format",
            "-of", "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        stop(f"ffprobe n'a pas pu lire le fichier : {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        stop(f"Réponse ffprobe invalide : {error}")
    audio_streams = [
        stream
        for stream in data.get("streams", [])
        if stream.get("codec_type") == "audio"
    ]
    atomic_write_text(
        WORK / f"{name}.ffprobe.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )
    if not audio_streams:
        stop("Aucune piste audio n'a été trouvée dans le fichier.")
    print(f"\nPistes audio détectées : {len(audio_streams)}")
    for position, stream in enumerate(audio_streams):
        tags = stream.get("tags") or {}
        print(
            f"  Piste {position + 1} : codec={stream.get('codec_name', '?')}, "
            f"canaux={stream.get('channels', '?')}, "
            f"langue={tags.get('language', 'non précisée')}"
        )
    if len(audio_streams) > 1:
        print(
            "⚠ Plusieurs pistes audio détectées. "
            "La première piste audio est utilisée."
        )
    return audio_streams


def warn_if_almost_silent(audio):
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(audio),
            "-af", "volumedetect",
            "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"mean_volume:\s*(-?inf|[-+]?[0-9.]+)\s*dB", result.stderr)
    if not match:
        return
    value = match.group(1).casefold()
    mean_volume = float("-inf") if value == "-inf" else float(value)
    if mean_volume < -50:
        print(
            f"⚠ Le fichier converti est presque muet "
            f"(volume moyen {mean_volume:.1f} dB). "
            "La transcription peut contenir des hallucinations."
        )


def convert_audio(source, name):
    if shutil.which("ffmpeg") is None:
        stop("ffmpeg est introuvable.")
    source = source.expanduser().resolve()
    if not source.is_file():
        stop(f"Fichier introuvable : {source}")
    probe_audio_streams(source, name)
    converted = WORK / f"{name}.wav"
    print("\nConversion du fichier audio...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-map", "0:a:0",
            "-vn", "-sn", "-dn",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(converted),
        ],
        check=True,
    )
    if not converted.exists():
        stop("La conversion audio a échoué.")
    warn_if_almost_silent(converted)
    return converted


def build_whisper_arguments(audio, output_prefix, params=None):
    params = params or WHISPER_PARAMS
    arguments = [
        str(WHISPER),
        "-m", str(WHISPER_MODEL),
        "-f", str(audio),
        "-l", str(params["language"]),
        "-t", str(params["threads"]),
        "-nt",
        "-mc", str(params["max_context"]),
        "-bs", str(params["beam_size"]),
        "-bo", str(params["best_of"]),
        "-et", str(params["entropy_thold"]),
        "-nth", str(params["no_speech_thold"]),
        "-tp", str(params["temperature"]),
    ]
    if params["suppress_nst"]:
        arguments.append("-sns")
    if params["vad"]:
        arguments.extend(
            [
                "--vad",
                "--vad-model", str(VAD_MODEL),
                "-vt", str(params["vad_threshold"]),
                "-vmsd", str(params["vad_max_speech_duration_s"]),
                "-vp", str(params["vad_speech_pad_ms"]),
            ]
        )
    if params["print_progress"]:
        arguments.append("-pp")
    arguments.extend(
        [
            "--output-txt",
            "--output-json-full",
            "--output-srt",
            "--output-file", str(output_prefix),
        ]
    )
    return arguments


def run_whisper(arguments, log_file, append=False):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with log_file.open(mode, encoding="utf-8", errors="replace") as log:
        if append:
            log.write("\n\n" + "=" * 72 + "\n")
        log.write("Commande : " + subprocess.list2cmdline(arguments) + "\n\n")
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
        )
        assert process.stderr is not None
        for line in process.stderr:
            log.write(line)
            log.flush()
            lowered = line.casefold()
            progress = re.search(r"progress\s*=\s*(\d+)%", line)
            if (
                (progress and 0 <= int(progress.group(1)) <= 100)
                or "error" in lowered
                or "warning" in lowered
            ):
                print(line.rstrip())
        return_code = process.wait()
    if return_code:
        code = f"0x{return_code & 0xFFFFFFFF:08X}"
        stop(f"Whisper a échoué ({code}). Consulte le journal : {log_file}")


def detect_silence_midpoints(audio):
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(audio),
            "-af", "silencedetect=noise=-35dB:d=0.5",
            "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    starts = [
        float(value)
        for value in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)
    ]
    ends = [
        float(value)
        for value in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)
    ]
    return [(start + end) / 2 for start, end in zip(starts, ends) if end > start]


def choose_chunk_ranges(audio, duration_seconds, chunk_seconds):
    if duration_seconds <= chunk_seconds:
        return [(0.0, duration_seconds)]
    silences = detect_silence_midpoints(audio)
    cuts = [0.0]
    target = float(chunk_seconds)
    while target < duration_seconds:
        candidates = [
            point for point in silences
            if cuts[-1] + chunk_seconds * 0.5 < point < duration_seconds
            and abs(point - target) <= min(120.0, chunk_seconds * 0.25)
        ]
        cut = min(candidates, key=lambda point: abs(point - target)) if candidates else target
        if duration_seconds - cut < chunk_seconds * 0.35:
            break
        cuts.append(cut)
        target = cut + chunk_seconds
    cuts.append(duration_seconds)
    return [
        (start, end)
        for start, end in zip(cuts, cuts[1:])
        if end - start > 0.1
    ]


def extract_audio_chunk(audio, destination, start_seconds, end_seconds):
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_seconds:.3f}",
            "-i", str(audio),
            "-t", f"{end_seconds - start_seconds:.3f}",
            "-map", "0:a:0",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(destination),
        ],
        check=True,
    )


def configured_hallucinations():
    values = PLAUD_SETTINGS.get("whisper_hallucinations")
    if isinstance(values, list) and all(isinstance(value, str) for value in values):
        return values
    return None


def _write_transcription_outputs(name, segments, events, attempts):
    output_prefix = TRANSCRIPTS / name
    transcript_file = Path(str(output_prefix) + ".txt")
    json_file = Path(str(output_prefix) + ".json")
    srt_file = Path(str(output_prefix) + ".srt")
    loops_file = WORK / f"{name}.loops.json"
    transcript = transcript_text(segments).strip()
    if not transcript:
        stop("La transcription est vide après le nettoyage.")
    atomic_write_text(transcript_file, transcript + "\n")
    write_json(
        json_file,
        segments,
        metadata={
            "result": {"language": WHISPER_PARAMS["language"]},
            "plaud": {
                "cleaned": True,
                "model": WHISPER_MODEL_KEY,
                "chunk_seconds": WHISPER_PARAMS["chunk_seconds"],
            },
        },
    )
    write_srt(srt_file, segments)
    atomic_write_text(
        loops_file,
        json.dumps(
            {"attempts": attempts, "corrections": events},
            ensure_ascii=False,
            indent=2,
        ),
    )
    if events:
        ranges = uncertain_ranges(events)
        labels = [
            f"{format_timestamp(start)[:8]}–{format_timestamp(end)[:8]}"
            for start, end in ranges
        ]
        preview = ", ".join(labels[:8])
        if len(labels) > 8:
            preview += f", … (+{len(labels) - 8})"
        print(f"\n⚠ {len(events)} boucle(s) corrigée(s) ou signalée(s) ({preview})")
    return transcript_file, transcript


def transcribe_in_chunks(audio, name, duration_seconds):
    chunk_seconds = max(60, int(WHISPER_PARAMS["chunk_seconds"]))
    chunks_dir = WORK / f"{name}_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    ranges = choose_chunk_ranges(audio, duration_seconds, chunk_seconds)
    atomic_write_text(
        chunks_dir / "manifest.json",
        json.dumps(
            {
                "source": str(audio),
                "model": WHISPER_MODEL_KEY,
                "language": WHISPER_PARAMS["language"],
                "duration_seconds": duration_seconds,
                "ranges": [
                    {"start": round(start, 3), "end": round(end, 3)}
                    for start, end in ranges
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    log_file = WORK / f"{name}.whisper.log"
    merged_segments = []
    attempts = []
    hallucinations = configured_hallucinations()
    cache_variant = re.sub(
        r"[^a-z0-9_-]+",
        "-",
        f"{WHISPER_MODEL_KEY}_{WHISPER_PARAMS['language']}",
    )

    for number, (start, end) in enumerate(ranges, start=1):
        print(
            f"\nTranscription morceau {number}/{len(ranges)} "
            f"({format_timestamp(start * 1000)[:8]}–{format_timestamp(end * 1000)[:8]})"
        )
        chunk_prefix = chunks_dir / f"chunk_{number:04d}_{cache_variant}"
        chunk_audio = chunk_prefix.with_suffix(".wav")
        chunk_json = chunk_prefix.with_suffix(".json")
        resumed = chunk_json.is_file()
        if not resumed:
            extract_audio_chunk(audio, chunk_audio, start, end)
            run_whisper(
                build_whisper_arguments(chunk_audio, chunk_prefix),
                log_file,
                append=log_file.exists(),
            )
        else:
            print("  Reprise : JSON existant réutilisé.")

        base_segments = load_whisper_json(chunk_json, offset_ms=round(start * 1000))
        base_findings = detect_loops(base_segments, hallucinations)
        selected_segments = base_segments
        selected_findings = base_findings
        selected_attempt = "normal"

        if base_findings:
            print(f"  ⚠ {len(base_findings)} signalement(s), nouvelle tentative stricte.")
            retry_prefix = chunks_dir / f"chunk_{number:04d}_{cache_variant}_retry"
            retry_json = retry_prefix.with_suffix(".json")
            if not retry_json.is_file():
                if not chunk_audio.is_file():
                    extract_audio_chunk(audio, chunk_audio, start, end)
                strict_params = WHISPER_PARAMS.copy()
                strict_params.update(
                    {
                        "max_context": 0,
                        "entropy_thold": 2.8,
                        "temperature": 0.2,
                        "vad_threshold": 0.6,
                    }
                )
                run_whisper(
                    build_whisper_arguments(chunk_audio, retry_prefix, strict_params),
                    log_file,
                    append=True,
                )
            retry_segments = load_whisper_json(retry_json, offset_ms=round(start * 1000))
            retry_findings = detect_loops(retry_segments, hallucinations)
            if loop_score(retry_findings) <= loop_score(base_findings):
                selected_segments = retry_segments
                selected_findings = retry_findings
                selected_attempt = "strict"

        attempts.append(
            {
                "chunk": number,
                "model": WHISPER_MODEL_KEY,
                "language": WHISPER_PARAMS["language"],
                "start_ms": round(start * 1000),
                "end_ms": round(end * 1000),
                "resumed": resumed,
                "normal_findings": len(base_findings),
                "selected": selected_attempt,
                "remaining_findings": len(selected_findings),
            }
        )
        merged_segments.extend(selected_segments)

    cleaned, events = clean_transcript_segments(merged_segments, hallucinations)
    post_clean_findings = detect_loops(cleaned, hallucinations)
    if post_clean_findings:
        cleaned = mark_uncertain_segments(cleaned, post_clean_findings)
        for start, end in uncertain_ranges(post_clean_findings):
            events.append(
                {
                    "type": "passage_incertain",
                    "start_ms": start,
                    "end_ms": end,
                    "action": "zone marquée après deux transcriptions suspectes",
                }
            )
        events.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["type"]))
    return _write_transcription_outputs(name, cleaned, events, attempts)


def transcribe(audio, name):
    output_prefix = TRANSCRIPTS / name
    log_file = WORK / f"{name}.whisper.log"
    print("\n========================================")
    print("TRANSCRIPTION WHISPER")
    print("========================================\n")
    print(f"Modèle : {WHISPER_MODEL_KEY}\n")
    print(f"Langue source : {WHISPER_PARAMS['language']}\n")
    duration_seconds = get_audio_duration_seconds(audio)
    chunk_seconds = max(60, int(WHISPER_PARAMS["chunk_seconds"]))
    if duration_seconds > chunk_seconds:
        transcript_file, transcript = transcribe_in_chunks(audio, name, duration_seconds)
        print("\nTranscription terminée.")
        return transcript_file, transcript

    run_whisper(build_whisper_arguments(audio, output_prefix), log_file)
    transcript_file = Path(str(output_prefix) + ".txt")
    json_file = Path(str(output_prefix) + ".json")
    srt_file = Path(str(output_prefix) + ".srt")
    if not transcript_file.exists():
        stop("La transcription n'a pas été créée.")
    if not json_file.exists() or not srt_file.exists():
        stop("Les sorties horodatées JSON/SRT n'ont pas été créées.")
    hallucinations = configured_hallucinations()
    segments = load_whisper_json(json_file)
    findings = detect_loops(segments, hallucinations)
    cleaned, events = clean_transcript_segments(segments, hallucinations)
    transcript_file, transcript = _write_transcription_outputs(
        name,
        cleaned,
        events,
        [
            {
                "chunk": 1,
                "model": WHISPER_MODEL_KEY,
                "language": WHISPER_PARAMS["language"],
                "start_ms": 0,
                "end_ms": round(duration_seconds * 1000),
                "resumed": False,
                "normal_findings": len(findings),
                "selected": "normal",
                "remaining_findings": len(detect_loops(cleaned, hallucinations)),
            }
        ],
    )
    print("\nTranscription terminée.")
    return transcript_file, transcript


def split_text(text, max_length=6000):
    """Découpe une transcription sans casser brutalement les phrases quand c'est possible."""
    parts = []
    text = text.strip()
    while len(text) > max_length:
        candidates = [
            text.rfind("\n\n", 0, max_length),
            text.rfind(". ", 0, max_length),
            text.rfind("! ", 0, max_length),
            text.rfind("? ", 0, max_length),
            text.rfind(" ", 0, max_length),
        ]
        cut = max(candidates)
        if cut < max_length // 2:
            cut = max_length
        elif text[cut:cut + 2] in {". ", "! ", "? "}:
            cut += 1
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def ask_ollama(
    prompt,
    *,
    json_mode=False,
    temperature=0.15,
    num_ctx=8192,
    num_predict=4096,
):
    """Appelle Qwen. json_mode force une sortie JSON quand Ollama le permet."""
    system = (
        "Tu es le moteur d'analyse documentaire de PLAUD. Tu réponds en français. "
        "Tu travailles strictement à partir des informations fournies. "
        "Tu n'inventes jamais de fait, de décision, de responsable, de date, de montant ou d'échéance. "
        "Tu distingues clairement faits, hypothèses, propositions et décisions. "
        "Tu privilégies une structure concise, non répétitive et utile."
    )
    if json_mode:
        system += " Tu retournes uniquement un objet JSON valide, sans Markdown ni commentaire autour."

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    if json_mode:
        payload["format"] = "json"

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    heartbeat_stop = threading.Event()

    def report_ollama_activity():
        elapsed = 0
        while not heartbeat_stop.wait(15):
            elapsed += 15
            print(f"  Ollama travaille toujours… {elapsed} s")

    heartbeat = threading.Thread(target=report_ollama_activity, daemon=True)
    heartbeat.start()
    try:
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=900) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace").strip()
                if attempt == 0 and error.code in {404, 500, 502, 503}:
                    print(
                        f"⚠ Ollama a répondu HTTP {error.code}; "
                        "nouvelle tentative dans 2 secondes."
                    )
                    time.sleep(2)
                    continue
                raise RuntimeError(
                    f"Ollama a répondu HTTP {error.code}"
                    + (f" : {detail}" if detail else ".")
                ) from error
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)

    text = result.get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("Ollama a retourné une réponse vide.")
    return text


def _extract_json_object(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last > first:
            return json.loads(text[first:last + 1])
        raise


def ask_ollama_json(prompt, *, num_ctx=8192, num_predict=4096):
    """Retourne un dictionnaire JSON et tente une réparation unique si nécessaire."""
    raw = ask_ollama(
        prompt,
        json_mode=True,
        num_ctx=num_ctx,
        num_predict=num_predict,
    )
    try:
        data = _extract_json_object(raw)
    except Exception:
        repaired = ask_ollama(
            "Répare le JSON ci-dessous. Retourne uniquement le JSON valide, sans explication.\n\n" + raw,
            json_mode=True,
            temperature=0.0,
            num_ctx=num_ctx,
            num_predict=num_predict,
        )
        data = _extract_json_object(repaired)
    if not isinstance(data, dict):
        raise RuntimeError("La réponse structurée de Qwen n'est pas un objet JSON.")
    return data


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _string_list(value):
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        text = _text(item)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def extract_chunk(part, number, total):
    """Extraction factuelle : aucune mise en page, uniquement la compréhension du contenu."""
    prompt = f"""
Tu analyses la partie {number}/{total} d'une transcription audio.

OBJECTIF
Extraire les informations utiles sans les embellir et sans rien inventer.

Retourne exactement un objet JSON compatible avec cette structure :
{{
  "topics": [{{"name": "", "details": [""]}}],
  "facts": [""],
  "decisions": [{{"text": "", "context": ""}}],
  "actions": [{{"action": "", "owner": "", "due": "", "status": ""}}],
  "people": [{{"name": "", "role_or_context": ""}}],
  "dates_numbers": [{{"label": "", "value": "", "context": ""}}],
  "comparisons": [
    {{
      "subject": "",
      "columns": [""],
      "rows": [[""]]
    }}
  ],
  "open_questions": [""]
}}

RÈGLES
- Une information absente doit être une chaîne vide ou une liste vide.
- Ne transforme jamais une suggestion en décision.
- Ne crée jamais un responsable ou une échéance implicite.
- Préserve les noms, dates, montants, unités et nombres utiles.
- Regroupe les détails par sujet.
- Crée une comparaison uniquement si plusieurs éléments partagent réellement les mêmes attributs.
- Pas de Markdown.

TRANSCRIPTION :
{part}
"""
    return normalize_extraction(
        ask_ollama_json(prompt, num_predict=2048)
    )


def normalize_extraction(data):
    result = {
        "topics": [],
        "facts": _string_list(data.get("facts")),
        "decisions": [],
        "actions": [],
        "people": [],
        "dates_numbers": [],
        "comparisons": [],
        "open_questions": _string_list(data.get("open_questions")),
    }

    for topic in data.get("topics", []) if isinstance(data.get("topics"), list) else []:
        if not isinstance(topic, dict):
            continue
        name = _text(topic.get("name"))
        details = _string_list(topic.get("details"))
        if name or details:
            result["topics"].append({"name": name or "Sujet", "details": details})

    for decision in data.get("decisions", []) if isinstance(data.get("decisions"), list) else []:
        if isinstance(decision, str):
            decision = {"text": decision, "context": ""}
        if isinstance(decision, dict):
            text = _text(decision.get("text"))
            if text:
                result["decisions"].append({"text": text, "context": _text(decision.get("context"))})

    for action in data.get("actions", []) if isinstance(data.get("actions"), list) else []:
        if isinstance(action, str):
            action = {"action": action}
        if isinstance(action, dict):
            text = _text(action.get("action"))
            if text:
                result["actions"].append({
                    "action": text,
                    "owner": _text(action.get("owner")),
                    "due": _text(action.get("due")),
                    "status": _text(action.get("status")),
                })

    for person in data.get("people", []) if isinstance(data.get("people"), list) else []:
        if isinstance(person, dict) and _text(person.get("name")):
            result["people"].append({
                "name": _text(person.get("name")),
                "role_or_context": _text(person.get("role_or_context")),
            })

    for item in data.get("dates_numbers", []) if isinstance(data.get("dates_numbers"), list) else []:
        if isinstance(item, dict) and (_text(item.get("value")) or _text(item.get("label"))):
            result["dates_numbers"].append({
                "label": _text(item.get("label")),
                "value": _text(item.get("value")),
                "context": _text(item.get("context")),
            })

    for table in data.get("comparisons", []) if isinstance(data.get("comparisons"), list) else []:
        normalized = normalize_table(table, title_key="subject")
        if normalized:
            result["comparisons"].append(normalized)

    return result


def batched(items, size):
    iterator = iter(items)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def merge_extractions(group, round_number, batch_number):
    serialized_group = json.dumps(group, ensure_ascii=False)
    prompt = f"""
Fusionne les extractions JSON ci-dessous provenant du même enregistrement audio.

OBJECTIF
Créer une extraction consolidée sans perdre les informations utiles et sans introduire d'information nouvelle.

Retourne exactement la même structure JSON : topics, facts, decisions, actions, people,
dates_numbers, comparisons, open_questions.

RÈGLES
- Supprime les doublons sémantiques évidents.
- Conserve les formulations les plus précises.
- Ne fusionne pas deux personnes ou deux actions différentes.
- Une proposition reste une proposition : ne la transforme jamais en décision.
- Ne crée pas de tableau si les éléments ne sont pas réellement comparables.
- Aucun Markdown.

TOUR DE CONSOLIDATION : {round_number}
LOT : {batch_number}

DONNÉES :
{serialized_group}
"""
    estimated_tokens = max(1, round(len(prompt) / 3.5))
    num_ctx = 8192
    if estimated_tokens > 7500:
        num_ctx = 16384
        print(
            f"⚠ Fusion estimée à {estimated_tokens} tokens : "
            f"contexte Ollama augmenté à {num_ctx}."
        )
    return normalize_extraction(
        ask_ollama_json(prompt, num_ctx=num_ctx, num_predict=2048)
    )


def _dedupe_key(value):
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _extend_unique(target, values):
    seen = {_dedupe_key(value) for value in target}
    for value in values:
        key = _dedupe_key(value)
        if key and key not in seen:
            seen.add(key)
            target.append(value)


def reduce_extractions(extractions):
    """Fusion déterministe : évite une pyramide d'appels LLM lente et fragile."""
    extractions = [normalize_extraction(item) for item in extractions]
    if not extractions:
        return normalize_extraction({})

    print(f"\nConsolidation locale de {len(extractions)} bloc(s)...")
    result = normalize_extraction({})
    topics_by_name = {}

    for extraction in extractions:
        for topic in extraction["topics"]:
            key = _dedupe_key(topic.get("name", "Sujet"))
            if key not in topics_by_name:
                topics_by_name[key] = {
                    "name": topic.get("name") or "Sujet",
                    "details": [],
                }
            _extend_unique(topics_by_name[key]["details"], topic.get("details", []))

        for field in (
            "facts",
            "decisions",
            "actions",
            "people",
            "dates_numbers",
            "comparisons",
            "open_questions",
        ):
            _extend_unique(result[field], extraction[field])

    result["topics"] = list(topics_by_name.values())
    print("Consolidation terminée.")
    return result


def normalize_table(table, title_key="title"):
    if not isinstance(table, dict):
        return None
    title = _text(table.get(title_key) or table.get("title") or table.get("subject"))
    columns = [_text(c) for c in table.get("columns", [])] if isinstance(table.get("columns"), list) else []
    columns = [c for c in columns if c]
    rows = table.get("rows", []) if isinstance(table.get("rows"), list) else []
    if len(columns) < 2 or not rows:
        return None

    normalized_rows = []
    for row in rows:
        if not isinstance(row, list):
            continue
        cells = [_text(cell) for cell in row[:len(columns)]]
        cells.extend([""] * (len(columns) - len(cells)))
        if any(cells):
            normalized_rows.append(cells)
    if not normalized_rows:
        return None

    # Supprime les colonnes qui sont presque entièrement vides.
    keep_indexes = []
    for index, column in enumerate(columns):
        non_empty = sum(1 for row in normalized_rows if row[index])
        if non_empty >= max(1, (len(normalized_rows) + 1) // 2):
            keep_indexes.append(index)
    if len(keep_indexes) < 2:
        keep_indexes = list(range(len(columns)))

    columns = [columns[i] for i in keep_indexes][:6]
    normalized_rows = [[row[i] for i in keep_indexes[:6]] for row in normalized_rows]

    return {"title": title, "columns": columns, "rows": normalized_rows}


def ollama_context_for_prompt(prompt, output_tokens=4096):
    estimated_input = max(1, round(len(prompt) / 3.5))
    required = estimated_input + output_tokens + 512
    for context in (8192, 16384, 32768):
        if required <= context:
            if context > 8192:
                print(
                    f"⚠ Document long : contexte Ollama {context} "
                    f"(entrée estimée à {estimated_input} tokens)."
                )
            return context
    print(
        f"⚠ Entrée très longue ({estimated_input} tokens estimés) : "
        "contexte Ollama plafonné à 32768."
    )
    return 32768


def _sample_evenly(items, limit):
    items = list(items)
    if len(items) <= limit:
        return items
    if limit <= 1:
        return items[:limit]
    indexes = {
        round(index * (len(items) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [items[index] for index in sorted(indexes)]


def _fallback_summary(extraction):
    candidates = []
    for topic in extraction.get("topics", []):
        name = _text(topic.get("name"))
        details = topic.get("details", [])
        if name and details:
            candidates.append(f"{name} : {_text(details[0])}")
        elif name:
            candidates.append(name)
    candidates.extend(extraction.get("facts", []))
    candidates = [_text(value) for value in candidates if _text(value)]
    summary = " ".join(candidates[:3])
    return {
        "document_type": "note",
        "summary": summary or "Synthèse non disponible.",
        "key_points": candidates[:8],
    }


def _compact_extraction_for_summary(extraction):
    topics = []
    for topic in _sample_evenly(extraction.get("topics", []), 30):
        topics.append(
            {
                "name": _text(topic.get("name")),
                "details": _sample_evenly(topic.get("details", []), 3),
            }
        )
    return {
        "topics": topics,
        "facts": _sample_evenly(extraction.get("facts", []), 50),
        "decisions": _sample_evenly(extraction.get("decisions", []), 30),
        "actions": _sample_evenly(extraction.get("actions", []), 40),
        "open_questions": _sample_evenly(
            extraction.get("open_questions", []),
            25,
        ),
        "source_counts": {
            key: len(extraction.get(key, []))
            for key in extraction
            if isinstance(extraction.get(key), list)
        },
    }


def create_deterministic_document(title, extraction, editorial=None):
    editorial = editorial if isinstance(editorial, dict) else {}
    fallback = _fallback_summary(extraction)
    document_data = {
        "document_type": (
            _text(editorial.get("document_type"))
            or fallback["document_type"]
        ),
        "summary": (
            _text(editorial.get("summary"))
            or fallback["summary"]
        ),
        "key_points": (
            _string_list(editorial.get("key_points"))
            or fallback["key_points"]
        ),
        "decisions": extraction.get("decisions", []),
        "actions": extraction.get("actions", []),
        "open_questions": extraction.get("open_questions", []),
        "sections": [],
    }

    for topic in extraction.get("topics", []):
        name = _text(topic.get("name")) or "Sujet"
        details = _string_list(topic.get("details"))
        if details:
            document_data["sections"].append(
                {
                    "title": name,
                    "blocks": [{"type": "bullets", "items": details}],
                }
            )

    facts = _string_list(extraction.get("facts"))
    if facts:
        document_data["sections"].append(
            {
                "title": "Faits et informations",
                "blocks": [{"type": "bullets", "items": facts}],
            }
        )

    for comparison in extraction.get("comparisons", []):
        table = normalize_table(comparison)
        if table:
            document_data["sections"].append(
                {
                    "title": table.get("title") or "Comparaison",
                    "blocks": [
                        {
                            "type": "table",
                            "title": table.get("title", ""),
                            "columns": table["columns"],
                            "rows": table["rows"],
                        }
                    ],
                }
            )

    people = extraction.get("people", [])
    if people:
        document_data["sections"].append(
            {
                "title": "Personnes mentionnées",
                "blocks": [
                    {
                        "type": "table",
                        "columns": ["Personne", "Rôle ou contexte"],
                        "rows": [
                            [
                                _text(person.get("name")),
                                _text(person.get("role_or_context")),
                            ]
                            for person in people
                        ],
                    }
                ],
            }
        )

    dates_numbers = extraction.get("dates_numbers", [])
    if dates_numbers:
        document_data["sections"].append(
            {
                "title": "Dates et chiffres",
                "blocks": [
                    {
                        "type": "table",
                        "columns": ["Élément", "Valeur", "Contexte"],
                        "rows": [
                            [
                                _text(item.get("label")),
                                _text(item.get("value")),
                                _text(item.get("context")),
                            ]
                            for item in dates_numbers
                        ],
                    }
                ],
            }
        )

    return normalize_document(document_data, title)


def create_large_structured_document(title, extraction):
    print(
        "Document long : résumé éditorial compact, "
        "puis assemblage déterministe sécurisé."
    )
    compact = _compact_extraction_for_summary(extraction)
    prompt = f"""
Tu rédiges la synthèse d'un enregistrement long.

Retourne uniquement ce JSON court :
{{
  "document_type": "reunion|cours|idee|entretien|compte_rendu|note|autre",
  "summary": "un paragraphe concis",
  "key_points": ["maximum 10 points"]
}}

RÈGLES
- Réponds en français.
- N'invente aucune information.
- Conserve les éléments les plus importants et représentatifs de tout le document.
- Pas de sections, décisions, actions ou tableaux dans cette réponse.

TITRE : {title}

EXTRACTION COMPACTE :
{json.dumps(compact, ensure_ascii=False)}
"""
    editorial = None
    try:
        editorial = ask_ollama_json(
            prompt,
            num_ctx=ollama_context_for_prompt(prompt, output_tokens=1536),
            num_predict=1536,
        )
    except Exception as error:
        print(
            f"⚠ Résumé Ollama invalide ({error}). "
            "Utilisation de la synthèse de secours."
        )
    return create_deterministic_document(title, extraction, editorial)


def create_structured_document(title, extraction):
    serialized_extraction = json.dumps(extraction, ensure_ascii=False)
    estimated_tokens = max(1, round(len(serialized_extraction) / 3.5))
    if estimated_tokens > 10_000:
        return create_large_structured_document(title, extraction)

    prompt = f"""
Tu transformes une extraction factuelle d'un enregistrement en structure de note professionnelle.

TITRE FOURNI PAR L'UTILISATEUR : {title}

Retourne uniquement ce JSON :
{{
  "schema_version": 1,
  "document_type": "reunion|cours|idee|entretien|compte_rendu|note|autre",
  "summary": "",
  "key_points": [""],
  "decisions": [{{"text": "", "context": ""}}],
  "actions": [{{"action": "", "owner": "", "due": "", "status": ""}}],
  "open_questions": [""],
  "sections": [
    {{
      "title": "",
      "blocks": [
        {{"type": "paragraph", "text": ""}},
        {{"type": "bullets", "items": [""]}},
        {{"type": "table", "title": "", "columns": [""], "rows": [[""]]}}
      ]
    }}
  ]
}}

LOGIQUE DE RÉDACTION
- Le résumé doit permettre de comprendre l'essentiel rapidement.
- Les key_points doivent être peu nombreux, précis et non redondants.
- Les décisions ne contiennent que des décisions réellement présentes dans l'extraction.
- Les actions ne contiennent que de vraies actions. N'invente jamais owner/due/status.
- Les sections détaillées s'adaptent au contenu ; n'impose pas un plan générique.
- Les sections ne doivent pas répéter le résumé, les décisions ou les actions.
- Crée un bloc table uniquement quand une relation tabulaire améliore réellement la lecture :
  plusieurs actions comparables, plusieurs options, plusieurs dates/événements,
  plusieurs chiffres/indicateurs, plusieurs personnes avec les mêmes attributs, planning, budget, comparatif.
- Ne crée pas de tableau pour un seul élément, pour des paragraphes longs, ni si la majorité des cellules seraient vides.
- Maximum 6 colonnes par tableau.
- Les sections vides sont interdites.
- Aucun fait nouveau.

EXTRACTION SOURCE :
{serialized_extraction}
"""
    try:
        return normalize_document(
            ask_ollama_json(
                prompt,
                num_ctx=ollama_context_for_prompt(prompt),
                num_predict=4096,
            ),
            title,
        )
    except Exception as error:
        print(
            f"⚠ Structuration Ollama invalide ({error}). "
            "Assemblage déterministe de secours."
        )
        return create_deterministic_document(title, extraction)


def normalize_document(data, title=""):
    if not isinstance(data, dict):
        raise RuntimeError("Document structuré invalide.")

    document = {
        "schema_version": 1,
        "title": title,
        "document_type": _text(data.get("document_type")) or "note",
        "summary": _text(data.get("summary")),
        "key_points": _string_list(data.get("key_points")),
        "decisions": [],
        "actions": [],
        "open_questions": _string_list(data.get("open_questions")),
        "sections": [],
    }

    for decision in data.get("decisions", []) if isinstance(data.get("decisions"), list) else []:
        if isinstance(decision, str):
            decision = {"text": decision}
        if isinstance(decision, dict) and _text(decision.get("text")):
            document["decisions"].append({
                "text": _text(decision.get("text")),
                "context": _text(decision.get("context")),
            })

    for action in data.get("actions", []) if isinstance(data.get("actions"), list) else []:
        if isinstance(action, str):
            action = {"action": action}
        if isinstance(action, dict) and _text(action.get("action")):
            document["actions"].append({
                "action": _text(action.get("action")),
                "owner": _text(action.get("owner")),
                "due": _text(action.get("due")),
                "status": _text(action.get("status")),
            })

    for section in data.get("sections", []) if isinstance(data.get("sections"), list) else []:
        if not isinstance(section, dict):
            continue
        section_title = _text(section.get("title"))
        blocks = []
        for block in section.get("blocks", []) if isinstance(section.get("blocks"), list) else []:
            if not isinstance(block, dict):
                continue
            kind = _text(block.get("type")).lower()
            if kind == "paragraph":
                text = _text(block.get("text"))
                if text:
                    blocks.append({"type": "paragraph", "text": text})
            elif kind == "bullets":
                items = _string_list(block.get("items"))
                if items:
                    blocks.append({"type": "bullets", "items": items})
            elif kind == "table":
                table = normalize_table(block)
                if table:
                    table["type"] = "table"
                    blocks.append(table)
        if section_title and blocks:
            document["sections"].append({"title": section_title, "blocks": blocks})

    if not document["summary"]:
        # Une note doit au minimum avoir une synthèse utile.
        fallback_bits = document["key_points"][:3]
        if fallback_bits:
            document["summary"] = " ".join(fallback_bits)
        else:
            document["summary"] = "Synthèse non disponible."

    return document


def create_structured_note(title, transcript, name=None):
    parts = split_text(transcript)
    print("\n========================================")
    print("ANALYSE DE LA TRANSCRIPTION")
    print("========================================\n")
    print(f"Modèle Ollama : {OLLAMA_MODEL}\n")

    extractions = []
    total = len(parts)
    transcript_digest = hashlib.sha256(
        transcript.encode("utf-8")
    ).hexdigest()[:12]
    cache_dir = WORK / f"{name}_analysis" if name else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    for number, part in enumerate(parts, start=1):
        cache_file = (
            cache_dir / f"{transcript_digest}_{number:04d}.json"
            if cache_dir
            else None
        )
        if cache_file and cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                extractions.append(normalize_extraction(cached))
                print(f"Analyse partie {number}/{total} : reprise du cache.")
                continue
            except Exception:
                pass

        print(f"Analyse partie {number}/{total}...")
        extraction = extract_chunk(part, number, total)
        extractions.append(extraction)
        if cache_file:
            atomic_write_text(
                cache_file,
                json.dumps(extraction, ensure_ascii=False, indent=2),
            )

    print("\n========================================")
    print("CONSOLIDATION DES INFORMATIONS")
    print("========================================\n")
    merged = reduce_extractions(extractions)

    print("\n========================================")
    print("STRUCTURATION DE LA NOTE")
    print("========================================\n")
    return create_structured_document(title, merged)


def atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return path


def save_structured_json(name, document):
    path = STRUCTURED / f"{name}.json"
    atomic_write_text(path, json.dumps(document, indent=2, ensure_ascii=False))
    return path


def _md_escape_cell(value):
    return _text(value).replace("|", "\\|").replace("\n", " ")


def markdown_table(columns, rows):
    columns = [_md_escape_cell(c) for c in columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        cells = [_md_escape_cell(cell) for cell in row]
        cells.extend([""] * (len(columns) - len(cells)))
        lines.append("| " + " | ".join(cells[:len(columns)]) + " |")
    return "\n".join(lines)


def _actions_as_table(actions):
    if len(actions) < 2:
        return None
    candidates = [
        ("Action", "action"),
        ("Responsable", "owner"),
        ("Échéance", "due"),
        ("Statut", "status"),
    ]
    useful = []
    for label, key in candidates:
        non_empty = sum(1 for action in actions if _text(action.get(key)))
        if key == "action" or non_empty >= max(1, (len(actions) + 1) // 2):
            useful.append((label, key))
    if len(useful) < 2:
        return None
    columns = [label for label, _ in useful]
    rows = [[_text(action.get(key)) for _, key in useful] for action in actions]
    return {"title": "Plan d'action", "columns": columns, "rows": rows}


def render_markdown(title, audio, transcript_file, transcript, document, duration_seconds=0):
    date = datetime.now().strftime("%d/%m/%Y à %H:%M")
    duration = format_seconds(duration_seconds) if duration_seconds else "Non disponible"
    lines = [
        f"# {title}",
        "",
        f"**Date :** {date}",
        f"**Durée :** {duration}",
        f"**Dossier :** {WORKSPACE.name}",
        f"**Audio :** `{audio.name}`",
        f"**Transcription :** `{transcript_file.name}`",
        "",
        "---",
        "",
        "## Synthèse",
        "",
        document["summary"],
    ]

    if document["key_points"]:
        lines += ["", "## À retenir", ""]
        lines += [f"- {item}" for item in document["key_points"]]

    if document["decisions"]:
        lines += ["", "## Décisions", ""]
        for decision in document["decisions"]:
            text = decision["text"]
            context = decision.get("context", "")
            lines.append(f"- {text}" + (f" — {context}" if context else ""))

    if document["actions"]:
        lines += ["", "## Actions", ""]
        action_table = _actions_as_table(document["actions"])
        if action_table:
            lines.append(markdown_table(action_table["columns"], action_table["rows"]))
        else:
            for action in document["actions"]:
                details = []
                if action.get("owner"):
                    details.append(f"Responsable : {action['owner']}")
                if action.get("due"):
                    details.append(f"Échéance : {action['due']}")
                if action.get("status"):
                    details.append(f"Statut : {action['status']}")
                suffix = f" ({'; '.join(details)})" if details else ""
                lines.append(f"- {action['action']}{suffix}")

    for section in document["sections"]:
        lines += ["", f"## {section['title']}", ""]
        for block in section["blocks"]:
            if block["type"] == "paragraph":
                lines += [block["text"], ""]
            elif block["type"] == "bullets":
                lines += [f"- {item}" for item in block["items"]]
                lines.append("")
            elif block["type"] == "table":
                if block.get("title"):
                    lines += [f"### {block['title']}", ""]
                lines += [markdown_table(block["columns"], block["rows"]), ""]

    if document["open_questions"]:
        lines += ["", "## Questions ouvertes", ""]
        lines += [f"- {item}" for item in document["open_questions"]]

    lines += [
        "",
        "---",
        "",
        "## Annexe - Transcription complète",
        "",
        transcript,
        "",
    ]
    return "\n".join(lines)


def save_markdown_note(title, name, audio, transcript_file, transcript, document, duration_seconds=0):
    print("\nCRÉATION DU MARKDOWN...")
    note_file = NOTES / f"{name}.md"
    content = render_markdown(
        title, audio, transcript_file, transcript, document, duration_seconds
    )
    return atomic_write_text(note_file, content)


def format_seconds(seconds):
    seconds = int(max(0, seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {seconds:02d} s"
    return f"{seconds} s"


def get_audio_duration_seconds(path):
    if shutil.which("ffprobe"):
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except Exception:
            pass
    return 0.0


def _pdf_imports():
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            BaseDocTemplate, Frame, KeepTogether, ListFlowable, ListItem,
            NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer,
            Table, TableStyle,
        )
        from reportlab.platypus.tableofcontents import TableOfContents
    except ImportError as error:
        raise RuntimeError(
            "ReportLab est requis pour créer le PDF. Installe-le avec : pip install reportlab"
        ) from error
    return locals()


def _pdf_text(value):
    return html.escape(_text(value)).replace("\n", "<br/>")


def _document_mode(document, transcript):
    complexity = len(document.get("sections", []))
    complexity += len(document.get("decisions", [])) // 2
    complexity += len(document.get("actions", [])) // 2
    table_count = sum(
        1
        for section in document.get("sections", [])
        for block in section.get("blocks", [])
        if block.get("type") == "table"
    )
    complexity += table_count
    if len(transcript) > 30000 or complexity >= 9:
        return "LONG"
    if len(transcript) < 5000 and complexity <= 3:
        return "COMPACT"
    return "STANDARD"


def _column_widths(columns, rows, available_width):
    scores = []
    for index, column in enumerate(columns):
        lengths = [len(_text(column))]
        for row in rows[:30]:
            if index < len(row):
                lengths.append(min(len(_text(row[index])), 120))
        average = sum(lengths) / max(1, len(lengths))
        scores.append(max(8.0, average))
    total = sum(scores) or 1
    widths = [available_width * (score / total) for score in scores]
    minimum = min(26 * 2.83465, available_width / max(1, len(columns)) * 0.60)
    widths = [max(minimum, width) for width in widths]
    factor = available_width / sum(widths)
    return [width * factor for width in widths]


def generate_pdf(title, name, audio, transcript_file, transcript, document, duration_seconds=0):
    print("\n========================================")
    print("CRÉATION DU PDF")
    print("========================================\n")

    r = _pdf_imports()
    colors = r["colors"]
    A4 = r["A4"]
    landscape = r["landscape"]
    mm = r["mm"]
    ParagraphStyle = r["ParagraphStyle"]
    getSampleStyleSheet = r["getSampleStyleSheet"]
    BaseDocTemplate = r["BaseDocTemplate"]
    Frame = r["Frame"]
    KeepTogether = r["KeepTogether"]
    ListFlowable = r["ListFlowable"]
    ListItem = r["ListItem"]
    NextPageTemplate = r["NextPageTemplate"]
    PageBreak = r["PageBreak"]
    PageTemplate = r["PageTemplate"]
    Paragraph = r["Paragraph"]
    Spacer = r["Spacer"]
    Table = r["Table"]
    TableStyle = r["TableStyle"]
    TableOfContents = r["TableOfContents"]

    pdf_file = REPORTS / f"{name}.pdf"
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = pdf_file.with_name(pdf_file.name + ".tmp")
    if temporary.exists():
        temporary.unlink()

    PAGE_W, PAGE_H = A4
    LPAGE_W, LPAGE_H = landscape(A4)
    left_margin = 18 * mm
    right_margin = 18 * mm
    top_margin = 20 * mm
    bottom_margin = 18 * mm
    portrait_width = PAGE_W - left_margin - right_margin
    landscape_width = LPAGE_W - left_margin - right_margin

    BLUE = colors.HexColor("#536DFE")
    NAVY = colors.HexColor("#172146")
    INK = colors.HexColor("#17202A")
    MUTED = colors.HexColor("#667085")
    LINE = colors.HexColor("#D9E0E8")
    SOFT = colors.HexColor("#F4F6FA")
    SOFT_BLUE = colors.HexColor("#EEF1FF")
    ORANGE = colors.HexColor("#B87510")
    WHITE = colors.white

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "PlaudTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=23, leading=27, textColor=INK, spaceAfter=5 * mm,
        ),
        "eyebrow": ParagraphStyle(
            "PlaudEyebrow", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=8.5, leading=10, textColor=BLUE, spaceAfter=2 * mm,
        ),
        "meta": ParagraphStyle(
            "PlaudMeta", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.7, leading=12, textColor=MUTED,
        ),
        "section": ParagraphStyle(
            "PlaudSection", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=14.5, leading=18, textColor=INK, spaceBefore=7 * mm,
            spaceAfter=3 * mm, keepWithNext=True,
        ),
        "section_notoc": ParagraphStyle(
            "PlaudSectionNoTOC", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=14.5, leading=18, textColor=INK, spaceBefore=7 * mm,
            spaceAfter=3 * mm, keepWithNext=True,
        ),
        "subsection": ParagraphStyle(
            "PlaudSubsection", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=11.5, leading=14, textColor=INK, spaceBefore=4 * mm,
            spaceAfter=2 * mm, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "PlaudBody", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.6, leading=14.2, textColor=INK, spaceAfter=2.5 * mm,
        ),
        "small": ParagraphStyle(
            "PlaudSmall", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.3, leading=11.5, textColor=MUTED, spaceAfter=1.5 * mm,
        ),
        "table_header": ParagraphStyle(
            "PlaudTableHeader", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=8.3, leading=10, textColor=WHITE,
        ),
        "table_cell": ParagraphStyle(
            "PlaudTableCell", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.2, leading=10.8, textColor=INK,
        ),
        "transcript": ParagraphStyle(
            "PlaudTranscript", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.2, leading=12.2, textColor=colors.HexColor("#303846"),
            spaceAfter=1.8 * mm,
        ),
        "toc": ParagraphStyle(
            "PlaudTOC", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.2, leading=13, textColor=INK,
        ),
    }

    class PlaudDocTemplate(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if isinstance(flowable, Paragraph) and flowable.style.name == "PlaudSection":
                text = flowable.getPlainText()
                self.notify("TOCEntry", (0, text, self.page))

    portrait_frame = Frame(
        left_margin, bottom_margin, portrait_width, PAGE_H - top_margin - bottom_margin,
        id="portrait-frame", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    landscape_frame = Frame(
        left_margin, bottom_margin, landscape_width, LPAGE_H - top_margin - bottom_margin,
        id="landscape-frame", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def page_decor(canvas, doc):
        canvas.saveState()
        width, height = canvas._pagesize
        page = canvas.getPageNumber()
        if page > 1:
            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(0.45)
            canvas.line(left_margin, height - 12 * mm, width - right_margin, height - 12 * mm)
            canvas.setFont("Helvetica-Bold", 7.7)
            canvas.setFillColor(BLUE)
            canvas.drawString(left_margin, height - 9.2 * mm, "PLAUD")
            canvas.setFont("Helvetica", 7.7)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(width - right_margin, height - 9.2 * mm, title[:75])
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(left_margin, 9 * mm, WORKSPACE.name[:55])
        canvas.drawRightString(width - right_margin, 9 * mm, f"Page {page}")
        canvas.restoreState()

    doc = PlaudDocTemplate(
        str(temporary),
        pagesize=A4,
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        title=title,
        author="PLAUD Local",
    )
    doc.addPageTemplates([
        PageTemplate(id="portrait", pagesize=A4, frames=[portrait_frame], onPage=page_decor),
        PageTemplate(id="landscape", pagesize=landscape(A4), frames=[landscape_frame], onPage=page_decor),
    ])

    story = []
    mode = _document_mode(document, transcript)

    def P(text, style="body"):
        return Paragraph(_pdf_text(text), styles[style])

    def section_heading(text, toc=True):
        return Paragraph(_pdf_text(text), styles["section" if toc else "section_notoc"])

    def bullet_list(items, small=False):
        style = styles["small" if small else "body"]
        return ListFlowable(
            [ListItem(Paragraph(_pdf_text(item), style), leftIndent=0) for item in items],
            bulletType="bullet", leftIndent=14, bulletFontName="Helvetica",
            bulletFontSize=7, bulletColor=BLUE, spaceAfter=2 * mm,
        )

    def callout(text, accent=BLUE, background=SOFT_BLUE):
        table = Table([[Paragraph(_pdf_text(text), styles["body"])]], colWidths=[portrait_width])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), background),
            ("BOX", (0, 0), (-1, -1), 0.65, accent),
            ("LEFTPADDING", (0, 0), (-1, -1), 11),
            ("RIGHTPADDING", (0, 0), (-1, -1), 11),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        return table

    def callout_list(items, accent=BLUE, background=SOFT_BLUE):
        rows = []
        for item in items:
            chunks = split_text(_text(item), max_length=900) or [""]
            for index, chunk in enumerate(chunks):
                prefix = "• " if index == 0 else "  "
                rows.append(
                    [Paragraph(_pdf_text(prefix + chunk), styles["body"])]
                )
        table = Table(
            rows,
            colWidths=[portrait_width],
            splitByRow=1,
            repeatRows=0,
        )
        commands = [
            ("BACKGROUND", (0, 0), (-1, -1), background),
            ("BOX", (0, 0), (-1, -1), 0.65, accent),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 11),
            ("RIGHTPADDING", (0, 0), (-1, -1), 11),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
        for row_index in range(len(rows) - 1):
            commands.append(
                ("LINEBELOW", (0, row_index), (0, row_index), 0.25, LINE)
            )
        table.setStyle(TableStyle(commands))
        return table

    def table_is_wide(table_data):
        columns = table_data["columns"]
        return len(columns) >= 5 or sum(len(_text(c)) for c in columns) > 65

    def table_flowables(table_data, *, already_landscape=False):
        columns = table_data["columns"]
        rows = table_data["rows"]
        wide = table_is_wide(table_data)
        available = landscape_width if wide else portrait_width
        col_widths = _column_widths(columns, rows, available)
        data = [
            [Paragraph(_pdf_text(c), styles["table_header"]) for c in columns]
        ]
        for row in rows:
            data.append([
                Paragraph(_pdf_text(cell), styles["table_cell"])
                for cell in row
            ])
        table = Table(data, colWidths=col_widths, repeatRows=1, splitByRow=1, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.35, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
        for row_index in range(1, len(data)):
            if row_index % 2 == 0:
                commands.append(("BACKGROUND", (0, row_index), (-1, row_index), SOFT))
        table.setStyle(TableStyle(commands))

        flow = []
        if wide and not already_landscape:
            flow.extend([NextPageTemplate("landscape"), PageBreak()])
        if table_data.get("title"):
            flow.append(Paragraph(_pdf_text(table_data["title"]), styles["subsection"]))
        flow.extend([table, Spacer(1, 2 * mm)])
        if wide:
            flow.extend([NextPageTemplate("portrait"), PageBreak()])
        return flow

    # En-tête / première page compacte et professionnelle.
    story.append(Paragraph("PLAUD / NOTE STRUCTURÉE", styles["eyebrow"]))
    story.append(Paragraph(_pdf_text(title), styles["title"]))
    date = datetime.now().strftime("%d/%m/%Y à %H:%M")
    duration = format_seconds(duration_seconds) if duration_seconds else "Durée non disponible"
    meta = (
        f"{date} &nbsp;&nbsp;·&nbsp;&nbsp; {duration} &nbsp;&nbsp;·&nbsp;&nbsp; "
        f"Dossier : {_pdf_text(WORKSPACE.name)} &nbsp;&nbsp;·&nbsp;&nbsp; Type : {_pdf_text(document.get('document_type', 'note'))}"
    )
    story.append(Paragraph(meta, styles["meta"]))
    story.append(Spacer(1, 6 * mm))
    story.append(section_heading("Synthèse", toc=False))
    story.append(callout(document["summary"]))

    if document["key_points"]:
        story.append(section_heading("À retenir", toc=False))
        story.append(bullet_list(document["key_points"]))

    # Sommaire seulement pour un document réellement long.
    if mode == "LONG" and len(document.get("sections", [])) >= 5:
        story.append(section_heading("Sommaire", toc=False))
        toc = TableOfContents()
        toc.levelStyles = [styles["toc"]]
        story.append(toc)

    if document["decisions"]:
        story.append(section_heading("Décisions"))
        story.append(
            callout_list(
                [
                    decision["text"]
                    + (
                        f" - {decision['context']}"
                        if decision.get("context")
                        else ""
                    )
                    for decision in document["decisions"]
                ],
                accent=BLUE,
                background=colors.HexColor("#F3F5FF"),
            )
        )

    if document["actions"]:
        story.append(section_heading("Actions"))
        actions_table = _actions_as_table(document["actions"])
        if actions_table:
            story.extend(table_flowables(actions_table))
        else:
            action_lines = []
            for action in document["actions"]:
                bits = [action["action"]]
                meta_bits = []
                if action.get("owner"):
                    meta_bits.append(f"Responsable : {action['owner']}")
                if action.get("due"):
                    meta_bits.append(f"Échéance : {action['due']}")
                if action.get("status"):
                    meta_bits.append(f"Statut : {action['status']}")
                if meta_bits:
                    bits.append(" - ".join(meta_bits))
                action_lines.append(" | ".join(bits))
            story.append(bullet_list(action_lines))

    for section in document["sections"]:
        blocks = section["blocks"]
        first_wide = bool(
            blocks
            and blocks[0].get("type") == "table"
            and table_is_wide(blocks[0])
        )
        if first_wide:
            story.extend([NextPageTemplate("landscape"), PageBreak()])
        story.append(section_heading(section["title"]))

        for index, block in enumerate(blocks):
            if block["type"] == "paragraph":
                story.append(P(block["text"]))
            elif block["type"] == "bullets":
                story.append(bullet_list(block["items"]))
            elif block["type"] == "table":
                story.extend(
                    table_flowables(
                        block,
                        already_landscape=(first_wide and index == 0),
                    )
                )

    if document["open_questions"]:
        story.append(section_heading("Questions ouvertes"))
        story.append(
            callout_list(
                document["open_questions"],
                accent=ORANGE,
                background=colors.HexColor("#FFF8EC"),
            )
        )

    # Annexe : transcription complète, séparée visuellement du compte rendu.
    story.append(PageBreak())
    story.append(Paragraph("ANNEXE", styles["eyebrow"]))
    story.append(section_heading("Transcription complète"))
    story.append(Paragraph(
        _pdf_text(f"Fichier source : {transcript_file.name}. Le texte ci-dessous correspond à la sortie Whisper conservée pour vérification."),
        styles["small"],
    ))
    for paragraph in split_text(transcript, max_length=1200):
        paragraph = paragraph.strip()
        if paragraph:
            story.append(Paragraph(_pdf_text(paragraph), styles["transcript"]))

    try:
        doc.multiBuild(story)
        temporary.replace(pdf_file)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    print(f"PDF créé :\n{pdf_file}\n")
    return pdf_file


def process_stored_audio(title, stored_audio):
    stored_audio = stored_audio.expanduser().resolve()
    if not stored_audio.is_file():
        stop(f"Audio introuvable : {stored_audio}")

    name = stored_audio.stem
    converted = convert_audio(stored_audio, name)
    transcript_file, transcript = transcribe(converted, name)
    duration_seconds = get_audio_duration_seconds(stored_audio)

    try:
        document = create_structured_note(title, transcript, name)
        structured_file = save_structured_json(name, document)
        note_file = save_markdown_note(
            title, name, stored_audio, transcript_file, transcript, document, duration_seconds
        )
        pdf_file = generate_pdf(
            title, name, stored_audio, transcript_file, transcript, document, duration_seconds
        )
    except Exception as error:
        stop(f"Le traitement intelligent de la note/PDF a échoué : {error}")

    print("\n========================================")
    print("TERMINÉ")
    print("========================================\n")
    print(f"Structure JSON :\n{structured_file}\n")
    print(f"Note Markdown :\n{note_file}\n")
    print(f"PDF final :\n{pdf_file}\n")

def main():
    check_installation()

    parser = argparse.ArgumentParser(description="PLAUD local")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("record-only", help="Enregistrer sans traiter")
    p.add_argument("title", nargs="?", default="Note vocale")

    p = commands.add_parser("import-only", help="Importer sans traiter")
    p.add_argument("path", type=Path)
    p.add_argument("title", nargs="?", default=None)

    p = commands.add_parser("process-stored", help="Traiter un audio déjà stocké")
    p.add_argument("path", type=Path)
    p.add_argument("title")

    p = commands.add_parser("record", help="Compatibilité : enregistrer puis traiter")
    p.add_argument("title", nargs="?", default="Note vocale")

    p = commands.add_parser("file", help="Compatibilité : importer puis traiter")
    p.add_argument("path", type=Path)
    p.add_argument("title", nargs="?", default=None)

    args = parser.parse_args()

    if args.command == "record-only":
        name = make_name(args.title)
        audio = record_audio(name)
        print(f"PLAUD_RECORDED={audio.name}")

    elif args.command == "import-only":
        title = args.title or args.path.stem.replace("_", " ").replace("-", " ")
        name = make_name(title)
        audio = store_imported_audio(args.path, name)
        print(f"PLAUD_IMPORTED={audio.name}")

    elif args.command == "process-stored":
        process_stored_audio(args.title, args.path)

    elif args.command == "record":
        name = make_name(args.title)
        audio = record_audio(name)
        process_stored_audio(args.title, audio)

    elif args.command == "file":
        title = args.title or args.path.stem.replace("_", " ").replace("-", " ")
        name = make_name(title)
        audio = store_imported_audio(args.path, name)
        process_stored_audio(title, audio)


if __name__ == "__main__":
    main()
