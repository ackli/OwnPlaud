#!/usr/bin/env python3

import argparse
import html
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import wave
import sys
import urllib.request
from datetime import datetime
from itertools import islice
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("PLAUD_WORKSPACE", APP_ROOT)).expanduser().resolve()
RECORDINGS = WORKSPACE / "recordings"
TRANSCRIPTS = WORKSPACE / "transcripts"
NOTES = WORKSPACE / "notes"
WORK = WORKSPACE / "work"
QUEUE = WORKSPACE / "queue"
REPORTS = WORKSPACE / "reports"
STRUCTURED = WORK / "structured"
WHISPER_CANDIDATES = [
    APP_ROOT / "whisper.cpp" / "build" / "bin" / "whisper-cli",
    APP_ROOT / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe",
    APP_ROOT / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe",
]
WHISPER = next(
    (candidate for candidate in WHISPER_CANDIDATES if candidate.exists()),
    WHISPER_CANDIDATES[0],
)
WHISPER_MODEL = APP_ROOT / "whisper.cpp" / "models" / "ggml-large-v3-turbo.bin"
VAD_MODEL = APP_ROOT / "whisper.cpp" / "ggml-silero-v6.2.0.bin"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3:14b"


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
        stop(f"Le modèle Whisper est introuvable : {WHISPER_MODEL}")
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

        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)

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
        stop("La conversion audio a échoué.")
    return converted


def transcribe(audio, name):
    output_prefix = TRANSCRIPTS / name
    print("\n========================================")
    print("TRANSCRIPTION WHISPER")
    print("========================================\n")
    subprocess.run(
        [
            str(WHISPER), "-m", str(WHISPER_MODEL), "-f", str(audio), "-l", "fr",
            "-t", "8", "-nt",
            "--vad", "--vad-model", str(VAD_MODEL),
            "--output-txt", "--output-file", str(output_prefix),
        ],
        check=True,
    )
    transcript_file = Path(str(output_prefix) + ".txt")
    if not transcript_file.exists():
        stop("La transcription n'a pas été créée.")
    transcript = transcript_file.read_text(encoding="utf-8").strip()
    if not transcript:
        stop("La transcription est vide.")
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


def ask_ollama(prompt, *, json_mode=False, temperature=0.15):
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
        "options": {"temperature": temperature, "num_ctx": 8192},
    }
    if json_mode:
        payload["format"] = "json"

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1800) as response:
        result = json.loads(response.read().decode("utf-8"))

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


def ask_ollama_json(prompt):
    """Retourne un dictionnaire JSON et tente une réparation unique si nécessaire."""
    raw = ask_ollama(prompt, json_mode=True)
    try:
        data = _extract_json_object(raw)
    except Exception:
        repaired = ask_ollama(
            "Répare le JSON ci-dessous. Retourne uniquement le JSON valide, sans explication.\n\n" + raw,
            json_mode=True,
            temperature=0.0,
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
    return normalize_extraction(ask_ollama_json(prompt))


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
{json.dumps(group, ensure_ascii=False)}
"""
    return normalize_extraction(ask_ollama_json(prompt))


def reduce_extractions(extractions):
    current = list(extractions)
    round_number = 1
    while len(current) > 1:
        print(f"\nConsolidation niveau {round_number} : {len(current)} bloc(s)...")
        merged = []
        for batch_number, group in enumerate(batched(current, 4), start=1):
            if len(group) == 1:
                merged.append(group[0])
            else:
                merged.append(merge_extractions(group, round_number, batch_number))
        current = merged
        round_number += 1
    return current[0] if current else normalize_extraction({})


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


def create_structured_document(title, extraction):
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
{json.dumps(extraction, ensure_ascii=False)}
"""
    return normalize_document(ask_ollama_json(prompt), title)


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


def create_structured_note(title, transcript):
    parts = split_text(transcript)
    print("\n========================================")
    print("ANALYSE DE LA TRANSCRIPTION")
    print("========================================\n")

    extractions = []
    total = len(parts)
    for number, part in enumerate(parts, start=1):
        print(f"Analyse partie {number}/{total}...")
        extractions.append(extract_chunk(part, number, total))

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
        story.append(callout("\n".join(d["text"] + (f" - {d['context']}" if d.get("context") else "") for d in document["decisions"]), accent=BLUE, background=colors.HexColor("#F3F5FF")))

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
        story.append(callout("\n".join(document["open_questions"]), accent=ORANGE, background=colors.HexColor("#FFF8EC")))

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
        document = create_structured_note(title, transcript)
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
