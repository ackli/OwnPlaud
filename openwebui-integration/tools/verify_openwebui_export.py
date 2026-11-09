#!/usr/bin/env python3
"""Envoie un Markdown factice vers Open WebUI pour vérifier la knowledge base."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import plaud_openwebui as owui  # noqa: E402


def main():
    config, warning = owui.load_openwebui_config(ROOT)
    if warning:
        print(f"ERREUR : {warning}")
        print("Renseigne .env à partir de .env.example puis réessaie.")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        path = owui.write_kb_markdown(
            Path(directory) / f"plaud_verify_{stamp}.md",
            title=f"PLAUD verify {stamp}",
            source="verify_openwebui_export.py",
            language="fr",
            body=(
                "# Vérification PLAUD → Open WebUI\n\n"
                f"Fichier de test généré le {stamp}.\n"
            ),
        )
        print(f"Upload de {path.name} vers {config['url']} …")
        # Ne jamais afficher la clé.
        file_id = owui.push_markdown_file(
            config,
            workspace,
            path,
            label=path.name,
        )
        print(f"OK — file_id={file_id}")
        print(
            "Vérifie dans Open WebUI → Workspace → Knowledge "
            f"(collection {config['knowledge_id']})."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
