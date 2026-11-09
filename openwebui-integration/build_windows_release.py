#!/usr/bin/env python3
"""Construit une distribution Windows autonome sans fichiers source Python."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYINSTALLER_DIST = ROOT / "pyinstaller-dist"
PYINSTALLER_BUILD = ROOT / "pyinstaller-build"
RELEASE_ROOT = ROOT / "release"
PACKAGE_NAME = "PLAUD-Local-Windows-x64-v9"
PACKAGE_DIR = RELEASE_ROOT / PACKAGE_NAME
ZIP_PATH = RELEASE_ROOT / f"{PACKAGE_NAME}.zip"

WHISPER_FILES = (
    "whisper-cli.exe",
    "whisper.dll",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-cpu.dll",
)


def run(command):
    print("\n>", subprocess.list2cmdline([str(part) for part in command]))
    subprocess.run([str(part) for part in command], cwd=ROOT, check=True)


def require(path, label):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"{label} introuvable : {path}")
    return path


def copy_file(source, destination):
    source = require(source, "Fichier requis")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    print(f"  + {destination.relative_to(PACKAGE_DIR)}")


def resolve_executable(name):
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"{name} est introuvable dans PATH.")
    return Path(executable).resolve()


def build_executables():
    run([sys.executable, ROOT / "prepare_distribution.py"])
    for spec in ("PLAUD-Engine.spec", "PLAUD.spec"):
        run(
            [
                sys.executable,
                "-m", "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath", PYINSTALLER_DIST,
                "--workpath", PYINSTALLER_BUILD,
                ROOT / spec,
            ]
        )


def copy_whisper_runtime():
    cuda_dir = ROOT / "whisper.cpp" / "build" / "bin" / "Release"
    cpu_dir = ROOT / "whisper.cpp" / "build-cpu" / "bin" / "Release"
    package_cuda = PACKAGE_DIR / "whisper.cpp" / "build" / "bin" / "Release"
    package_cpu = PACKAGE_DIR / "whisper.cpp" / "build-cpu" / "bin" / "Release"

    for filename in WHISPER_FILES:
        copy_file(cpu_dir / filename, package_cpu / filename)
        copy_file(cuda_dir / filename, package_cuda / filename)
    copy_file(cuda_dir / "ggml-cuda.dll", package_cuda / "ggml-cuda.dll")

    model_dir = PACKAGE_DIR / "whisper.cpp" / "models"
    copy_file(
        ROOT / "whisper.cpp" / "models" / "ggml-large-v3-turbo.bin",
        model_dir / "ggml-large-v3-turbo.bin",
    )
    copy_file(
        ROOT / "whisper.cpp" / "ggml-silero-v6.2.0.bin",
        PACKAGE_DIR / "whisper.cpp" / "ggml-silero-v6.2.0.bin",
    )

    large_v3 = ROOT / "whisper.cpp" / "models" / "ggml-large-v3.bin"
    if large_v3.is_file():
        copy_file(large_v3, model_dir / large_v3.name)


def copy_cuda_runtime():
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs" / "Ollama" / "lib" / "ollama" / "cuda_v13",
        Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib",
    ]
    runtime = next(
        (
            path for path in candidates
            if (path / "cublas64_13.dll").is_file()
            and (path / "cublasLt64_13.dll").is_file()
        ),
        None,
    )
    if runtime is None:
        print("  ! Runtime CUDA 13 absent : le repli CPU restera disponible.")
        return
    copy_file(runtime / "cublas64_13.dll", PACKAGE_DIR / "cublas64_13.dll")
    copy_file(runtime / "cublasLt64_13.dll", PACKAGE_DIR / "cublasLt64_13.dll")


def write_runtime_files():
    config = {"ollama_model": "qwen3:8b"}
    (PACKAGE_DIR / "plaud_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        commit = "inconnu"
    (PACKAGE_DIR / "VERSION.txt").write_text(
        f"PLAUD Local\nCommit : {commit}\nConstruction : "
        f"{datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )

    readme = """PLAUD LOCAL — WINDOWS 10/11 x64
==================================

LANCEMENT
1. Décompressez entièrement l'archive.
2. Double-cliquez sur PLAUD.exe.

PRÉREQUIS POUR LA GÉNÉRATION DES NOTES
- Ollama doit être installé et lancé : https://ollama.com/
- Dans un terminal, exécutez une fois : ollama pull qwen3:8b

TRANSCRIPTION
- Le modèle Whisper Turbo est inclus.
- Une carte NVIDIA récente est utilisée automatiquement quand elle est disponible.
- Sans runtime CUDA compatible, PLAUD utilise automatiquement le moteur CPU,
  qui est beaucoup plus lent.
- Le mode « Précision maximale (large-v3) » reste désactivé tant que le fichier
  whisper.cpp\\models\\ggml-large-v3.bin n'a pas été ajouté.

DONNÉES
Les enregistrements, transcriptions, notes et rapports sont créés dans le
dossier de travail choisi dans l'application.

Cette distribution ne contient aucun fichier source Python.
"""
    (PACKAGE_DIR / "LISEZ-MOI.txt").write_text(readme, encoding="utf-8-sig")


def write_checksums():
    targets = [
        PACKAGE_DIR / "PLAUD.exe",
        PACKAGE_DIR / "PLAUD-Engine.exe",
        PACKAGE_DIR / "ffmpeg.exe",
        PACKAGE_DIR / "ffprobe.exe",
        PACKAGE_DIR / "whisper.cpp" / "models" / "ggml-large-v3-turbo.bin",
    ]
    lines = []
    for path in targets:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {path.relative_to(PACKAGE_DIR)}")
    (PACKAGE_DIR / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="ascii",
    )


def create_zip():
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    print(f"\nCréation de {ZIP_PATH.name}...")
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(RELEASE_ROOT))


def stage_release():
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    PACKAGE_DIR.mkdir(parents=True)
    copy_file(PYINSTALLER_DIST / "PLAUD.exe", PACKAGE_DIR / "PLAUD.exe")
    copy_file(PYINSTALLER_DIST / "PLAUD-Engine.exe", PACKAGE_DIR / "PLAUD-Engine.exe")
    copy_file(resolve_executable("ffmpeg"), PACKAGE_DIR / "ffmpeg.exe")
    copy_file(resolve_executable("ffprobe"), PACKAGE_DIR / "ffprobe.exe")
    copy_whisper_runtime()
    copy_cuda_runtime()
    write_runtime_files()
    write_checksums()
    create_zip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Réutiliser les exécutables PyInstaller existants.",
    )
    args = parser.parse_args()

    if not args.skip_build:
        build_executables()
    stage_release()
    print("\nDistribution prête :")
    print(PACKAGE_DIR)
    print(ZIP_PATH)
    print(f"Taille archive : {ZIP_PATH.stat().st_size / (1024 ** 3):.2f} Gio")


if __name__ == "__main__":
    main()
