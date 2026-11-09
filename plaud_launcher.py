import os
import subprocess
from pathlib import Path

APP_DIR = Path.home() / "plaud-local"

PYTHON = APP_DIR / ".venv" / "Scripts" / "python.exe"
GUI = APP_DIR / "plaud_gui.py"


def main():
    if not APP_DIR.exists():
        raise RuntimeError(f"Dossier PLAUD introuvable : {APP_DIR}")

    if not PYTHON.exists():
        raise RuntimeError(f"Python PLAUD introuvable : {PYTHON}")

    if not GUI.exists():
        raise RuntimeError(f"Interface PLAUD introuvable : {GUI}")

    subprocess.Popen(
        [str(PYTHON), str(GUI)],
        cwd=str(APP_DIR),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )


if __name__ == "__main__":
    main()