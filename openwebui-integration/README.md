# PLAUD — branche Open WebUI (`openwebui-integration`)

Outil local de transcription (Whisper) et de note structurée (Ollama / Qwen),
avec export optionnel des Markdown vers une knowledge base [Open WebUI](https://docs.openwebui.com/).

## Prérequis

- Python 3.10+
- Whisper.cpp / ffmpeg (comme pour la build Windows habituelle)
- Ollama avec un modèle Qwen autorisé
- Open WebUI (souvent Docker) accessible, ex. `http://localhost:3000`

## Configuration Open WebUI

1. Copier `.env.example` vers `.env` (jamais committer `.env`).
2. Renseigner :
   - `OPENWEBUI_URL` — URL de l’instance (défaut `http://localhost:3000`)
   - `OPENWEBUI_API_KEY` — clé API personnelle
   - `OPENWEBUI_KNOWLEDGE_ID` — identifiant de la base de connaissances

### Où générer la clé API

Dans Open WebUI : **Réglages → Compte** (section clés API).  
Côté administration, les clés API doivent être autorisées si l’option est désactivée.

### Où trouver l’ID de la knowledge base

Workspace → Knowledge → ouvrir la collection : l’ID apparaît dans l’URL  
(`/workspace/knowledge/<id>` ou équivalent) ou dans les détails de la collection.

### URL si PLAUD tourne dans Docker

- Vers Open WebUI sur l’hôte : `http://host.docker.internal:3000` (Docker Desktop)
- Vers un service Compose nommé `open-webui` : `http://open-webui:8080` (port interne du service)

Ne jamais journaliser la clé API.

## Utilisation

1. Lancer `python plaud_gui.py` depuis ce dossier.
2. Choisir le dossier cible et la langue source.
3. Cocher **Envoyer vers Open WebUI** si souhaité (désactivé par défaut).
4. Importer / traiter : Whisper + note + PDF restent produits localement même si l’export échoue.

Fichiers envoyés (uniquement) :

- copies Markdown avec en-tête YAML dans `work/openwebui_export/`
- pas les PDF

Anti-doublons : `queue/openwebui_sent.json` dans le workspace.  
Forcer un renvoi : variable d’environnement `PLAUD_OPENWEBUI_FORCE=1`.

## Vérification manuelle

```bash
# Avec un .env valide
python tools/verify_openwebui_export.py
```

## Distribution

`prepare_distribution.py` empaquete `.env.example` et **exclut** tout fichier `.env`.
