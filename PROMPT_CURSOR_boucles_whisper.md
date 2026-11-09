# Prompt Cursor — Corriger les boucles de répétition de PLAUD local sur les longs fichiers

> À coller tel quel dans Cursor (mode Agent), à la racine du dossier `plaud-local`.

---

## 1. Contexte du projet

PLAUD local est un transcripteur + générateur de comptes rendus 100 % local, sous **Windows**, en Python.

**Chaîne de traitement actuelle :**

```
GUI (plaud_gui.py, customtkinter)
  └─ lance en sous-processus : python -u plaud.py process-stored <audio> <titre>
        1. convert_audio()   → ffmpeg -i <source> -ar 16000 -ac 1 -c:a pcm_s16le work/<nom>.wav
        2. transcribe()      → whisper.cpp (whisper-cli.exe, build CUDA) → transcripts/<nom>.txt
        3. create_structured_note() → découpe le texte en blocs de 6000 caractères
                                     → Ollama qwen3 (extraction JSON par bloc, puis fusion 4 par 4)
        4. Sorties : work/structured/<nom>.json, notes/<nom>.md, reports/<nom>.pdf (reportlab)
```

**Fichiers qui comptent :**

| Fichier | Rôle |
|---|---|
| `plaud.py` | Moteur (CLI) : conversion, transcription, analyse LLM, Markdown, PDF. **La fonction à corriger en priorité est `transcribe()` (~ligne 190).** |
| `plaud_gui.py` | Interface. Lance `plaud.py` via `subprocess.Popen` et lit stdout ligne à ligne (`start_process`, `read_process_output`). |
| `distribution-src/plaud_dist.py` / `plaud_gui_dist.py` | **Copies divergentes** de `plaud.py` / `plaud_gui.py` pour la version packagée PyInstaller (`PLAUD.spec`, `PLAUD-Engine.spec`, `prepare_distribution.py`). Toute correction doit y être reportée (ou mieux : factorisée). |
| `whisper.cpp/` | Clone de whisper.cpp (commit `c4ac0012`, 29/08/2026). Binaire utilisé : `whisper.cpp/build/bin/Release/whisper-cli.exe` (CUDA activé). |
| `whisper.cpp/models/ggml-large-v3-turbo.bin` | Modèle Whisper utilisé. |
| `whisper.cpp/ggml-silero-v6.2.0.bin` | Modèle VAD Silero. |
| `plaud_*backup*.py`, `*_AVANT_*.py`, `plaud_gui_tk_backup.py` | Anciennes sauvegardes. **Ne pas les modifier, ne pas s'en servir comme référence.** (`plaud_gui.py` est identique à `plaud_gui_tk_backup.py` et `plaud_gui_AVANT_LQG.py` ; `plaud.py` est identique à `plaud_BACKUP_AVANT_QT.py`.) |

Le dossier racine **n'est pas un dépôt git**. Avant toute modification : `git init`, un `.gitignore` (exclure `.venv/`, `build*/`, `dist*/`, `whisper.cpp/`, `recordings/`, `transcripts/`, `work/`, `notes/`, `reports/`, `*.wav`), puis un commit initial, pour pouvoir comparer avant/après.

**Appel Whisper actuel (plaud.py, `transcribe()`) :**

```python
[str(WHISPER), "-m", str(WHISPER_MODEL), "-f", str(audio), "-l", "fr",
 "-t", "8", "-nt",
 "--vad", "--vad-model", str(VAD_MODEL),
 "--output-txt", "--output-file", str(output_prefix)]
```

Le fichier entier (parfois plus de 2 h) est envoyé **en un seul appel**, avec tous les autres paramètres par défaut.

---

## 2. Symptôme à corriger

Sur certains fichiers, surtout des **vidéos**, et surtout quand le rush **dépasse environ 2 h** (le moment exact de la casse n'a pas été repéré), la transcription **se met à boucler** : un mot ou une courte expression se répète des dizaines ou des centaines de fois sans raison apparente.

Exemples constatés : **« Radio Canada »** et **« le CRM »** répétés en boucle.

Conséquences : transcription inutilisable sur la zone touchée, analyse Qwen polluée (blocs de 6000 caractères remplis de répétitions), PDF et note faussés.

---

## 3. Diagnostic (établi en lisant le code de ce repo et de whisper.cpp)

### Cause n°1, la plus probable : le texte précédent sert de consigne à la fenêtre suivante, sur tout le fichier

- Dans `whisper.cpp/examples/cli/cli.cpp`, `max_context = -1` par défaut, donc `n_max_text_ctx` reste à 16384. Dans `src/whisper.cpp`, `max_prompt_ctx = min(n_max_text_ctx, n_text_ctx/2)`, soit environ **224 tokens du texte précédent réinjectés comme prompt à chaque fenêtre de 30 s**.
- Conséquence : dès qu'une fenêtre produit une répétition ou une hallucination, cette répétition devient le prompt de la fenêtre suivante. **La boucle se propage** (effet de contagion). Sur 2 h, cela fait plus de 240 fenêtres enchaînées sans jamais repartir de zéro, donc la probabilité d'un décrochage augmente avec la durée.
- Le prompt n'est abandonné que si la température de repli atteint 0,5 (`WHISPER_HISTORY_CONDITIONING_TEMP_CUTOFF`), c'est-à-dire après plusieurs échecs de décodage consécutifs.
- La détection de boucle intégrée (`entropy_thold = 2.4`, vérifiée seulement si `result_len > 32`, et `seek_delta` dans `whisper_full_with_state`) ne repère pas une courte expression répétée **d'une fenêtre à l'autre** : chaque fenêtre, prise seule, paraît correcte.

### Cause n°2 : « Radio Canada » est très probablement une hallucination connue de Whisper en français

Whisper a été entraîné sur beaucoup de sous-titres francophones. Sur du silence, de la musique, des jingles, des applaudissements ou du bruit, il produit des phrases types comme **« Sous-titrage Société Radio-Canada »**, « Sous-titres réalisés par la communauté d'Amara.org », « Merci d'avoir regardé cette vidéo » ou « Sous-titrage ST' 501 ». Ce texte n'a sans doute **jamais été prononcé** : il vient des passages sans parole de la vidéo, puis la cause n°1 le fait boucler.

« Le CRM » est plus probablement un vrai terme du contenu, qui s'est ensuite emballé à cause de la cause n°1.

### Cause n°3 : la VAD recolle toute la parole bout à bout

Avec `--vad`, whisper.cpp (`whisper_vad()` dans `src/whisper.cpp`) concatène tous les segments de parole avec seulement 0,1 s d'écart entre eux. Il n'y a donc plus aucune vraie pause qui pourrait « remettre à zéro » le décodage. Par ailleurs, avec les paramètres par défaut (`vad_threshold 0.5`, `max_speech_duration_s = FLT_MAX`), la musique et les voix off de vidéos peuvent passer pour de la parole.

### Cause n°4 : modèle turbo, recherche en faisceau, aucun garde-fou en post-traitement

- `ggml-large-v3-turbo` (4 couches de décodeur) est réputé plus sujet aux répétitions et hallucinations que `large-v3` ou `large-v2`. **À confirmer par un test A/B, pas à supposer.**
- Aucun contrôle après la transcription : rien ne détecte ni ne nettoie les répétitions avant de passer le texte à Qwen.
- La sortie se fait en `.txt` seul, sans horodatage (`-nt`, `--output-txt`), donc **impossible de savoir à quel moment la boucle démarre**. C'est pour cela que le moment exact de la casse n'a pas pu être repéré.

### Cause n°5 : spécificités des fichiers vidéo (à vérifier)

`convert_audio()` fait `ffmpeg -i source -ar 16000 -ac 1` sans choisir de piste. Sur une vidéo :

- ffmpeg prend la piste audio par défaut, qui n'est pas forcément la bonne (enregistrements OBS ou Teams avec plusieurs pistes, piste de commentaire, piste muette) ;
- un stéréo dont un canal est en opposition de phase peut s'annuler au mixage mono et donner un son quasi muet, donc des hallucinations ;
- `-vn`, `-sn` et `-dn` ne sont pas précisés.

Rien n'est journalisé sur les flux (ffprobe), donc ce cas ne peut pas être écarté aujourd'hui.

---

## 4. Travail demandé (par ordre de priorité)

### Étape 0 : pouvoir diagnostiquer (à faire en premier)

1. Créer `tools/diagnose_transcript.py` qui, pour un fichier audio ou vidéo donné :
   - liste les flux avec `ffprobe -show_streams -of json` (nombre de pistes audio, canaux, langue, durée) ;
   - lance Whisper avec les **paramètres actuels**, en sortie JSON horodatée (`-ojf`) et SRT (`-osrt`) ;
   - détecte les boucles (voir l'algorithme de l'étape 3) et affiche **l'horodatage de début et de fin de chaque boucle**, le texte répété et le nombre de répétitions ;
   - relance ensuite avec les **nouveaux paramètres** (étapes 1 et 2) et affiche un comparatif avant/après.
2. Dans `transcribe()`, toujours produire en plus du `.txt` un `.json` horodaté (`-oj` ou `-ojf`) et un `.srt`, et écrire toute la sortie stderr de whisper-cli dans `work/<nom>.whisper.log`.

### Étape 1 : paramètres Whisper anti-boucle (correctif rapide)

Modifier `transcribe()`. Les valeurs doivent être des **constantes regroupées en tête de fichier** (dictionnaire `WHISPER_PARAMS`, surchargeable par `plaud_config.json`, que `plaud_dist.py` lit déjà pour `ollama_model`) :

| Paramètre | Valeur proposée | Pourquoi |
|---|---|---|
| `-mc` / `--max-context` | **`0`** | Coupe la réinjection du texte précédent. **C'est le correctif principal** contre la contagion des boucles. |
| `-sns` / `--suppress-nst` | activé | Supprime les tokens non vocaux. |
| `-bs 5 -bo 5` | explicites | Les valeurs par défaut, mais écrites noir sur blanc. |
| `-et` / `--entropy-thold` | `2.4` par défaut, **tester `2.8`** | Seuil plus haut : les fenêtres répétitives sont rejetées plus facilement (repli en température). |
| `-nth` / `--no-speech-thold` | `0.6`, tester `0.5` | |
| `-vt` / `--vad-threshold` | tester `0.5` puis `0.6` | Laisser passer moins de musique et de jingles. |
| `-vmsd` / `--vad-max-speech-duration-s` | `30` | Découpe les segments de parole trop longs. |
| `-vp` / `--vad-speech-pad-ms` | tester `100` à `200` | Évite de couper les débuts et fins de mots. |
| `-pp` | activé | Progression lisible par la GUI. |

Vérifier chaque option contre `whisper-cli.exe --help` du binaire compilé présent, pas seulement contre la documentation en ligne.

### Étape 2 : transcrire par morceaux (correctif de fond)

Ne plus envoyer un fichier de 2 h ou plus en un seul appel.

1. Découper le WAV 16 kHz en morceaux d'environ **10 minutes**, **coupés dans un silence** : chercher le silence le plus proche de chaque cible de 10 min, avec `ffmpeg -af silencedetect=noise=-35dB:d=0.5` ou avec `whisper-vad-speech-segments.exe`, déjà compilé dans `whisper.cpp/build/bin/Release/`. Les morceaux vont dans `work/<nom>_chunks/`.
2. Transcrire chaque morceau par un appel whisper-cli séparé (le contexte repart de zéro à chaque morceau), en JSON horodaté.
3. Fusionner en recalant les horodatages avec le décalage de chaque morceau. Produire `transcripts/<nom>.txt`, `.json` et `.srt`.
4. **Reprise sur incident** : si un morceau a déjà son JSON, ne pas le refaire.
5. Afficher une progression `Transcription morceau i/N` sur stdout, pour la GUI.
6. Si un morceau est détecté en boucle (étape 3), **le retranscrire une fois** avec des paramètres plus stricts (`-mc 0 -et 2.8 -tp 0.2`, `-vt 0.6`). S'il boucle encore, garder la meilleure version et marquer la zone `[passage incertain hh:mm:ss–hh:mm:ss]`.

Garder un paramètre `CHUNK_SECONDS` configurable, et ne découper que si la durée dépasse ce seuil.

### Étape 3 : garde-fou en post-traitement

Nouvelle fonction `clean_transcript_segments(segments)`, appliquée sur les segments JSON **avant** l'écriture du `.txt` et **avant** l'envoi à Qwen :

1. **Répétitions dans un segment** : un n-gramme de 1 à 8 mots répété au moins 3 fois d'affilée (comparaison insensible à la casse et à la ponctuation) est ramené à une seule occurrence.
2. **Répétitions entre segments** : au moins 3 segments consécutifs identiques ou presque (normalisation, puis ratio `difflib` supérieur à 0,9) sont marqués comme boucle et réduits à une occurrence. La zone est journalisée avec ses horodatages.
3. **Ratio de compression**, comme l'implémentation OpenAI : `len(texte) / len(zlib.compress(texte))` supérieur à 2,4 signale un segment suspect.
4. **Liste noire d'hallucinations connues en français**, supprimées **seulement si elles forment tout le segment** : « Sous-titrage Société Radio-Canada », « Sous-titres réalisés par la communauté d'Amara.org », « Sous-titrage ST' 501 », « Merci d'avoir regardé cette vidéo », « Abonnez-vous », etc. La liste est configurable.
5. Écrire un rapport `work/<nom>.loops.json` (zones touchées, texte d'origine, action faite), et afficher sur stdout un résumé du type `⚠ 3 boucles corrigées (01:42:10–01:44:55, …)`.

**Interdiction de supprimer du contenu légitime** : une expression répétée 2 fois reste en l'état. On journalise toujours ce qu'on retire, rien n'est supprimé en silence.

### Étape 4 : entrées vidéo robustes

Dans `convert_audio()` :

- lancer `ffprobe` avant la conversion et journaliser les pistes audio trouvées ;
- ajouter `-vn -sn -dn` et un choix de piste explicite (`-map 0:a:<index>`). Par défaut, première piste audio ; s'il y a plusieurs pistes, afficher un avertissement sur stdout (et idéalement laisser le choix dans la GUI plus tard) ;
- détecter un fichier converti presque muet (`volumedetect` : `mean_volume` inférieur à -50 dB) et avertir ;
- ne **pas** ajouter de normalisation agressive (`dynaudnorm` amplifie le bruit dans les silences et favorise les hallucinations). Tout au plus un `loudnorm` optionnel, désactivé par défaut.

Dans `plaud_gui.py`, harmoniser les extensions acceptées (filtre du sélecteur de fichier dans `import_audio`, ensemble `supported` dans `fetch_icloud`, ensemble `extensions` dans `refresh_stats`) et ajouter `.mkv .mov .webm .avi .m4v .wma`.

### Étape 5 : bugs annexes repérés pendant l'analyse (à corriger aussi)

1. **Risque de blocage de la GUI sur Windows** : `start_process()` ouvre le sous-processus avec `text=True` **sans `encoding`**, donc stdout est décodé en cp1252. Whisper écrit de l'UTF-8 ; certains octets (0x81, 0x8D, 0x8F, 0x90, 0x9D, qu'on trouve par exemple dans « Á », « Í », « Ï », « č ») provoquent une `UnicodeDecodeError` qui tue le thread `read_process_output`. Le `finally` appelle alors `process.wait()` alors que plus personne ne vide le pipe : whisper se bloque dès que le tampon est plein, et la GUI reste « En traitement » indéfiniment. **Correctif** : `encoding="utf-8", errors="replace"` dans `Popen`, plus `PYTHONUTF8=1` et `PYTHONIOENCODING=utf-8` dans `make_environment()`, et `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` au début de `plaud.py`.
2. **Journal GUI sans limite** : sur 2 h, `add_log()` insère des milliers de lignes dans le widget texte et Tk ralentit. Limiter à environ 5000 lignes, et/ou lancer whisper avec `-np` en ne remontant que la progression.
3. **Contexte Ollama trop court pour la fusion** : `merge_extractions()` envoie jusqu'à 4 extractions JSON avec `num_ctx: 8192`. Sur un long enregistrement, cela peut dépasser la fenêtre, et Ollama **tronque sans prévenir** le début du prompt. Estimer la taille (environ 1 token pour 3,5 caractères en français), réduire le lot à 2 ou augmenter `num_ctx` si besoin, et journaliser un avertissement.
4. **Code en double** : `plaud.py` et `distribution-src/plaud_dist.py` ne diffèrent que sur `APP_ROOT` (mode frozen), le `PATH` Windows et `load_ollama_model()`. Idéalement, faire de `plaud.py` la seule source (gérer le mode frozen dans `plaud.py`) et faire copier le fichier par `prepare_distribution.py`. Sinon, **reporter toutes les corrections dans les deux fichiers**. Même chose pour `plaud_gui.py` et `plaud_gui_dist.py`.
5. `PLAUD.bat` suppose que l'application est dans `%USERPROFILE%\plaud-local`. La rendre relative : `cd /d "%~dp0"`.

---

## 5. Contraintes

- Windows 10/11, Python du `.venv` existant, whisper.cpp **déjà compilé** : **ne pas recompiler whisper.cpp et ne pas modifier son code C++**. Tout se règle par les options de la CLI et en Python.
- Aucune nouvelle dépendance lourde. Bibliothèque standard (`zlib`, `difflib`, `json`, `re`, `subprocess`) plus ffmpeg et ffprobe, déjà requis.
- Garder l'interface actuelle de `plaud.py` (sous-commandes `record-only`, `import-only`, `process-stored`, `record`, `file`) et les marqueurs stdout `PLAUD_RECORDED=` et `PLAUD_IMPORTED=` lus par la GUI.
- Garder les noms et emplacements des sorties (`transcripts/<nom>.txt`, `notes/`, `reports/`, `work/structured/`). Les nouveaux fichiers (`.json`, `.srt`, `.loops.json`, `.whisper.log`, `_chunks/`) sont des ajouts.
- Messages utilisateur en français, dans le même style que l'existant.
- Petits commits, un par étape, avec un message clair.

---

## 6. Plan de test et critères d'acceptation

**Fichiers de test :**

- `test.wav` (racine du projet) et `recordings/2026-08-30_12-38-06_test-iphone.m4a` : non-régression sur des fichiers courts ;
- **[À FOURNIR : chemin de la vidéo de plus de 2 h qui a bouclé sur « Radio Canada » / « le CRM »]** ;
- si possible, une vidéo avec de la musique ou un générique, et une vidéo à plusieurs pistes audio.

**Critères :**

1. `tools/diagnose_transcript.py` sur la vidéo problématique, **avant correction**, affiche l'horodatage exact du début de chaque boucle.
2. **Après correction** : aucun n-gramme de 1 à 8 mots répété plus de 3 fois d'affilée, et aucune série de 3 segments consécutifs identiques dans la transcription finale.
3. « Radio-Canada » n'apparaît plus, sauf là où c'est réellement prononcé (vérifier à l'oreille aux horodatages).
4. Chaque tranche de 10 minutes de l'enregistrement produit du texte, sans zone vide inexpliquée.
5. Sur `test.wav` et le m4a, la transcription reste au moins aussi bonne qu'avant (comparer avec un diff).
6. Temps de traitement total au plus 20 % plus long qu'avant, hors retranscriptions de morceaux en boucle.
7. La GUI affiche la progression par morceau et ne se bloque plus sur des caractères non-cp1252.
8. Test A/B, **à titre informatif seulement**, entre `large-v3-turbo` et `large-v3` (si l'utilisateur télécharge `ggml-large-v3.bin`) sur la même vidéo. Consigner le résultat dans `docs/whisper_tuning.md` sans changer le modèle par défaut sans validation.

Commence par l'étape 0 et montre-moi l'horodatage des boucles avant de toucher aux paramètres.
