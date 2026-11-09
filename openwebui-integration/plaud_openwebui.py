#!/usr/bin/env python3
"""Export optionnel de Markdown PLAUD vers une knowledge base Open WebUI."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HTTP_TIMEOUT_SECONDS = 30
PROCESS_STATUS_MAX_SECONDS = 120
ADD_MAX_ATTEMPTS = 5
ADD_BACKOFF_SECONDS = (2, 4, 8, 16, 32)

ENV_KEYS = ("OPENWEBUI_URL", "OPENWEBUI_API_KEY", "OPENWEBUI_KNOWLEDGE_ID")


class OpenWebUIError(RuntimeError):
    """Erreur d'export Open WebUI (jamais fatale pour le pipeline PLAUD)."""


def load_dotenv(path):
    """Charge un fichier .env simple (KEY=VALUE), sans dépendance externe."""
    values = {}
    path = Path(path)
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def find_env_file(app_root):
    candidates = [
        Path(app_root) / ".env",
        Path.cwd() / ".env",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_openwebui_config(app_root):
    """
    Retourne (config_dict | None, warning_message | None).
    config_dict a les clés url, api_key, knowledge_id.
    """
    env_path = find_env_file(app_root)
    file_values = load_dotenv(env_path) if env_path else {}

    def pick(name, default=""):
        return (
            os.environ.get(name, "").strip()
            or file_values.get(name, "").strip()
            or default
        )

    url = pick("OPENWEBUI_URL", "http://localhost:3000").rstrip("/")
    api_key = pick("OPENWEBUI_API_KEY")
    knowledge_id = pick("OPENWEBUI_KNOWLEDGE_ID")

    missing = []
    if not api_key:
        missing.append("OPENWEBUI_API_KEY")
    if not knowledge_id:
        missing.append("OPENWEBUI_KNOWLEDGE_ID")
    if not url:
        missing.append("OPENWEBUI_URL")

    if missing:
        location = str(env_path) if env_path else "(aucun fichier .env trouvé)"
        return None, (
            "Open WebUI : configuration incomplète. "
            f"Manquant : {', '.join(missing)}. "
            f"Fichier .env : {location}. "
            "Renseigne .env à partir de .env.example."
        )

    return {
        "url": url,
        "api_key": api_key,
        "knowledge_id": knowledge_id,
        "env_path": str(env_path) if env_path else "",
    }, None


def export_requested():
    return os.environ.get("PLAUD_OPENWEBUI_EXPORT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def force_resend():
    return os.environ.get("PLAUD_OPENWEBUI_FORCE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def yaml_frontmatter(title, date_iso, source, language):
    def quote(value):
        text = str(value or "").replace('"', '\\"')
        return f'"{text}"'

    return (
        "---\n"
        f"title: {quote(title)}\n"
        f"date: {quote(date_iso)}\n"
        f"source: {quote(source)}\n"
        f"language: {quote(language)}\n"
        "---\n\n"
    )


def write_kb_markdown(path, title, source, language, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    date_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    content = yaml_frontmatter(title, date_iso, source, language) + (body or "").rstrip() + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def markdown_body_for_fingerprint(text):
    """Retourne le corps Markdown sans frontmatter YAML (ex. date d'export)."""
    text = text or ""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    body = text[end + len("\n---") :]
    if body.startswith("\n"):
        body = body[1:]
    return body


def content_fingerprint(path):
    """Empreinte du corps seul — le frontmatter (date) ne doit pas forcer un renvoi."""
    text = Path(path).read_text(encoding="utf-8")
    body = markdown_body_for_fingerprint(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def tracking_path(workspace):
    return Path(workspace) / "queue" / "openwebui_sent.json"


def load_tracking(workspace):
    path = tracking_path(workspace)
    if not path.is_file():
        return {"entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": {}}
    if not isinstance(data, dict):
        return {"entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"entries": entries}


def save_tracking(workspace, data):
    path = tracking_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def already_sent(workspace, fingerprint, knowledge_id):
    data = load_tracking(workspace)
    entry = data["entries"].get(fingerprint)
    if not isinstance(entry, dict):
        return None
    if entry.get("knowledge_id") != knowledge_id:
        return None
    return entry.get("file_id")


def remember_sent(workspace, fingerprint, *, file_id, knowledge_id, path):
    data = load_tracking(workspace)
    data["entries"][fingerprint] = {
        "file_id": file_id,
        "knowledge_id": knowledge_id,
        "path": str(path),
        "sent_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    save_tracking(workspace, data)


def _auth_headers(api_key, extra=None):
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra:
        headers.update(extra)
    return headers


def _request(method, url, *, headers, data=None, timeout=HTTP_TIMEOUT_SECONDS):
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = body.decode(charset, errors="replace")
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        # Ne jamais inclure les en-têtes (Bearer) dans le message.
        raise OpenWebUIError(
            f"HTTP {error.code} sur {method} {url}: {detail[:500]}"
        ) from None
    except urllib.error.URLError as error:
        raise OpenWebUIError(f"Connexion Open WebUI impossible ({url}): {error.reason}") from None


def upload_file(config, path):
    path = Path(path)
    boundary = f"----plaud{hashlib.sha1(str(path).encode()).hexdigest()[:16]}"
    file_bytes = path.read_bytes()
    filename = path.name
    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/markdown; charset=utf-8\r\n\r\n"
    ).encode("utf-8")
    epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = preamble + file_bytes + epilogue
    headers = _auth_headers(
        config["api_key"],
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    payload = _request(
        "POST",
        f"{config['url']}/api/v1/files/",
        headers=headers,
        data=body,
    )
    file_id = payload.get("id") if isinstance(payload, dict) else None
    if not file_id:
        raise OpenWebUIError(
            "Réponse upload Open WebUI sans id de fichier."
        )
    return str(file_id)


def wait_until_processed(config, file_id):
    deadline = time.monotonic() + PROCESS_STATUS_MAX_SECONDS
    url = f"{config['url']}/api/v1/files/{file_id}/process/status"
    headers = _auth_headers(config["api_key"])
    last_status = "unknown"
    while time.monotonic() < deadline:
        payload = _request("GET", url, headers=headers)
        status = ""
        if isinstance(payload, dict):
            status = str(
                payload.get("status")
                or payload.get("data", {}).get("status")
                or ""
            ).casefold()
        last_status = status or last_status
        if status in {"completed", "complete", "done", "success"}:
            return
        if status in {"failed", "error", "cancelled", "canceled"}:
            detail = ""
            if isinstance(payload, dict):
                detail = str(
                    payload.get("error")
                    or payload.get("message")
                    or payload.get("detail")
                    or ""
                )
            raise OpenWebUIError(
                f"Traitement Open WebUI échoué pour {file_id}"
                + (f" : {detail}" if detail else f" (status={status})")
            )
        time.sleep(2)
    raise OpenWebUIError(
        f"Délai dépassé ({PROCESS_STATUS_MAX_SECONDS}s) en attendant "
        f"le traitement Open WebUI (dernier statut : {last_status})."
    )


def add_file_to_knowledge(config, file_id):
    url = f"{config['url']}/api/v1/knowledge/{config['knowledge_id']}/file/add"
    headers = _auth_headers(
        config["api_key"],
        {"Content-Type": "application/json"},
    )
    body = json.dumps({"file_id": file_id}).encode("utf-8")
    last_error = None
    for attempt, delay in enumerate(ADD_BACKOFF_SECONDS[:ADD_MAX_ATTEMPTS], start=1):
        try:
            return _request("POST", url, headers=headers, data=body)
        except OpenWebUIError as error:
            last_error = error
            message = str(error).casefold()
            retryable = (
                "empty content" in message
                or "still processing" in message
                or "http 400" in message
                or "http 409" in message
                or "http 429" in message
                or "http 500" in message
                or "http 502" in message
                or "http 503" in message
            )
            if not retryable or attempt >= ADD_MAX_ATTEMPTS:
                raise
            print(
                f"Open WebUI : ajout knowledge reporté "
                f"(essai {attempt}/{ADD_MAX_ATTEMPTS}), nouvelle tentative dans {delay}s…"
            )
            time.sleep(delay)
    raise last_error or OpenWebUIError("Échec d'ajout à la knowledge base.")


def push_markdown_file(config, workspace, path, *, label=""):
    path = Path(path)
    fingerprint = content_fingerprint(path)
    existing = already_sent(workspace, fingerprint, config["knowledge_id"])
    if existing and not force_resend():
        print(
            f"Open WebUI : {label or path.name} déjà envoyé "
            f"(file_id connu, contenu identique) — ignoré."
        )
        return existing

    print(f"Open WebUI : envoi de {label or path.name}…")
    file_id = upload_file(config, path)
    wait_until_processed(config, file_id)
    add_file_to_knowledge(config, file_id)
    remember_sent(
        workspace,
        fingerprint,
        file_id=file_id,
        knowledge_id=config["knowledge_id"],
        path=path,
    )
    print(f"Open WebUI : {label or path.name} ajouté à la knowledge base.")
    return file_id


def export_files(app_root, workspace, markdown_paths):
    """
    Exporte une liste de Path Markdown.
    Ne lève pas d'exception fatale : retourne (ok: bool, message: str).
    """
    if not export_requested():
        return True, ""

    config, warning = load_openwebui_config(app_root)
    if warning:
                print(f"\nATTENTION : {warning}\n")
                return False, warning

    try:
        for path in markdown_paths:
            path = Path(path)
            if not path.is_file():
                raise OpenWebUIError(f"Fichier Markdown introuvable : {path}")
            push_markdown_file(config, workspace, path, label=path.name)
        return True, "Export Open WebUI terminé."
    except OpenWebUIError as error:
        message = f"Open WebUI : échec d'export — {error}"
        print(f"\nATTENTION : {message}\n")
        return False, message
    except Exception as error:
        message = f"Open WebUI : erreur inattendue — {error}"
        print(f"\nATTENTION : {message}\n")
        return False, message
