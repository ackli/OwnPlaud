#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("PLAUD_WORKSPACE", APP_ROOT)).expanduser().resolve()
RECORDINGS = WORKSPACE / "recordings"
TRANSCRIPTS = WORKSPACE / "transcripts"
NOTES = WORKSPACE / "notes"
WORK = WORKSPACE / "work"
QUEUE = WORKSPACE / "queue"
WHISPER = APP_ROOT / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe"
WHISPER_MODEL = APP_ROOT / "whisper.cpp" / "models" / "ggml-base.bin"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3:1.7b"


def stop(message):
    print(f"\nERREUR : {message}\n")
    sys.exit(1)


def prepare_workspace():
    for folder in [WORKSPACE, RECORDINGS, TRANSCRIPTS, NOTES, WORK, QUEUE]:
        folder.mkdir(parents=True, exist_ok=True)


def check_installation():
    prepare_workspace()
    if not WHISPER.exists():
        stop(f"Whisper est introuvable : {WHISPER}")
    if not WHISPER_MODEL.exists():
        stop(f"Le modÃ¨le Whisper est introuvable : {WHISPER_MODEL}")


def clean_name(text):
    text = text.lower().strip()
    replacements = {
        "Ã ": "a", "Ã¢": "a", "Ã¤": "a", "Ã©": "e", "Ã¨": "e", "Ãª": "e", "Ã«": "e",
        "Ã®": "i", "Ã¯": "i", "Ã´": "o", "Ã¶": "o", "Ã¹": "u", "Ã»": "u", "Ã¼": "u", "Ã§": "c",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "note-vocale")[:60]


def make_name(title):
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stamp}_{clean_name(title)}"


def record_audio(name):
    if shutil.which("pw-record") is None:
        stop("La commande pw-record est introuvable.")

    output = RECORDINGS / f"{name}.wav"
    print("\n========================================")
    print("ENREGISTREMENT")
    print("========================================\n")
    print("L'enregistrement a commencÃ©.")
    print("ArrÃªte-le depuis l'interface PLAUD.\n")

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
        print("\nArrÃªt de l'enregistrement...")
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        process.wait()

    if not output.exists() or output.stat().st_size < 1000:
        stop("Le fichier audio est introuvable ou semble vide.")

    print(f"\nAudio enregistrÃ© :\n{output}\n")
    return output


def store_imported_audio(source, name):
    source = source.expanduser().resolve()
    if not source.is_file():
        stop(f"Fichier introuvable : {source}")
    extension = source.suffix or ".audio"
    stored = RECORDINGS / f"{name}{extension}"
    shutil.copy2(source, stored)
    print(f"\nAudio importÃ© :\n{stored}\n")
    return stored


def convert_audio(source, name):
    if shutil.which("ffmpeg") is None:
        stop("ffmpeg est introuvable.")
    source = source.expanduser().resolve()
    if not source.is_file():
        stop(f"Fichier introuvable : {source}")
    converted = WORK / f"{name}.wav"
    print("\nConversion du fichier audio...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(converted),
        ],
        check=True,
    )
    if not converted.exists():
        stop("La conversion audio a Ã©chouÃ©.")
    return converted


def transcribe(audio, name):
    output_prefix = TRANSCRIPTS / name
    print("\n========================================")
    print("TRANSCRIPTION WHISPER")
    print("========================================\n")
    subprocess.run(
        [
            str(WHISPER), "-m", str(WHISPER_MODEL), "-f", str(audio), "-l", "fr",
            "-t", "4", "-ng", "-nt", "--output-txt", "--output-file", str(output_prefix),
        ],
        check=True,
    )
    transcript_file = Path(str(output_prefix) + ".txt")
    if not transcript_file.exists():
        stop("La transcription n'a pas Ã©tÃ© crÃ©Ã©e.")
    transcript = transcript_file.read_text(encoding="utf-8").strip()
    if not transcript:
        stop("La transcription est vide.")
    print("\nTranscription terminÃ©e.")
    return transcript_file, transcript


def split_text(text, max_length=6000):
    parts = []
    text = text.strip()
    while len(text) > max_length:
        cut = text.rfind(" ", 0, max_length)
        if cut < max_length // 2:
            cut = max_length
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def ask_ollama(prompt):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Tu es un assistant spÃ©cialisÃ© dans la prise de notes. Tu rÃ©ponds en franÃ§ais. "
                    "Tu travailles uniquement Ã  partir du texte fourni. Tu n'inventes jamais d'information. "
                    "Tu produis du Markdown clair et organisÃ©."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1800) as response:
        result = json.loads(response.read().decode("utf-8"))
    text = result.get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("Ollama a retournÃ© une rÃ©ponse vide.")
    return text


def create_summary(transcript):
    parts = split_text(transcript)
    if len(parts) == 1:
        return ask_ollama(f"""
Voici une transcription audio.

Transforme-la en une note claire avec exactement cette structure :

## RÃ©sumÃ©

## Points clÃ©s

## Actions / tÃ¢ches

## Notes dÃ©taillÃ©es

N'invente aucune information. S'il n'y a aucune action explicite, Ã©cris : Aucune action explicite.

TRANSCRIPTION :

{transcript}
""")

    summaries = []
    for number, part in enumerate(parts, start=1):
        print(f"\nAnalyse partie {number}/{len(parts)}...")
        summaries.append(ask_ollama(f"""
Voici la partie {number} sur {len(parts)} d'une transcription longue.
Extrais uniquement les sujets, faits importants, dÃ©cisions, tÃ¢ches, noms, dates et nombres importants.
N'invente rien.

TRANSCRIPTION :

{part}
"""))

    combined = "\n\n".join(
        f"PARTIE {index}\n{summary}"
        for index, summary in enumerate(summaries, start=1)
    )

    return ask_ollama(f"""
Voici les rÃ©sumÃ©s des diffÃ©rentes parties d'un mÃªme enregistrement.
CrÃ©e une note finale avec exactement cette structure :

## RÃ©sumÃ©

## Points clÃ©s

## Actions / tÃ¢ches

## Notes dÃ©taillÃ©es

Supprime les doublons et n'invente rien.

RÃ‰SUMÃ‰S :

{combined}
""")


def save_note(title, name, audio, transcript_file, transcript, summary):
    note_file = NOTES / f"{name}.md"
    date = datetime.now().strftime("%d/%m/%Y Ã  %H:%M")
    content = f"""# {title}

**Date :** {date}

**Audio :** `{audio.name}`

**Transcription :** `{transcript_file.name}`

---

{summary}

---

## Transcription complÃ¨te

{transcript}
"""
    note_file.write_text(content, encoding="utf-8")
    return note_file


def process_stored_audio(title, stored_audio):
    stored_audio = stored_audio.expanduser().resolve()
    if not stored_audio.is_file():
        stop(f"Audio introuvable : {stored_audio}")

    name = stored_audio.stem
    converted = convert_audio(stored_audio, name)
    transcript_file, transcript = transcribe(converted, name)

    print("\n========================================")
    print("CRÃ‰ATION DE LA NOTE")
    print("========================================\n")

    try:
        summary = create_summary(transcript)
    except Exception as error:
        print(f"La crÃ©ation du rÃ©sumÃ© a Ã©chouÃ© : {error}")
        summary = """## RÃ©sumÃ©

RÃ©sumÃ© non gÃ©nÃ©rÃ©.

## Points clÃ©s

Non gÃ©nÃ©rÃ©s.

## Actions / tÃ¢ches

Non gÃ©nÃ©rÃ©es.

## Notes dÃ©taillÃ©es

Non gÃ©nÃ©rÃ©es.
"""

    note_file = save_note(
        title,
        name,
        stored_audio,
        transcript_file,
        transcript,
        summary,
    )

    print("\n========================================")
    print("TERMINÃ‰")
    print("========================================\n")
    print(f"Note finale :\n{note_file}\n")


def main():
    check_installation()

    parser = argparse.ArgumentParser(description="PLAUD local")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("record-only", help="Enregistrer sans traiter")
    p.add_argument("title", nargs="?", default="Note vocale")

    p = commands.add_parser("import-only", help="Importer sans traiter")
    p.add_argument("path", type=Path)
    p.add_argument("title", nargs="?", default=None)

    p = commands.add_parser("process-stored", help="Traiter un audio dÃ©jÃ  stockÃ©")
    p.add_argument("path", type=Path)
    p.add_argument("title")

    p = commands.add_parser("record", help="CompatibilitÃ© : enregistrer puis traiter")
    p.add_argument("title", nargs="?", default="Note vocale")

    p = commands.add_parser("file", help="CompatibilitÃ© : importer puis traiter")
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

