from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
DIST_SRC = ROOT / "distribution-src"

DIST_SRC.mkdir(parents=True, exist_ok=True)

gui_source = ROOT / "plaud_gui.py"
engine_source = ROOT / "plaud.py"
transcript_source = ROOT / "plaud_transcript.py"

if not gui_source.exists():
    raise RuntimeError("plaud_gui.py introuvable")

if not engine_source.exists():
    raise RuntimeError("plaud.py introuvable")

if not transcript_source.exists():
    raise RuntimeError("plaud_transcript.py introuvable")


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
    transcript_source,
    DIST_SRC / "plaud_transcript.py",
)


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
print(DIST_SRC / "PLAUD.ico")
print()
print("Modeles disponibles :")
print(" - qwen3:1.7b")
print(" - qwen3:4b")
print(" - qwen3:8b")
print(" - qwen3:14b")
print()