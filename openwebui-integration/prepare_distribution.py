from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
DIST_SRC = ROOT / "distribution-src"

DIST_SRC.mkdir(parents=True, exist_ok=True)

gui_source = ROOT / "plaud_gui.py"
engine_source = ROOT / "plaud.py"
# Source unique : plaud_transcript.py à la racine du dépôt (pas d'import parent).
transcript_source = PARENT / "plaud_transcript.py"

if not gui_source.exists():
    raise RuntimeError("plaud_gui.py introuvable")

if not engine_source.exists():
    raise RuntimeError("plaud.py introuvable")

if not transcript_source.exists():
    raise RuntimeError(
        f"plaud_transcript.py introuvable à la racine : {transcript_source}"
    )

# Recopie la source canonique dans openwebui-integration/ avant dist.
shutil.copy2(transcript_source, ROOT / "plaud_transcript.py")


# ============================================================
# RACINE APPLICATION PYINSTALLER
# ============================================================

old_root = 'APP_ROOT = Path(__file__).resolve().parent'

new_root = '''APP_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)'''


# ============================================================
# GUI
# ============================================================

gui = gui_source.read_text(encoding="utf-8")

if old_root not in gui:
    raise RuntimeError(
        "Impossible de trouver APP_ROOT dans plaud_gui.py."
    )

gui = gui.replace(old_root, new_root, 1)


# ------------------------------------------------------------
# VERSION COMPILEE :
# PLAUD.exe lance PLAUD-Engine.exe
#
# VERSION DEVELOPPEMENT :
# Python lance toujours plaud.py
# ------------------------------------------------------------

old_command = '''        command = [
            sys.executable,
            "-u",
            str(PLAUD_PY),
        ]
        command.extend(arguments)'''

new_command = '''        if getattr(sys, "frozen", False):
            command = [
                str(APP_ROOT / "PLAUD-Engine.exe"),
            ]
        else:
            command = [
                sys.executable,
                "-u",
                str(PLAUD_PY),
            ]

        command.extend(arguments)'''

if old_command not in gui:
    raise RuntimeError(
        "Impossible de trouver le bloc de lancement "
        "de plaud.py dans plaud_gui.py."
    )

gui = gui.replace(old_command, new_command, 1)


(DIST_SRC / "plaud_gui_dist.py").write_text(
    gui,
    encoding="utf-8"
)


# ============================================================
# MOTEUR
# ============================================================

engine = engine_source.read_text(encoding="utf-8")

(DIST_SRC / "plaud_dist.py").write_text(
    engine,
    encoding="utf-8"
)

shutil.copy2(
    ROOT / "plaud_transcript.py",
    DIST_SRC / "plaud_transcript.py",
)

openwebui_source = ROOT / "plaud_openwebui.py"
if not openwebui_source.exists():
    raise RuntimeError("plaud_openwebui.py introuvable")
shutil.copy2(openwebui_source, DIST_SRC / "plaud_openwebui.py")

# Sécurité : jamais empaqueter un .env réel ; seulement l'exemple vide.
env_file = ROOT / ".env"
if env_file.exists():
    print("AVERTISSEMENT : .env présent à la racine — exclu de la distribution.")
dist_env = DIST_SRC / ".env"
if dist_env.exists():
    dist_env.unlink()

env_example = ROOT / ".env.example"
if env_example.exists():
    shutil.copy2(env_example, DIST_SRC / ".env.example")
else:
    print("AVERTISSEMENT : .env.example introuvable")


# ============================================================
# ICONE
# ============================================================

icon = ROOT / "PLAUD.ico"

if not icon.exists():
    raise RuntimeError("PLAUD.ico introuvable")

shutil.copy2(
    icon,
    DIST_SRC / "PLAUD.ico"
)


# ============================================================
# FIN
# ============================================================

print()
print("==========================================")
print("SOURCES DISTRIBUTION PRETES")
print("==========================================")
print()
print(DIST_SRC / "plaud_gui_dist.py")
print(DIST_SRC / "plaud_dist.py")
print(DIST_SRC / "plaud_transcript.py")
print(DIST_SRC / "plaud_openwebui.py")
print(DIST_SRC / "PLAUD.ico")
if (DIST_SRC / ".env.example").exists():
    print(DIST_SRC / ".env.example")
print()
print("Modeles disponibles :")
print(" - qwen3:1.7b")
print(" - qwen3:4b")
print(" - qwen3:8b")
print(" - qwen3:14b")
print()