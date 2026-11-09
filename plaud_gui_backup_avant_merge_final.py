#!/usr/bin/env python3

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox


APP_ROOT = Path(__file__).resolve().parent
PLAUD_PY = APP_ROOT / "plaud.py"

CONFIG_DIR = Path.home() / ".config" / "plaud-local"
CONFIG_FILE = CONFIG_DIR / "config.json"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLOR_BG = "#090D13"
COLOR_SIDEBAR = "#0D121A"
COLOR_CARD = "#121923"
COLOR_CARD_ALT = "#151D28"
COLOR_CARD_INNER = "#0D131C"
COLOR_BORDER = "#222C39"
COLOR_BORDER_LIGHT = "#2C3847"
COLOR_TEXT = "#F3F6FA"
COLOR_TEXT_SOFT = "#C2CBD6"
COLOR_TEXT_MUTED = "#7D8998"
COLOR_BLUE = "#536DFE"
COLOR_BLUE_HOVER = "#657CFF"
COLOR_BLUE_DARK = "#172146"
COLOR_BLUE_SOFT = "#202B59"
COLOR_RED = "#E95A67"
COLOR_GREEN = "#42C98A"
COLOR_ORANGE = "#E5A94C"
COLOR_INPUT = "#0A1018"


def open_with_system(path):
    try:
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def clean_duration(seconds):
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def audio_duration(path):
    try:
        if shutil.which("ffprobe"):
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                value = result.stdout.strip()
                if value:
                    return float(value)
    except Exception:
        pass

    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                frames = audio.getnframes()
                rate = audio.getframerate()
                if rate:
                    return frames / rate
        except Exception:
            pass

    return 0


class NewProjectDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.result = None
        self.title("Nouveau projet PLAUD")
        self.geometry("620x430")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG)
        self.transient(parent)
        self.grab_set()

        documents = Path.home() / "Documents"
        default_location = documents if documents.exists() else Path.home()

        self.name_var = ctk.StringVar()
        self.location_var = ctk.StringVar(value=str(default_location))

        self.create_interface()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.after(150, self.name_entry.focus_set)
        self.wait_window()

    def create_interface(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=32, pady=30)

        ctk.CTkLabel(
            container,
            text="Créer un nouveau projet",
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            container,
            text=(
                "Choisis un nom et un emplacement. "
                "PLAUD créera automatiquement toute la structure."
            ),
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=13),
            justify="left",
            wraplength=540,
        ).pack(anchor="w", pady=(6, 24))

        card = ctk.CTkFrame(
            container,
            fg_color=COLOR_CARD,
            corner_radius=20,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        card.pack(fill="x")

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=24, pady=24)

        ctk.CTkLabel(
            form,
            text="Nom du projet",
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        self.name_entry = ctk.CTkEntry(
            form,
            textvariable=self.name_var,
            height=46,
            corner_radius=12,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER_LIGHT,
            border_width=1,
            text_color=COLOR_TEXT,
            placeholder_text="Ex : Cours Histoire",
        )
        self.name_entry.pack(fill="x", pady=(8, 20))

        ctk.CTkLabel(
            form,
            text="Emplacement",
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        location_row = ctk.CTkFrame(form, fg_color="transparent")
        location_row.pack(fill="x", pady=(8, 8))

        self.location_entry = ctk.CTkEntry(
            location_row,
            textvariable=self.location_var,
            height=46,
            corner_radius=12,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER_LIGHT,
            border_width=1,
            text_color=COLOR_TEXT,
        )
        self.location_entry.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            location_row,
            text="Parcourir",
            width=110,
            height=46,
            corner_radius=12,
            command=self.browse,
            fg_color=COLOR_CARD_ALT,
            hover_color=COLOR_BORDER_LIGHT,
            border_width=1,
            border_color=COLOR_BORDER_LIGHT,
            text_color=COLOR_TEXT,
        ).pack(side="left", padx=(10, 0))

        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(fill="x", pady=(22, 0))

        ctk.CTkButton(
            buttons,
            text="Annuler",
            command=self.cancel,
            width=120,
            height=44,
            corner_radius=12,
            fg_color=COLOR_CARD_ALT,
            hover_color=COLOR_BORDER_LIGHT,
            border_width=1,
            border_color=COLOR_BORDER_LIGHT,
            text_color=COLOR_TEXT,
        ).pack(side="right")

        ctk.CTkButton(
            buttons,
            text="+  Créer le projet",
            command=self.create_project,
            width=170,
            height=44,
            corner_radius=12,
            fg_color=COLOR_BLUE,
            hover_color=COLOR_BLUE_HOVER,
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="right", padx=(0, 10))

        self.bind("<Return>", lambda event: self.create_project())

    def browse(self):
        path = filedialog.askdirectory(
            parent=self,
            title="Choisir où créer le projet",
            initialdir=self.location_var.get(),
        )
        if path:
            self.location_var.set(path)

    def create_project(self):
        name = self.name_var.get().strip()
        location = self.location_var.get().strip()

        if not name:
            messagebox.showwarning("Nom manquant", "Donne un nom à ton projet.", parent=self)
            return

        if name in {".", ".."} or "/" in name or "\\" in name:
            messagebox.showwarning(
                "Nom incorrect",
                "Utilise un nom simple.\n\nExemple : Cours Histoire",
                parent=self,
            )
            return

        if not location:
            messagebox.showwarning("Emplacement manquant", "Choisis un emplacement.", parent=self)
            return

        parent_path = Path(location).expanduser()

        try:
            parent_path.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            messagebox.showerror(
                "Erreur",
                f"Impossible d'utiliser cet emplacement.\n\n{error}",
                parent=self,
            )
            return

        project = parent_path / name

        if project.exists():
            messagebox.showwarning(
                "Projet existant",
                "Un dossier portant déjà ce nom existe.\n\nUtilise « Ouvrir un projet ».",
                parent=self,
            )
            return

        try:
            project.mkdir(parents=True, exist_ok=False)
        except Exception as error:
            messagebox.showerror("Erreur", str(error), parent=self)
            return

        self.result = project.resolve()
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class PlaudApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("PLAUD")
        self.geometry("1180x820")
        self.minsize(900, 650)
        self.configure(fg_color=COLOR_BG)

        self.workspace = None
        self.process = None
        self.recording_active = False
        self.processing_active = False
        self.recording_started_at = None
        self.output_queue = queue.Queue()
        self.recent_workspaces = []

        self.project_name_var = ctk.StringVar(value="Aucun projet sélectionné")
        self.project_path_var = ctk.StringVar(value="Choisis ou crée un projet pour commencer.")
        self.title_var = ctk.StringVar()
        self.timer_var = ctk.StringVar(value="00:00:00")
        self.status_var = ctk.StringVar(value="Prêt")
        self.recordings_count_var = ctk.StringVar(value="0")
        self.notes_count_var = ctk.StringVar(value="0")
        self.duration_var = ctk.StringVar(value="00:00:00")

        self.create_layout()
        self.load_config()
        self.refresh_project()

        self.after(100, self.check_queue)
        self.after(500, self.update_timer)

        self.protocol("WM_DELETE_WINDOW", self.close_application)

        self.bind_all("<Control-n>", lambda event: self.create_workspace())
        self.bind_all("<Control-o>", lambda event: self.open_workspace())
        self.bind_all("<Control-i>", lambda event: self.import_audio())
        self.bind_all("<Control-r>", lambda event: self.toggle_recording())

    def create_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.create_sidebar()

        self.main_scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=COLOR_BG,
            corner_radius=0,
            scrollbar_button_color=COLOR_BORDER_LIGHT,
            scrollbar_button_hover_color=COLOR_BLUE,
        )
        self.main_scroll.grid(row=0, column=1, sticky="nsew")
        self.main_scroll.grid_columnconfigure(0, weight=1)

        self.create_dashboard()

    def create_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self,
            width=210,
            corner_radius=0,
            fg_color=COLOR_SIDEBAR,
            border_width=0,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        ctk.CTkLabel(
            self.sidebar,
            text="✣  P L A U D",
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(28, 30))

        self.sidebar_button("⌂   Accueil", lambda: self.scroll_to(0.0), active=True)
        self.sidebar_button("◉   Enregistrer", lambda: self.scroll_to(0.27))
        self.sidebar_button("⇧   Importer", lambda: self.scroll_to(0.45))
        self.sidebar_button("□   Projets", self.open_workspace)
        self.sidebar_button("▤   Notes", lambda: self.scroll_to(0.58))
        self.sidebar_button("⚙   Paramètres", lambda: self.scroll_to(1.0))

        spacer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        spacer.pack(fill="both", expand=True)

        ctk.CTkLabel(
            self.sidebar,
            text="PROJET ACTUEL",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=22)

        ctk.CTkLabel(
            self.sidebar,
            textvariable=self.project_name_var,
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="left",
            wraplength=165,
        ).pack(anchor="w", padx=22, pady=(8, 4))

        ctk.CTkLabel(
            self.sidebar,
            textvariable=self.project_path_var,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            justify="left",
            wraplength=165,
        ).pack(anchor="w", padx=22, pady=(0, 28))

        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent", height=50)
        footer.pack(fill="x", padx=22, pady=(0, 22))

        ctk.CTkLabel(
            footer,
            text="PLAUD Local\n100 % local",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
            justify="left",
        ).pack(anchor="w")

    def sidebar_button(self, text, command, active=False):
        fg = COLOR_CARD_ALT if active else "transparent"

        button = ctk.CTkButton(
            self.sidebar,
            text=text,
            command=command,
            height=42,
            corner_radius=9,
            anchor="w",
            fg_color=fg,
            hover_color=COLOR_CARD_ALT,
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        button.pack(fill="x", padx=14, pady=3)

    def create_dashboard(self):
        content = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        content.grid(row=0, column=0, sticky="ew", padx=28, pady=(26, 30))
        content.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_area = ctk.CTkFrame(header, fg_color="transparent")
        title_area.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            title_area,
            text="Bienvenue dans PLAUD",
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=28, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_area,
            text="Enregistre, transcris et organise tes idées en toute simplicité.",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", pady=(4, 0))

        self.status_badge = ctk.CTkLabel(
            header,
            textvariable=self.status_var,
            width=100,
            height=42,
            corner_radius=21,
            fg_color=COLOR_CARD_ALT,
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.status_badge.grid(row=0, column=1, sticky="e")

        stats = ctk.CTkFrame(content, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(26, 18))
        stats.grid_columnconfigure((0, 1, 2), weight=1)

        self.create_stat_card(stats, 0, "◉", "Enregistrements", self.recordings_count_var, "Ce projet")
        self.create_stat_card(stats, 1, "▤", "Notes générées", self.notes_count_var, "Ce projet")
        self.create_stat_card(stats, 2, "◷", "Durée totale", self.duration_var, "Ce projet")

        project = self.create_card(content)
        project.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        project.grid_columnconfigure(0, weight=1)

        project_left = ctk.CTkFrame(project, fg_color="transparent")
        project_left.grid(row=0, column=0, sticky="nsew", padx=24, pady=24)

        self.section_title(project_left, "□", "Projet actuel")

        ctk.CTkLabel(
            project_left,
            textvariable=self.project_name_var,
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
            justify="left",
        ).pack(anchor="w", pady=(18, 5))

        ctk.CTkLabel(
            project_left,
            textvariable=self.project_path_var,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=12),
            justify="left",
            wraplength=460,
        ).pack(anchor="w")

        project_buttons = ctk.CTkFrame(project, fg_color="transparent")
        project_buttons.grid(row=0, column=1, sticky="e", padx=(10, 24), pady=24)

        self.new_project_button = ctk.CTkButton(
            project_buttons,
            text="+   Nouveau projet",
            command=self.create_workspace,
            width=190,
            height=44,
            corner_radius=12,
            fg_color=COLOR_BLUE,
            hover_color=COLOR_BLUE_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.new_project_button.pack(pady=(0, 9))

        self.open_project_button = self.secondary_button(
            project_buttons,
            "□   Ouvrir un projet",
            self.open_workspace,
            width=190,
        )
        self.open_project_button.pack(pady=(0, 9))

        self.open_folder_button = self.secondary_button(
            project_buttons,
            "□   Ouvrir le dossier",
            self.open_workspace_folder,
            width=190,
        )
        self.open_folder_button.pack()

        record_card = self.create_card(content)
        record_card.grid(row=3, column=0, sticky="ew", pady=(0, 18))

        record_inner = ctk.CTkFrame(record_card, fg_color="transparent")
        record_inner.pack(fill="x", padx=24, pady=24)

        self.section_title(record_inner, "◉", "Nouvel enregistrement")

        ctk.CTkLabel(
            record_inner,
            text="Titre de l'enregistrement",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(20, 7))

        self.title_entry = ctk.CTkEntry(
            record_inner,
            textvariable=self.title_var,
            height=48,
            corner_radius=11,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER_LIGHT,
            border_width=1,
            placeholder_text="Ex : Cours de philosophie",
            text_color=COLOR_TEXT,
        )
        self.title_entry.pack(fill="x")

        recorder = ctk.CTkFrame(
            record_inner,
            fg_color=COLOR_CARD_INNER,
            corner_radius=18,
        )
        recorder.pack(fill="x", pady=(18, 0))
        recorder.grid_columnconfigure(1, weight=1)

        self.record_circle = ctk.CTkButton(
            recorder,
            text="●",
            width=72,
            height=72,
            corner_radius=36,
            fg_color=COLOR_BLUE_DARK,
            hover_color=COLOR_BLUE_SOFT,
            border_width=2,
            border_color=COLOR_BLUE,
            text_color="#7590FF",
            font=ctk.CTkFont(size=26, weight="bold"),
            command=self.toggle_recording,
        )
        self.record_circle.grid(row=0, column=0, rowspan=2, padx=(18, 20), pady=18)

        self.record_main_label = ctk.CTkLabel(
            recorder,
            text="Démarrer l'enregistrement",
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.record_main_label.grid(row=0, column=1, sticky="sw", pady=(20, 0))

        self.record_sub_label = ctk.CTkLabel(
            recorder,
            text="Clique pour commencer",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=11),
        )
        self.record_sub_label.grid(row=1, column=1, sticky="nw", pady=(1, 20))

        timer_frame = ctk.CTkFrame(
            recorder,
            width=230,
            height=70,
            corner_radius=12,
            fg_color=COLOR_CARD,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        timer_frame.grid(row=0, column=2, rowspan=2, padx=(10, 18), pady=18, sticky="e")
        timer_frame.grid_propagate(False)

        ctk.CTkLabel(
            timer_frame,
            textvariable=self.timer_var,
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(pady=(10, 0))

        ctk.CTkLabel(
            timer_frame,
            text="Durée",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack()

        import_card = self.create_card(content)
        import_card.grid(row=4, column=0, sticky="ew", pady=(0, 18))
        import_card.grid_columnconfigure(0, weight=1)

        import_text = ctk.CTkFrame(import_card, fg_color="transparent")
        import_text.grid(row=0, column=0, sticky="w", padx=24, pady=22)

        self.section_title(import_text, "⇧", "Importer un fichier audio")

        ctk.CTkLabel(
            import_text,
            text="Importe un MP3, M4A, WAV, FLAC, OGG ou OPUS pour le transcrire et créer une note.",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            wraplength=560,
            justify="left",
        ).pack(anchor="w", pady=(7, 0))

        self.import_button = self.secondary_button(
            import_card,
            "Choisir un fichier   ⇧",
            self.import_audio,
            width=180,
        )
        self.import_button.grid(row=0, column=1, sticky="e", padx=24, pady=22)

        notes_card = self.create_card(content)
        notes_card.grid(row=5, column=0, sticky="ew", pady=(0, 18))

        notes_header = ctk.CTkFrame(notes_card, fg_color="transparent")
        notes_header.pack(fill="x", padx=24, pady=(22, 10))

        self.section_title(notes_header, "▤", "Dernières notes")

        self.notes_container = ctk.CTkFrame(
            notes_card,
            fg_color=COLOR_CARD_INNER,
            corner_radius=14,
        )
        self.notes_container.pack(fill="x", padx=18, pady=(0, 18))

        lower = ctk.CTkFrame(content, fg_color="transparent")
        lower.grid(row=6, column=0, sticky="ew", pady=(0, 18))
        lower.grid_columnconfigure(0, weight=1)
        lower.grid_columnconfigure(1, weight=1)

        activity_card = self.create_card(lower)
        activity_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        activity_header = ctk.CTkFrame(activity_card, fg_color="transparent")
        activity_header.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(
            activity_header,
            text=">_   Activité",
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            activity_header,
            text="Vider",
            width=65,
            height=30,
            corner_radius=15,
            fg_color=COLOR_CARD_ALT,
            hover_color=COLOR_BORDER_LIGHT,
            command=self.clear_log,
            text_color=COLOR_TEXT_MUTED,
        ).pack(side="right")

        self.log = ctk.CTkTextbox(
            activity_card,
            height=240,
            corner_radius=12,
            fg_color=COLOR_INPUT,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(family="Monospace", size=11),
        )
        self.log.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.log.insert(
            "end",
            (
                "PLAUD est prêt.\n\n"
                "Choisis ou crée un projet, puis démarre un enregistrement "
                "ou importe un fichier audio.\n\n"
                "Les événements apparaîtront ici."
            ),
        )
        self.log.configure(state="disabled")

        info_column = ctk.CTkFrame(lower, fg_color="transparent")
        info_column.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        about = self.create_card(info_column)
        about.pack(fill="x", pady=(0, 14))

        about_inner = ctk.CTkFrame(about, fg_color="transparent")
        about_inner.pack(fill="x", padx=20, pady=20)

        self.section_title(about_inner, "⌂", "À propos de PLAUD")

        self.info_line(about_inner, "100 % local & privé", "Tes données restent sur ton ordinateur.")
        self.info_line(about_inner, "Transcription avec Whisper", "Le modèle fonctionne localement.")
        self.info_line(about_inner, "Résumé avec Ollama / Qwen", "Tes notes sont structurées en local.")

        shortcuts = self.create_card(info_column)
        shortcuts.pack(fill="x")

        shortcuts_inner = ctk.CTkFrame(shortcuts, fg_color="transparent")
        shortcuts_inner.pack(fill="x", padx=20, pady=20)

        self.section_title(shortcuts_inner, "◷", "Raccourcis utiles")

        self.shortcut_line(shortcuts_inner, "Nouveau projet", "Ctrl + N")
        self.shortcut_line(shortcuts_inner, "Ouvrir un projet", "Ctrl + O")
        self.shortcut_line(shortcuts_inner, "Enregistrer / arrêter", "Ctrl + R")
        self.shortcut_line(shortcuts_inner, "Importer un fichier", "Ctrl + I")

        footer = ctk.CTkFrame(
            content,
            fg_color=COLOR_CARD,
            corner_radius=12,
            border_width=1,
            border_color=COLOR_BORDER,
            height=46,
        )
        footer.grid(row=7, column=0, sticky="ew", pady=(0, 12))
        footer.grid_propagate(False)
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            footer,
            textvariable=self.project_name_var,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=0, padx=16)

        ctk.CTkLabel(
            footer,
            text="PLAUD Local",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            footer,
            textvariable=self.status_var,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=2, padx=16)

    def create_card(self, parent):
        return ctk.CTkFrame(
            parent,
            fg_color=COLOR_CARD,
            corner_radius=18,
            border_width=1,
            border_color=COLOR_BORDER,
        )

    def secondary_button(self, parent, text, command, width=160):
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=42,
            corner_radius=11,
            fg_color=COLOR_CARD_ALT,
            hover_color=COLOR_BORDER_LIGHT,
            border_width=1,
            border_color=COLOR_BORDER_LIGHT,
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=12, weight="bold"),
        )

    def section_title(self, parent, icon, title):
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.pack(anchor="w")

        ctk.CTkLabel(
            line,
            text=icon,
            width=24,
            text_color="#94A4BA",
            font=ctk.CTkFont(size=16),
        ).pack(side="left")

        ctk.CTkLabel(
            line,
            text=title,
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left", padx=(6, 0))

    def create_stat_card(self, parent, column, icon, title, variable, subtitle):
        card = self.create_card(parent)
        card.grid(
            row=0,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 8, 0 if column == 2 else 8),
        )

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            inner,
            text=icon,
            text_color="#93A3BA",
            font=ctk.CTkFont(size=22),
        ).pack(anchor="w")

        ctk.CTkLabel(
            inner,
            text=title,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(8, 0))

        ctk.CTkLabel(
            inner,
            textvariable=variable,
            text_color=COLOR_TEXT,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(anchor="w", pady=(3, 0))

        ctk.CTkLabel(
            inner,
            text=subtitle,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w")

    def info_line(self, parent, title, subtitle):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(13, 0))

        ctk.CTkLabel(
            row,
            text="◇",
            width=22,
            text_color=COLOR_GREEN,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left", anchor="n")

        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True, padx=(7, 0))

        ctk.CTkLabel(
            text,
            text=title,
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            text,
            text=subtitle,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=9),
            wraplength=300,
            justify="left",
        ).pack(anchor="w")

    def shortcut_line(self, parent, title, shortcut):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))

        ctk.CTkLabel(
            row,
            text=title,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(side="left")

        ctk.CTkLabel(
            row,
            text=shortcut,
            width=75,
            height=22,
            corner_radius=8,
            fg_color=COLOR_CARD_ALT,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=9),
        ).pack(side="right")

    def scroll_to(self, fraction):
        try:
            canvas = self.main_scroll._parent_canvas
            canvas.yview_moveto(fraction)
        except Exception:
            pass

    def load_config(self):
        if not CONFIG_FILE.exists():
            return

        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

            recent = data.get("recent_workspaces", [])
            self.recent_workspaces = [
                str(Path(path))
                for path in recent
                if Path(path).is_dir()
            ]

            last = data.get("last_workspace")

            if last and Path(last).is_dir():
                self.select_workspace(Path(last), save=False)

        except Exception:
            pass

    def save_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        data = {
            "last_workspace": str(self.workspace) if self.workspace else None,
            "recent_workspaces": self.recent_workspaces[:10],
        }

        CONFIG_FILE.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )

    def prepare_workspace(self, workspace):
        workspace.mkdir(parents=True, exist_ok=True)

        for folder in ["recordings", "transcripts", "notes", "work"]:
            (workspace / folder).mkdir(parents=True, exist_ok=True)

    def create_workspace(self):
        if self.process is not None:
            return

        dialog = NewProjectDialog(self)

        if dialog.result is None:
            return

        self.select_workspace(dialog.result)
        self.title_var.set("")
        self.add_log(f"\nProjet créé : {dialog.result}\n")

    def open_workspace(self):
        if self.process is not None:
            return

        path = filedialog.askdirectory(
            parent=self,
            title="Ouvrir un projet PLAUD",
        )

        if not path:
            return

        self.select_workspace(Path(path))

    def select_workspace(self, workspace, save=True):
        workspace = workspace.expanduser().resolve()
        self.prepare_workspace(workspace)

        self.workspace = workspace
        self.project_name_var.set(workspace.name)
        self.project_path_var.set(str(workspace))

        workspace_string = str(workspace)

        if workspace_string in self.recent_workspaces:
            self.recent_workspaces.remove(workspace_string)

        self.recent_workspaces.insert(0, workspace_string)
        self.recent_workspaces = self.recent_workspaces[:10]

        self.set_status("Prêt", "normal")
        self.refresh_project()

        if save:
            self.save_config()

    def open_workspace_folder(self):
        if not self.check_workspace():
            return
        open_with_system(self.workspace)

    def check_workspace(self):
        if self.workspace is None:
            messagebox.showwarning(
                "Aucun projet",
                "Commence par créer ou ouvrir un projet.",
                parent=self,
            )
            return False
        return True

    def refresh_project(self):
        self.refresh_stats()
        self.refresh_notes()

    def refresh_stats(self):
        if self.workspace is None:
            self.recordings_count_var.set("0")
            self.notes_count_var.set("0")
            self.duration_var.set("00:00:00")
            return

        recordings_folder = self.workspace / "recordings"
        notes_folder = self.workspace / "notes"

        audio_extensions = {
            ".wav", ".mp3", ".m4a", ".aac",
            ".flac", ".ogg", ".opus", ".mp4",
        }

        recordings = [
            path
            for path in recordings_folder.iterdir()
            if path.is_file() and path.suffix.lower() in audio_extensions
        ]

        notes = list(notes_folder.glob("*.md"))

        self.recordings_count_var.set(str(len(recordings)))
        self.notes_count_var.set(str(len(notes)))

        total = 0
        for audio in recordings:
            total += audio_duration(audio)

        self.duration_var.set(clean_duration(total))

    def refresh_notes(self):
        for widget in self.notes_container.winfo_children():
            widget.destroy()

        if self.workspace is None:
            self.empty_notes()
            return

        notes_folder = self.workspace / "notes"

        notes = sorted(
            notes_folder.glob("*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

        if not notes:
            self.empty_notes()
            return

        for note in notes[:6]:
            self.create_note_row(note)

    def empty_notes(self):
        row = ctk.CTkFrame(self.notes_container, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=16)

        ctk.CTkLabel(
            row,
            text="▤",
            width=42,
            height=42,
            corner_radius=21,
            fg_color=COLOR_CARD_ALT,
            text_color="#91A0B5",
            font=ctk.CTkFont(size=17),
        ).pack(side="left")

        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", padx=(14, 0))

        ctk.CTkLabel(
            text,
            text="Aucune note pour le moment",
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            text,
            text="Les notes générées apparaîtront ici.",
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w")

    def create_note_row(self, note):
        row = ctk.CTkFrame(
            self.notes_container,
            fg_color="transparent",
            height=62,
        )
        row.pack(fill="x", padx=12, pady=4)
        row.pack_propagate(False)

        ctk.CTkLabel(
            row,
            text="▤",
            width=42,
            height=42,
            corner_radius=21,
            fg_color=COLOR_CARD_ALT,
            text_color="#91A0B5",
            font=ctk.CTkFont(size=16),
        ).pack(side="left", padx=(4, 12))

        text_area = ctk.CTkFrame(row, fg_color="transparent")
        text_area.pack(side="left", fill="both", expand=True, pady=8)

        display_name = note.stem.replace("_", " ")

        ctk.CTkLabel(
            text_area,
            text=display_name,
            text_color=COLOR_TEXT_SOFT,
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        ).pack(anchor="w")

        modified = datetime.fromtimestamp(
            note.stat().st_mtime
        ).strftime("%d/%m/%Y à %H:%M")

        ctk.CTkLabel(
            text_area,
            text=modified,
            text_color=COLOR_TEXT_MUTED,
            font=ctk.CTkFont(size=9),
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkButton(
            row,
            text="Ouvrir",
            width=82,
            height=32,
            corner_radius=9,
            fg_color=COLOR_CARD_ALT,
            hover_color=COLOR_BORDER_LIGHT,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_SOFT,
            command=lambda path=note: open_with_system(path),
        ).pack(side="right", padx=(8, 6))

    def add_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def make_environment(self):
        environment = os.environ.copy()
        environment["PLAUD_WORKSPACE"] = str(self.workspace)
        return environment

    def start_process(self, arguments, recording=False):
        if self.process is not None:
            return

        command = [
            sys.executable,
            "-u",
            str(PLAUD_PY),
        ]
        command.extend(arguments)

        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(APP_ROOT),
                env=self.make_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as error:
            self.process = None
            messagebox.showerror("Erreur", str(error), parent=self)
            return

        self.recording_active = recording
        self.processing_active = not recording

        self.set_busy(True)

        if recording:
            self.recording_started_at = time.monotonic()
            self.timer_var.set("00:00:00")

            self.record_circle.configure(
                text="■",
                fg_color="#3A171C",
                hover_color="#4B1D23",
                border_color=COLOR_RED,
                text_color=COLOR_RED,
            )

            self.record_main_label.configure(text="Arrêter l'enregistrement")
            self.record_sub_label.configure(text="Enregistrement en cours")
            self.set_status("Enregistrement", "recording")
        else:
            self.set_status("Traitement", "processing")

        thread = threading.Thread(
            target=self.read_process_output,
            args=(self.process,),
            daemon=True,
        )
        thread.start()

    def read_process_output(self, process):
        try:
            for line in process.stdout:
                self.output_queue.put(("text", line))
        finally:
            return_code = process.wait()
            self.output_queue.put(("finished", return_code))

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"

        self.new_project_button.configure(state=state)
        self.open_project_button.configure(state=state)
        self.open_folder_button.configure(state=state)
        self.import_button.configure(state=state)
        self.title_entry.configure(state=state)

        if self.recording_active:
            self.record_circle.configure(state="normal")
        elif busy:
            self.record_circle.configure(state="disabled")
        else:
            self.record_circle.configure(state="normal")

    def toggle_recording(self):
        if self.recording_active:
            self.stop_recording()
        elif self.process is None:
            self.start_recording()

    def start_recording(self):
        if not self.check_workspace():
            return

        title = self.title_var.get().strip()

        if not title:
            messagebox.showwarning(
                "Titre manquant",
                "Donne un titre à ton enregistrement.",
                parent=self,
            )
            self.title_entry.focus_set()
            return

        self.add_log("\nDémarrage de l'enregistrement...\n")

        self.start_process(
            ["record", title],
            recording=True,
        )

    def stop_recording(self):
        if self.process is None:
            return

        if not self.recording_active:
            return

        self.recording_active = False
        self.processing_active = True

        self.record_circle.configure(
            state="disabled",
            text="…",
            fg_color=COLOR_CARD_ALT,
            border_color=COLOR_BORDER_LIGHT,
            text_color=COLOR_TEXT_MUTED,
        )

        self.record_main_label.configure(text="Traitement en cours")
        self.record_sub_label.configure(text="Whisper va maintenant transcrire l'audio")

        self.set_status("Transcription", "processing")
        self.add_log("\nArrêt demandé.\nFermeture du fichier audio...\n")

        try:
            self.process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass

    def import_audio(self):
        if not self.check_workspace():
            return

        if self.process is not None:
            return

        audio = filedialog.askopenfilename(
            parent=self,
            title="Importer un fichier audio",
            filetypes=[
                (
                    "Fichiers audio",
                    "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.mp4",
                ),
                ("Tous les fichiers", "*"),
            ],
        )

        if not audio:
            return

        title = self.title_var.get().strip()

        if not title:
            title = (
                Path(audio)
                .stem
                .replace("_", " ")
                .replace("-", " ")
            )
            self.title_var.set(title)

        self.add_log(f"\nImport du fichier :\n{audio}\n")

        self.start_process(
            ["file", audio, title],
            recording=False,
        )

    def process_finished(self, return_code):
        self.process = None
        self.recording_active = False
        self.processing_active = False
        self.recording_started_at = None

        self.timer_var.set("00:00:00")
        self.set_busy(False)

        self.record_circle.configure(
            text="●",
            fg_color=COLOR_BLUE_DARK,
            hover_color=COLOR_BLUE_SOFT,
            border_color=COLOR_BLUE,
            text_color="#7590FF",
            state="normal",
        )

        self.record_main_label.configure(text="Démarrer l'enregistrement")
        self.record_sub_label.configure(text="Clique pour commencer")

        self.refresh_project()

        if return_code == 0:
            self.set_status("Terminé", "success")
            self.add_log("\n✓ Traitement terminé.\n")
            self.title_var.set("")
        else:
            self.set_status("Erreur", "error")
            self.add_log("\n✕ Une erreur est survenue.\n")

            messagebox.showerror(
                "Erreur PLAUD",
                (
                    "Une erreur est survenue.\n\n"
                    "Regarde la section Activité pour voir les détails."
                ),
                parent=self,
            )

    def set_status(self, text, kind):
        self.status_var.set(text)

        if kind == "recording":
            color = "#34171C"
            text_color = COLOR_RED
        elif kind == "processing":
            color = "#302716"
            text_color = COLOR_ORANGE
        elif kind == "success":
            color = "#112A21"
            text_color = COLOR_GREEN
        elif kind == "error":
            color = "#34171C"
            text_color = COLOR_RED
        else:
            color = COLOR_CARD_ALT
            text_color = COLOR_TEXT_SOFT

        self.status_badge.configure(
            fg_color=color,
            text_color=text_color,
        )

    def update_timer(self):
        if self.recording_active and self.recording_started_at:
            elapsed = time.monotonic() - self.recording_started_at
            self.timer_var.set(clean_duration(elapsed))

        self.after(500, self.update_timer)

    def check_queue(self):
        try:
            while True:
                event = self.output_queue.get_nowait()

                if event[0] == "text":
                    self.add_log(event[1])
                elif event[0] == "finished":
                    self.process_finished(event[1])

        except queue.Empty:
            pass

        self.after(100, self.check_queue)

    def close_application(self):
        if self.process is not None:
            if self.recording_active:
                text = (
                    "Un enregistrement est encore en cours.\n\n"
                    "Arrête-le avant de fermer PLAUD."
                )
            else:
                text = (
                    "Whisper ou Qwen travaille encore.\n\n"
                    "Attends la fin du traitement avant de fermer PLAUD."
                )

            messagebox.showwarning(
                "PLAUD travaille encore",
                text,
                parent=self,
            )
            return

        self.destroy()


def main():
    if not PLAUD_PY.exists():
        print("ERREUR : plaud.py est introuvable.")
        sys.exit(1)

    app = PlaudApp()
    app.mainloop()


if __name__ == "__main__":
    main()
