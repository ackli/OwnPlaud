# PLAUD Local

Outil **local et hors-ligne** de transcription audio et de prise de note structurée.

À partir d'un enregistrement ou d'un fichier média, PLAUD Local produit une transcription,
une note de synthèse organisée (résumé, points clés, décisions, actions) et un export
**Markdown + PDF**, le tout **sur la machine**, sans envoyer l'audio dans le cloud :

- **Transcription** via [whisper.cpp](https://github.com/ggerganov/whisper.cpp) (CUDA ou CPU),
  avec détection d'activité vocale (VAD), découpage dans les silences et traitement
  anti-hallucination / anti-boucle ;
- **Synthèse** via un LLM local servi par [Ollama](https://ollama.com) (modèles Qwen3) ;
- **Interface graphique** en [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) ;
- **Export PDF** via [ReportLab](https://www.reportlab.com/) et note Markdown.

> Projet personnel. PLAUD® est une marque de son éditeur ; ce projet n'y est pas affilié.
> Il fournit une chaîne de traitement locale autour de fichiers audio/vidéo standards.

## Chaîne de traitement

```
audio/vidéo ──▶ ffmpeg ──▶ whisper.cpp ──▶ nettoyage / anti-boucle ──▶ Ollama (Qwen3) ──▶ note structurée
   (import)    (conversion)  (transcription)    (plaud_transcript)        (synthèse)        Markdown + PDF
```

Points techniques notables :

- **Découpage par morceaux dans les silences** avec cache par morceau : une reprise réutilise
  les transcriptions déjà calculées (quelques secondes au lieu de plusieurs minutes).
- **Réglages anti-hallucination** de Whisper (`max_context=0`, suppression des tokens non
  vocaux, seuils d'entropie, VAD bornée) — voir [`docs/whisper_tuning.md`](docs/whisper_tuning.md)
  pour la démarche de mesure et les résultats.
- **Choix de la langue source** par fichier (ou détection automatique), répercuté jusque dans
  la clé de cache pour ne jamais réutiliser une transcription d'une autre langue.
- **Génération de document déterministe** à partir de l'extraction LLM (titres, tableaux,
  listes d'actions), avec repli si le modèle renvoie un JSON incomplet.

## Prérequis

Python 3.10+ et les outils externes suivants (non inclus dans le dépôt, voir ci-dessous) :

| Outil | Rôle |
|---|---|
| `whisper.cpp` (binaire `whisper-cli` + modèles `ggml`) | Transcription |
| `ffmpeg` / `ffprobe` | Conversion audio |
| `Ollama` + un modèle Qwen3 (`qwen3:1.7b`/`4b`/`8b`/`14b`) | Synthèse |

> **Volontairement absents du dépôt** (taille / dépendances tierces) : les binaires
> (`ffmpeg`, `whisper-cli`), les modèles `ggml` (plusieurs Go), le dossier `whisper.cpp/`,
> l'environnement virtuel et les artefacts de build. Voir `.gitignore`.

## Installation

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate   |   Linux/macOS : source .venv/bin/activate
pip install -r requirements.txt
```

Puis, séparément :

1. **whisper.cpp** : compiler `whisper-cli` et placer les modèles dans
   `whisper.cpp/models/` (`ggml-large-v3-turbo.bin` par défaut ; `ggml-large-v3.bin`
   active l'option « Précision maximale ») et la VAD `ggml-silero-v6.2.0.bin`.
2. **ffmpeg** : disponible dans le `PATH` ou à côté de l'application.
3. **Ollama** : installer, lancer le serveur (`ollama serve`) et récupérer un modèle,
   par exemple `ollama pull qwen3:8b`.

## Utilisation

Interface graphique :

```bash
python plaud_gui.py
```

Ligne de commande :

```bash
python plaud.py import-only  <fichier> ["Titre"]   # importer sans traiter
python plaud.py process-stored <fichier> "Titre"   # traiter un audio déjà stocké
python plaud.py record-only  ["Titre"]             # enregistrer sans traiter
python plaud.py file         <fichier> ["Titre"]   # importer puis traiter
python plaud.py record       ["Titre"]             # enregistrer puis traiter
```

## Configuration

`plaud_config.json` (à la racine) permet de surcharger le modèle Ollama et les
paramètres Whisper :

```json
{
  "ollama_model": "qwen3:8b",
  "whisper_params": {
    "max_context": 0,
    "entropy_thold": 2.4,
    "no_speech_thold": 0.6,
    "vad_threshold": 0.5,
    "vad_max_speech_duration_s": 30,
    "chunk_seconds": 600
  },
  "whisper_hallucinations": [
    "Sous-titrage Société Radio-Canada",
    "Abonnez-vous"
  ]
}
```

## Organisation du dépôt

| Chemin | Contenu |
|---|---|
| `plaud.py` | Cœur : transcription, synthèse Ollama, génération Markdown/PDF, CLI |
| `plaud_gui.py` | Interface graphique CustomTkinter |
| `plaud_transcript.py` | Chargement des JSON Whisper, détection de boucles, nettoyage |
| `plaud_launcher.py` | Petit lanceur de l'interface |
| `tests/` | Tests (transcription, analyse, diagnostic) |
| `tools/` | Outils (diagnostic de transcription) |
| `docs/` | Notes techniques (réglages Whisper) |
| `distribution-src/` | Variantes utilisées pour l'empaquetage PyInstaller |
| `*.spec`, `build_windows_release.py` | Empaquetage Windows (PyInstaller) |
| `openwebui-integration/` | Variante exportant les notes vers une base [Open WebUI](https://docs.openwebui.com/) |
| `plaud_*AVANT*/BACKUP*.py` | Anciennes versions conservées comme historique de travail |

## Développement

Le code a été écrit avec l'assistance d'outils d'IA (éditeur agentique), sous
supervision : définition des besoins, relecture, tests et validation de chaque changement.

## Licence

[MIT](LICENSE) © 2026 Dorian Cession.
