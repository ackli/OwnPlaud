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
import uuid
import wave
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox


# ============================================================
# CONFIGURATION
# ============================================================

APP_ROOT = Path(__file__).resolve().parent
PLAUD_PY = APP_ROOT / "plaud.py"

CONFIG_DIR = Path.home() / ".config" / "plaud-local"
CONFIG_FILE = CONFIG_DIR / "config.json"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ============================================================
# PALETTE
# ============================================================

BG = "#080C12"
SIDEBAR = "#0C1119"
CARD = "#111822"
CARD_2 = "#151D28"
CARD_3 = "#192331"
INNER = "#0B1119"

BORDER = "#222D3A"
BORDER_2 = "#2D3A4A"

TEXT = "#F4F7FB"
TEXT_SOFT = "#C7D0DC"
TEXT_MUTED = "#7F8B9B"

BLUE = "#536DFE"
BLUE_HOVER = "#667CFF"
BLUE_DARK = "#172146"
BLUE_SOFT = "#202B59"

RED = "#E95A67"
GREEN = "#42C98A"
ORANGE = "#E5A94C"


# ============================================================
# OUTILS
# ============================================================

def open_with_system(path):
    try:
        path = str(path)

        if os.name == "nt":
            os.startfile(path)

        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        else:
            subprocess.Popen(
                ["xdg-open", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    except Exception:
        pass


def format_duration(seconds):
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def get_audio_duration(path):
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


# ============================================================
# CRÉATION D'UN DOSSIER PLAUD
# ============================================================

class NewFolderDialog(ctk.CTkToplevel):

    def __init__(self, parent):
        super().__init__(parent)

        self.result = None

        self.title("Nouveau dossier PLAUD")
        self.geometry("620x430")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        self.transient(parent)
        self.grab_set()

        documents = Path.home() / "Documents"
        default_location = documents if documents.exists() else Path.home()

        self.name_var = ctk.StringVar()
        self.location_var = ctk.StringVar(value=str(default_location))

        self.build_ui()

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.after(150, self.name_entry.focus_set)
        self.wait_window()

    def build_ui(self):
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=32, pady=30)

        ctk.CTkLabel(
            outer,
            text="Créer un dossier",
            text_color=TEXT,
            font=ctk.CTkFont(size=25, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            outer,
            text=(
                "Exemples : École, Travail, Personnel. "
                "Chaque dossier garde ses propres enregistrements et notes."
            ),
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=13),
            justify="left",
            wraplength=540,
        ).pack(anchor="w", pady=(6, 24))

        card = ctk.CTkFrame(
            outer,
            fg_color=CARD,
            corner_radius=20,
            border_width=1,
            border_color=BORDER,
        )
        card.pack(fill="x")

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=24, pady=24)

        ctk.CTkLabel(
            form,
            text="Nom du dossier",
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        self.name_entry = ctk.CTkEntry(
            form,
            textvariable=self.name_var,
            placeholder_text="Ex : Travail",
            height=46,
            corner_radius=12,
            fg_color=INNER,
            border_color=BORDER_2,
            border_width=1,
            text_color=TEXT,
        )
        self.name_entry.pack(fill="x", pady=(8, 20))

        ctk.CTkLabel(
            form,
            text="Où ranger ce dossier ?",
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))

        self.location_entry = ctk.CTkEntry(
            row,
            textvariable=self.location_var,
            height=46,
            corner_radius=12,
            fg_color=INNER,
            border_color=BORDER_2,
            border_width=1,
            text_color=TEXT,
        )
        self.location_entry.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            row,
            text="Parcourir",
            command=self.browse,
            width=110,
            height=46,
            corner_radius=12,
            fg_color=CARD_2,
            hover_color=BORDER_2,
            border_width=1,
            border_color=BORDER_2,
            text_color=TEXT,
        ).pack(side="left", padx=(10, 0))

        buttons = ctk.CTkFrame(outer, fg_color="transparent")
        buttons.pack(fill="x", pady=(22, 0))

        ctk.CTkButton(
            buttons,
            text="Annuler",
            command=self.cancel,
            width=120,
            height=44,
            corner_radius=12,
            fg_color=CARD_2,
            hover_color=BORDER_2,
            border_width=1,
            border_color=BORDER_2,
            text_color=TEXT,
        ).pack(side="right")

        ctk.CTkButton(
            buttons,
            text="+  Créer le dossier",
            command=self.create_folder,
            width=175,
            height=44,
            corner_radius=12,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="right", padx=(0, 10))

        self.bind("<Return>", lambda event: self.create_folder())

    def browse(self):
        path = filedialog.askdirectory(
            parent=self,
            title="Choisir où créer le dossier",
            initialdir=self.location_var.get(),
        )
        if path:
            self.location_var.set(path)

    def create_folder(self):
        name = self.name_var.get().strip()
        location = self.location_var.get().strip()

        if not name:
            messagebox.showwarning(
                "Nom manquant",
                "Donne un nom au dossier.",
                parent=self,
            )
            return

        if name in {".", ".."} or "/" in name or "\\" in name:
            messagebox.showwarning(
                "Nom incorrect",
                "Utilise un nom simple, par exemple : Travail",
                parent=self,
            )
            return

        if not location:
            messagebox.showwarning(
                "Emplacement manquant",
                "Choisis l'endroit où créer le dossier.",
                parent=self,
            )
            return

        base = Path(location).expanduser()

        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            messagebox.showerror(
                "Erreur",
                f"Impossible d'utiliser cet emplacement.\n\n{error}",
                parent=self,
            )
            return

        folder = base / name

        if folder.exists():
            messagebox.showwarning(
                "Dossier existant",
                "Un dossier portant déjà ce nom existe.",
                parent=self,
            )
            return

        try:
            folder.mkdir(parents=True, exist_ok=False)
        except Exception as error:
            messagebox.showerror("Erreur", str(error), parent=self)
            return

        self.result = folder.resolve()
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


# ============================================================
# BIBLIOTHÈQUE DE DOSSIERS ET NOTES
# ============================================================

class LibraryDialog(ctk.CTkToplevel):

    def __init__(self, app):
        super().__init__(app)

        self.app = app
        self.selected_folder = None

        self.title("Bibliothèque PLAUD")
        self.geometry("940x650")
        self.minsize(820, 560)
        self.configure(fg_color=BG)

        self.transient(app)
        self.grab_set()

        self.build_ui()
        self.refresh_folders()

    def build_ui(self):
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=24, pady=24)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", pady=(0, 18))

        left_header = ctk.CTkFrame(header, fg_color="transparent")
        left_header.pack(side="left")

        ctk.CTkLabel(
            left_header,
            text="Bibliothèque",
            text_color=TEXT,
            font=ctk.CTkFont(size=25, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            left_header,
            text="Choisis un dossier, puis ouvre directement une note.",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkButton(
            header,
            text="+ Nouveau dossier",
            command=self.create_new_folder,
            width=150,
            height=40,
            corner_radius=11,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
        ).pack(side="right")

        ctk.CTkButton(
            header,
            text="Ajouter un dossier existant",
            command=self.add_existing_folder,
            width=180,
            height=40,
            corner_radius=11,
            fg_color=CARD_2,
            hover_color=BORDER_2,
            border_width=1,
            border_color=BORDER_2,
            text_color=TEXT,
        ).pack(side="right", padx=(0, 10))

        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, minsize=280)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Colonne dossiers
        folders_card = ctk.CTkFrame(
            body,
            fg_color=CARD,
            corner_radius=18,
            border_width=1,
            border_color=BORDER,
        )
        folders_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        ctk.CTkLabel(
            folders_card,
            text="DOSSIERS",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(18, 10))

        self.folders_scroll = ctk.CTkScrollableFrame(
            folders_card,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=BORDER_2,
            scrollbar_button_hover_color=BLUE,
        )
        self.folders_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Colonne notes
        notes_card = ctk.CTkFrame(
            body,
            fg_color=CARD,
            corner_radius=18,
            border_width=1,
            border_color=BORDER,
        )
        notes_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        notes_header = ctk.CTkFrame(notes_card, fg_color="transparent")
        notes_header.pack(fill="x", padx=18, pady=(18, 10))

        self.notes_title = ctk.CTkLabel(
            notes_header,
            text="Notes",
            text_color=TEXT,
            font=ctk.CTkFont(size=17, weight="bold"),
        )
        self.notes_title.pack(side="left")

        self.use_folder_button = ctk.CTkButton(
            notes_header,
            text="Utiliser ce dossier",
            command=self.use_selected_folder,
            width=135,
            height=34,
            corner_radius=10,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            state="disabled",
        )
        self.use_folder_button.pack(side="right")

        self.notes_scroll = ctk.CTkScrollableFrame(
            notes_card,
            fg_color=INNER,
            corner_radius=14,
            scrollbar_button_color=BORDER_2,
            scrollbar_button_hover_color=BLUE,
        )
        self.notes_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.enable_touchpad_scroll(self.folders_scroll)
        self.enable_touchpad_scroll(self.notes_scroll)

    def enable_touchpad_scroll(self, scroll_frame):
        try:
            canvas = scroll_frame._parent_canvas

            def on_mousewheel(event):
                delta = getattr(event, "delta", 0)
                if delta:
                    units = -1 if delta > 0 else 1
                    if abs(delta) >= 120:
                        units *= max(1, abs(int(delta / 120)))
                    canvas.yview_scroll(units * 3, "units")
                return "break"

            def on_button4(event):
                canvas.yview_scroll(-3, "units")
                return "break"

            def on_button5(event):
                canvas.yview_scroll(3, "units")
                return "break"

            canvas.bind("<MouseWheel>", on_mousewheel, add="+")
            canvas.bind("<Button-4>", on_button4, add="+")
            canvas.bind("<Button-5>", on_button5, add="+")
        except Exception:
            pass

    def refresh_folders(self):
        for widget in self.folders_scroll.winfo_children():
            widget.destroy()

        paths = list(self.app.recent_folders)

        if self.app.workspace:
            current = str(self.app.workspace)
            if current in paths:
                paths.remove(current)
            paths.insert(0, current)

        seen = set()
        paths = [
            p for p in paths
            if p not in seen and not seen.add(p) and Path(p).is_dir()
        ]

        if not paths:
            ctk.CTkLabel(
                self.folders_scroll,
                text="Aucun dossier pour le moment.",
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=10, pady=12)
            return

        for path_text in paths:
            path = Path(path_text)
            active = self.app.workspace and path.resolve() == self.app.workspace.resolve()

            button = ctk.CTkButton(
                self.folders_scroll,
                text=("●  " if active else "□  ") + path.name,
                command=lambda p=path: self.select_folder(p),
                height=44,
                corner_radius=11,
                anchor="w",
                fg_color=BLUE_DARK if active else CARD_2,
                hover_color=BLUE_SOFT if active else BORDER_2,
                border_width=1,
                border_color=BLUE if active else BORDER,
                text_color=TEXT,
            )
            button.pack(fill="x", padx=4, pady=4)

        if self.app.workspace:
            self.select_folder(self.app.workspace)

    def select_folder(self, folder):
        folder = Path(folder).expanduser().resolve()
        self.selected_folder = folder

        self.notes_title.configure(text=f"Notes — {folder.name}")
        self.use_folder_button.configure(state="normal")

        for widget in self.notes_scroll.winfo_children():
            widget.destroy()

        notes_dir = folder / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        notes = sorted(
            notes_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not notes:
            ctk.CTkLabel(
                self.notes_scroll,
                text="Aucune note dans ce dossier.",
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=12),
            ).pack(anchor="w", padx=12, pady=14)
            return

        for note in notes:
            row = ctk.CTkFrame(
                self.notes_scroll,
                fg_color=CARD_2,
                corner_radius=12,
                border_width=1,
                border_color=BORDER,
            )
            row.pack(fill="x", padx=4, pady=5)

            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=14, pady=12)

            ctk.CTkLabel(
                text,
                text=note.stem.replace("_", " "),
                text_color=TEXT_SOFT,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
            ).pack(anchor="w")

            modified = datetime.fromtimestamp(
                note.stat().st_mtime
            ).strftime("%d/%m/%Y à %H:%M")

            ctk.CTkLabel(
                text,
                text=modified,
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=9),
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))

            ctk.CTkButton(
                row,
                text="Ouvrir",
                command=lambda n=note: open_with_system(n),
                width=82,
                height=32,
                corner_radius=9,
                fg_color=CARD_3,
                hover_color=BORDER_2,
                border_width=1,
                border_color=BORDER_2,
                text_color=TEXT,
            ).pack(side="right", padx=12)

    def use_selected_folder(self):
        if not self.selected_folder:
            return

        self.app.select_workspace(self.selected_folder)
        self.destroy()

    def add_existing_folder(self):
        path = filedialog.askdirectory(
            parent=self,
            title="Ajouter un dossier PLAUD",
        )

        if not path:
            return

        folder = Path(path).expanduser().resolve()
        self.app.prepare_workspace(folder)
        self.app.remember_folder(folder)
        self.refresh_folders()
        self.select_folder(folder)

    def create_new_folder(self):
        dialog = NewFolderDialog(self)

        if dialog.result is None:
            return

        self.app.prepare_workspace(dialog.result)
        self.app.remember_folder(dialog.result)
        self.refresh_folders()
        self.select_folder(dialog.result)


# ============================================================
# APPLICATION PRINCIPALE
# ============================================================

class PlaudApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title("PLAUD")
        self.geometry("1180x820")
        self.minsize(900, 650)
        self.configure(fg_color=BG)

        self.workspace = None
        self.process = None
        self.recording_active = False
        self.processing_active = False
        self.recording_started_at = None
        self.output_queue = queue.Queue()
        self.recent_folders = []

        self.current_operation = None
        self.current_workspace = None
        self.current_pending_context = None
        self.pending_capture_title = "Note vocale"
        self.batch_mode = False

        self.folder_name_var = ctk.StringVar(value="Aucun dossier sélectionné")
        self.folder_path_var = ctk.StringVar(value="Choisis un dossier pour commencer.")
        self.title_var = ctk.StringVar()
        self.timer_var = ctk.StringVar(value="00:00:00")
        self.status_var = ctk.StringVar(value="Prêt")
        self.recordings_count_var = ctk.StringVar(value="0")
        self.notes_count_var = ctk.StringVar(value="0")
        self.duration_var = ctk.StringVar(value="00:00:00")
        self.pending_count_var = ctk.StringVar(value="0")

        self.build_layout()
        self.load_config()
        self.refresh_all()

        self.after(100, self.check_queue)
        self.after(500, self.update_timer)

        self.protocol("WM_DELETE_WINDOW", self.close_application)

        self.bind_all("<Control-n>", lambda event: self.create_folder())
        self.bind_all("<Control-o>", lambda event: self.open_library())
        self.bind_all("<Control-i>", lambda event: self.import_audio())
        self.bind_all("<Control-r>", lambda event: self.toggle_recording())

    # ========================================================
    # LAYOUT
    # ========================================================

    def build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.build_sidebar()

        self.main_scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=BG,
            corner_radius=0,
            scrollbar_button_color=BORDER_2,
            scrollbar_button_hover_color=BLUE,
        )
        self.main_scroll.grid(row=0, column=1, sticky="nsew")
        self.main_scroll.grid_columnconfigure(0, weight=1)

        self.build_dashboard()
        self.enable_touchpad_scroll(self.main_scroll)

    def build_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self,
            width=210,
            corner_radius=0,
            fg_color=SIDEBAR,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        ctk.CTkLabel(
            self.sidebar,
            text="✣  P L A U D",
            text_color=TEXT,
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(28, 30))

        self.side_button("⌂   Accueil", lambda: self.scroll_to(0.0), active=True)
        self.side_button("◉   Enregistrer", self.sidebar_record_action)
        self.side_button("⇧   Importer", self.import_audio)
        self.side_button("▣   Dossiers", self.open_library)
        self.side_button("▤   Notes", self.open_library)

        pending_header = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        pending_header.pack(fill="x", padx=15, pady=(16, 6))

        ctk.CTkLabel(
            pending_header,
            text="EN ATTENTE",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            pending_header,
            textvariable=self.pending_count_var,
            width=28,
            height=22,
            corner_radius=11,
            fg_color=CARD_2,
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(side="right")

        self.pending_scroll = ctk.CTkScrollableFrame(
            self.sidebar,
            height=185,
            fg_color=CARD,
            corner_radius=14,
            border_width=1,
            border_color=BORDER,
            scrollbar_button_color=BORDER_2,
            scrollbar_button_hover_color=BLUE,
        )
        self.pending_scroll.pack(fill="x", padx=12, pady=(0, 8))
        self.enable_touchpad_scroll(self.pending_scroll)

        self.process_all_button = ctk.CTkButton(
            self.sidebar,
            text="▶  Tout traiter",
            command=self.process_all_pending,
            height=36,
            corner_radius=10,
            fg_color=BLUE_DARK,
            hover_color=BLUE_SOFT,
            border_width=1,
            border_color=BLUE,
            text_color="#AEB9FF",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.process_all_button.pack(fill="x", padx=12, pady=(0, 10))

        spacer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        spacer.pack(fill="both", expand=True)

        ctk.CTkLabel(
            self.sidebar,
            text="DOSSIER ACTUEL",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=22)

        ctk.CTkLabel(
            self.sidebar,
            textvariable=self.folder_name_var,
            text_color=TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="left",
            wraplength=165,
        ).pack(anchor="w", padx=22, pady=(8, 4))

        ctk.CTkLabel(
            self.sidebar,
            textvariable=self.folder_path_var,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
            justify="left",
            wraplength=165,
        ).pack(anchor="w", padx=22, pady=(0, 28))

        ctk.CTkLabel(
            self.sidebar,
            text="PLAUD Local",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w", padx=22, pady=(0, 22))

    def side_button(self, text, command, active=False):
        button = ctk.CTkButton(
            self.sidebar,
            text=text,
            command=command,
            height=42,
            corner_radius=9,
            anchor="w",
            fg_color=CARD_2 if active else "transparent",
            hover_color=CARD_2,
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        button.pack(fill="x", padx=14, pady=3)

    def build_dashboard(self):
        content = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        content.grid(row=0, column=0, sticky="ew", padx=28, pady=(26, 30))
        content.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_area = ctk.CTkFrame(header, fg_color="transparent")
        title_area.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            title_area,
            text="Bienvenue dans PLAUD",
            text_color=TEXT,
            font=ctk.CTkFont(size=28, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_area,
            text="Enregistre, transcris et retrouve facilement tes notes.",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", pady=(4, 0))

        self.status_badge = ctk.CTkLabel(
            header,
            textvariable=self.status_var,
            width=100,
            height=42,
            corner_radius=21,
            fg_color=CARD_2,
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.status_badge.grid(row=0, column=1, sticky="e")

        # Stats
        stats = ctk.CTkFrame(content, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(26, 18))
        stats.grid_columnconfigure((0, 1, 2), weight=1)

        self.stat_card(stats, 0, "◉", "Enregistrements", self.recordings_count_var)
        self.stat_card(stats, 1, "▤", "Notes", self.notes_count_var)
        self.stat_card(stats, 2, "◷", "Durée totale", self.duration_var)

        # Dossier actuel
        folder_card = self.card(content)
        folder_card.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        folder_card.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(folder_card, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=24, pady=24)

        self.section_title(left, "▣", "Dossier actuel")

        ctk.CTkLabel(
            left,
            textvariable=self.folder_name_var,
            text_color=TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", pady=(18, 5))

        ctk.CTkLabel(
            left,
            textvariable=self.folder_path_var,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12),
            wraplength=480,
            justify="left",
        ).pack(anchor="w")

        buttons = ctk.CTkFrame(folder_card, fg_color="transparent")
        buttons.grid(row=0, column=1, sticky="e", padx=(10, 24), pady=24)

        self.new_folder_button = ctk.CTkButton(
            buttons,
            text="+   Nouveau dossier",
            command=self.create_folder,
            width=190,
            height=44,
            corner_radius=12,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.new_folder_button.pack(pady=(0, 9))

        self.library_button = self.secondary_button(
            buttons,
            "▣   Bibliothèque",
            self.open_library,
            width=190,
        )
        self.library_button.pack(pady=(0, 9))

        self.open_folder_button = self.secondary_button(
            buttons,
            "□   Ouvrir sur le disque",
            self.open_workspace_folder,
            width=190,
        )
        self.open_folder_button.pack()

        # Enregistrement
        record_card = self.card(content)
        record_card.grid(row=3, column=0, sticky="ew", pady=(0, 18))

        record_inner = ctk.CTkFrame(record_card, fg_color="transparent")
        record_inner.pack(fill="x", padx=24, pady=24)

        self.section_title(record_inner, "◉", "Nouvel enregistrement")

        ctk.CTkLabel(
            record_inner,
            text="Titre de l'enregistrement",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(20, 7))

        self.title_entry = ctk.CTkEntry(
            record_inner,
            textvariable=self.title_var,
            placeholder_text="Ex : Réunion équipe, Cours de maths...",
            height=48,
            corner_radius=11,
            fg_color=INNER,
            border_color=BORDER_2,
            border_width=1,
            text_color=TEXT,
        )
        self.title_entry.pack(fill="x")

        recorder = ctk.CTkFrame(
            record_inner,
            fg_color=INNER,
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
            fg_color=BLUE_DARK,
            hover_color=BLUE_SOFT,
            border_width=2,
            border_color=BLUE,
            text_color="#7590FF",
            font=ctk.CTkFont(size=26, weight="bold"),
            command=self.toggle_recording,
        )
        self.record_circle.grid(row=0, column=0, rowspan=2, padx=(18, 20), pady=18)

        self.record_main_label = ctk.CTkLabel(
            recorder,
            text="Démarrer l'enregistrement",
            text_color=TEXT,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.record_main_label.grid(row=0, column=1, sticky="sw", pady=(20, 0))

        self.record_sub_label = ctk.CTkLabel(
            recorder,
            text="Clique pour commencer",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
        )
        self.record_sub_label.grid(row=1, column=1, sticky="nw", pady=(1, 20))

        timer = ctk.CTkFrame(
            recorder,
            width=220,
            height=70,
            corner_radius=12,
            fg_color=CARD,
            border_width=1,
            border_color=BORDER,
        )
        timer.grid(row=0, column=2, rowspan=2, padx=(10, 18), pady=18, sticky="e")
        timer.grid_propagate(False)

        ctk.CTkLabel(
            timer,
            textvariable=self.timer_var,
            text_color=TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(pady=(10, 0))

        ctk.CTkLabel(
            timer,
            text="Durée",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack()

        # Import
        import_card = self.card(content)
        import_card.grid(row=4, column=0, sticky="ew", pady=(0, 18))
        import_card.grid_columnconfigure(0, weight=1)

        import_text = ctk.CTkFrame(import_card, fg_color="transparent")
        import_text.grid(row=0, column=0, sticky="w", padx=24, pady=22)

        self.section_title(import_text, "⇧", "Importer un fichier audio")

        ctk.CTkLabel(
            import_text,
            text="MP3, M4A, WAV, FLAC, OGG, OPUS… La note sera rangée dans le dossier actuel.",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            wraplength=570,
            justify="left",
        ).pack(anchor="w", pady=(7, 0))

        self.import_button = self.secondary_button(
            import_card,
            "Choisir un fichier   ⇧",
            self.import_audio,
            width=180,
        )
        self.import_button.grid(row=0, column=1, sticky="e", padx=24, pady=22)

        # Notes récentes
        notes_card = self.card(content)
        notes_card.grid(row=5, column=0, sticky="ew", pady=(0, 18))

        notes_header = ctk.CTkFrame(notes_card, fg_color="transparent")
        notes_header.pack(fill="x", padx=24, pady=(22, 10))

        self.section_title(notes_header, "▤", "Notes récentes")

        ctk.CTkButton(
            notes_header,
            text="Tout voir",
            command=self.open_library,
            width=90,
            height=32,
            corner_radius=10,
            fg_color=CARD_2,
            hover_color=BORDER_2,
            border_width=1,
            border_color=BORDER_2,
            text_color=TEXT_SOFT,
        ).pack(side="right")

        self.notes_container = ctk.CTkFrame(
            notes_card,
            fg_color=INNER,
            corner_radius=14,
        )
        self.notes_container.pack(fill="x", padx=18, pady=(0, 18))

        # Bas utile : activité + raccourcis/dossiers récents
        lower = ctk.CTkFrame(content, fg_color="transparent")
        lower.grid(row=6, column=0, sticky="ew", pady=(0, 18))
        lower.grid_columnconfigure(0, weight=1)
        lower.grid_columnconfigure(1, weight=1)

        activity_card = self.card(lower)
        activity_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        activity_header = ctk.CTkFrame(activity_card, fg_color="transparent")
        activity_header.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(
            activity_header,
            text=">_   Activité",
            text_color=TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            activity_header,
            text="Vider",
            command=self.clear_log,
            width=65,
            height=30,
            corner_radius=15,
            fg_color=CARD_2,
            hover_color=BORDER_2,
            text_color=TEXT_MUTED,
        ).pack(side="right")

        self.log = ctk.CTkTextbox(
            activity_card,
            height=250,
            corner_radius=12,
            fg_color=INNER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(family="Monospace", size=11),
        )
        self.log.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.log.insert(
            "end",
            (
                "PLAUD est prêt.\n\n"
                "Choisis un dossier, puis enregistre ou importe un fichier audio.\n"
                "Les étapes du traitement apparaîtront ici."
            ),
        )
        self.log.configure(state="disabled")

        utility_col = ctk.CTkFrame(lower, fg_color="transparent")
        utility_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        recent_card = self.card(utility_col)
        recent_card.pack(fill="x", pady=(0, 14))

        recent_inner = ctk.CTkFrame(recent_card, fg_color="transparent")
        recent_inner.pack(fill="x", padx=20, pady=20)

        self.section_title(recent_inner, "▣", "Dossiers récents")

        self.recent_container = ctk.CTkFrame(recent_inner, fg_color="transparent")
        self.recent_container.pack(fill="x", pady=(10, 0))

        shortcuts = self.card(utility_col)
        shortcuts.pack(fill="x")

        shortcuts_inner = ctk.CTkFrame(shortcuts, fg_color="transparent")
        shortcuts_inner.pack(fill="x", padx=20, pady=20)

        self.section_title(shortcuts_inner, "◷", "Raccourcis")

        self.shortcut_line(shortcuts_inner, "Nouveau dossier", "Ctrl + N")
        self.shortcut_line(shortcuts_inner, "Bibliothèque", "Ctrl + O")
        self.shortcut_line(shortcuts_inner, "Enregistrer / arrêter", "Ctrl + R")
        self.shortcut_line(shortcuts_inner, "Importer", "Ctrl + I")

        # Footer
        footer = ctk.CTkFrame(
            content,
            fg_color=CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
            height=46,
        )
        footer.grid(row=7, column=0, sticky="ew", pady=(0, 12))
        footer.grid_propagate(False)
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            footer,
            textvariable=self.folder_name_var,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=0, padx=16)

        ctk.CTkLabel(
            footer,
            text="PLAUD Local",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            footer,
            textvariable=self.status_var,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=2, padx=16)

    # ========================================================
    # COMPOSANTS
    # ========================================================

    def card(self, parent):
        return ctk.CTkFrame(
            parent,
            fg_color=CARD,
            corner_radius=18,
            border_width=1,
            border_color=BORDER,
        )

    def secondary_button(self, parent, text, command, width=160):
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=42,
            corner_radius=11,
            fg_color=CARD_2,
            hover_color=BORDER_2,
            border_width=1,
            border_color=BORDER_2,
            text_color=TEXT_SOFT,
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
            text_color=TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left", padx=(6, 0))

    def stat_card(self, parent, column, icon, title, variable):
        card = self.card(parent)
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
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(8, 0))

        ctk.CTkLabel(
            inner,
            textvariable=variable,
            text_color=TEXT,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(anchor="w", pady=(3, 0))

        ctk.CTkLabel(
            inner,
            text="Dossier actuel",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w")

    def shortcut_line(self, parent, title, shortcut):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))

        ctk.CTkLabel(
            row,
            text=title,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(side="left")

        ctk.CTkLabel(
            row,
            text=shortcut,
            width=75,
            height=22,
            corner_radius=8,
            fg_color=CARD_2,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=9),
        ).pack(side="right")

    # ========================================================
    # SCROLL MOLETTE + PAVÉ TACTILE
    # ========================================================

    def enable_touchpad_scroll(self, scroll_frame):
        try:
            canvas = scroll_frame._parent_canvas

            def on_mousewheel(event):
                delta = getattr(event, "delta", 0)

                if delta:
                    # Windows/macOS/Tk moderne
                    if abs(delta) >= 120:
                        steps = int(delta / 120)
                    else:
                        steps = 1 if delta > 0 else -1

                    canvas.yview_scroll(-steps * 3, "units")

                return "break"

            def on_button4(event):
                canvas.yview_scroll(-3, "units")
                return "break"

            def on_button5(event):
                canvas.yview_scroll(3, "units")
                return "break"

            canvas.bind("<MouseWheel>", on_mousewheel, add="+")
            canvas.bind("<Button-4>", on_button4, add="+")
            canvas.bind("<Button-5>", on_button5, add="+")
        except Exception:
            pass

    def scroll_to(self, fraction):
        try:
            self.main_scroll._parent_canvas.yview_moveto(fraction)
        except Exception:
            pass

    # ========================================================
    # ACTIONS SIDEBAR
    # ========================================================

    def sidebar_record_action(self):
        if self.recording_active:
            self.stop_recording()
            return

        self.scroll_to(0.25)
        self.after(150, self.title_entry.focus_set)

    def open_library(self):
        if self.process is not None:
            messagebox.showwarning(
                "PLAUD travaille",
                "Attends la fin de l'enregistrement ou du traitement.",
                parent=self,
            )
            return

        LibraryDialog(self)

    # ========================================================
    # CONFIGURATION
    # ========================================================

    def load_config(self):
        if not CONFIG_FILE.exists():
            return

        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

            # Compatibilité avec l'ancienne clé recent_workspaces
            recent = data.get(
                "recent_folders",
                data.get("recent_workspaces", []),
            )

            self.recent_folders = [
                str(Path(path))
                for path in recent
                if Path(path).is_dir()
            ]

            last = data.get(
                "last_folder",
                data.get("last_workspace"),
            )

            if last and Path(last).is_dir():
                self.select_workspace(Path(last), save=False)

        except Exception:
            pass

    def save_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        data = {
            "last_folder": str(self.workspace) if self.workspace else None,
            "recent_folders": self.recent_folders[:12],
        }

        CONFIG_FILE.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )

    def remember_folder(self, folder):
        folder = Path(folder).expanduser().resolve()
        value = str(folder)

        if value in self.recent_folders:
            self.recent_folders.remove(value)

        self.recent_folders.insert(0, value)
        self.recent_folders = self.recent_folders[:12]

        self.save_config()
        self.refresh_recent_folders()
        self.refresh_pending_sidebar()

    # ========================================================
    # DOSSIERS
    # ========================================================

    def prepare_workspace(self, workspace):
        workspace.mkdir(parents=True, exist_ok=True)

        for folder in ["recordings", "transcripts", "notes", "work", "queue"]:
            (workspace / folder).mkdir(parents=True, exist_ok=True)

    def create_folder(self):
        if self.process is not None:
            return

        dialog = NewFolderDialog(self)

        if dialog.result is None:
            return

        self.select_workspace(dialog.result)
        self.title_var.set("")
        self.add_log(f"\nDossier créé : {dialog.result}\n")

    def select_workspace(self, workspace, save=True):
        workspace = Path(workspace).expanduser().resolve()
        self.prepare_workspace(workspace)

        self.workspace = workspace
        self.folder_name_var.set(workspace.name)
        self.folder_path_var.set(str(workspace))

        value = str(workspace)

        if value in self.recent_folders:
            self.recent_folders.remove(value)

        self.recent_folders.insert(0, value)
        self.recent_folders = self.recent_folders[:12]

        self.set_status("Prêt", "normal")
        self.refresh_all()

        if save:
            self.save_config()

    def open_workspace_folder(self):
        if not self.check_workspace():
            return

        open_with_system(self.workspace)

    def check_workspace(self):
        if self.workspace is None:
            messagebox.showwarning(
                "Aucun dossier",
                "Choisis d'abord un dossier comme École, Travail ou Personnel.",
                parent=self,
            )
            return False

        return True

    # ========================================================
    # FILE D'ATTENTE
    # ========================================================

    def queue_file(self, folder):
        folder = Path(folder)
        queue_dir = folder / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        return queue_dir / "pending.json"

    def load_pending(self, folder):
        path = self.queue_file(folder)

        if not path.exists():
            return []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass

        return []

    def save_pending(self, folder, items):
        path = self.queue_file(folder)
        path.write_text(
            json.dumps(items, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add_pending(self, folder, title, audio_name, source_type):
        folder = Path(folder).expanduser().resolve()
        items = self.load_pending(folder)

        items.append(
            {
                "id": uuid.uuid4().hex,
                "title": title,
                "audio_name": audio_name,
                "source_type": source_type,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )

        self.save_pending(folder, items)

        value = str(folder)
        if value in self.recent_folders:
            self.recent_folders.remove(value)
        self.recent_folders.insert(0, value)
        self.recent_folders = self.recent_folders[:12]
        self.save_config()

        self.refresh_pending_sidebar()
        self.refresh_stats()

    def remove_pending(self, folder, item_id):
        items = [
            item
            for item in self.load_pending(folder)
            if item.get("id") != item_id
        ]
        self.save_pending(folder, items)
        self.refresh_pending_sidebar()
        self.refresh_stats()

    def all_pending(self):
        folders = []

        if self.workspace:
            folders.append(self.workspace)

        for path_text in self.recent_folders:
            path = Path(path_text)
            if path.is_dir() and path not in folders:
                folders.append(path)

        result = []

        for folder in folders:
            for item in self.load_pending(folder):
                result.append((folder, item))

        result.sort(key=lambda pair: pair[1].get("created_at", ""))
        return result

    def refresh_pending_sidebar(self):
        for widget in self.pending_scroll.winfo_children():
            widget.destroy()

        pending = self.all_pending()
        self.pending_count_var.set(str(len(pending)))

        if not pending:
            ctk.CTkLabel(
                self.pending_scroll,
                text="Aucun audio\nen attente.",
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=10),
                justify="left",
            ).pack(anchor="w", padx=8, pady=10)

            self.process_all_button.configure(state="disabled")
            return

        self.process_all_button.configure(
            state="disabled" if self.process is not None else "normal"
        )

        for folder, item in pending:
            card = ctk.CTkFrame(
                self.pending_scroll,
                fg_color=CARD_2,
                corner_radius=10,
                border_width=1,
                border_color=BORDER,
            )
            card.pack(fill="x", padx=2, pady=4)

            text = ctk.CTkFrame(card, fg_color="transparent")
            text.pack(fill="x", padx=8, pady=(8, 4))

            ctk.CTkLabel(
                text,
                text=item.get("title", "Sans titre"),
                text_color=TEXT_SOFT,
                font=ctk.CTkFont(size=10, weight="bold"),
                anchor="w",
                justify="left",
                wraplength=130,
            ).pack(anchor="w")

            ctk.CTkLabel(
                text,
                text=folder.name,
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=9),
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))

            ctk.CTkButton(
                card,
                text="▶ Traiter",
                command=lambda f=folder, i=item: self.process_pending_item(f, i),
                height=28,
                corner_radius=8,
                fg_color=BLUE_DARK,
                hover_color=BLUE_SOFT,
                border_width=1,
                border_color=BLUE,
                text_color="#AEB9FF",
                font=ctk.CTkFont(size=9, weight="bold"),
                state="disabled" if self.process is not None else "normal",
            ).pack(fill="x", padx=8, pady=(0, 8))

    def process_pending_item(self, folder, item):
        if self.process is not None:
            return

        folder = Path(folder).expanduser().resolve()
        audio = folder / "recordings" / item["audio_name"]

        if not audio.exists():
            messagebox.showerror(
                "Audio introuvable",
                f"Le fichier audio n'existe plus :\n\n{audio}",
                parent=self,
            )
            return

        self.current_pending_context = (folder, item.get("id"))

        self.add_log(
            "\nTraitement de la file d'attente :\n"
            f"{item.get('title', 'Sans titre')}\n"
            f"Dossier : {folder.name}\n"
        )

        self.start_process(
            [
                "process-stored",
                str(audio),
                item.get("title", "Note vocale"),
            ],
            recording=False,
            workspace=folder,
            operation="process-pending",
        )

    def process_all_pending(self):
        if self.process is not None:
            return

        if not self.all_pending():
            return

        self.batch_mode = True
        self.add_log("\nTraitement automatique de toute la file d'attente.\n")
        self.process_next_batch()

    def process_next_batch(self):
        pending = self.all_pending()

        if not pending:
            self.batch_mode = False
            self.set_status("Terminé", "success")
            self.add_log("\n✓ Toute la file d'attente est terminée.\n")
            return

        folder, item = pending[0]
        self.process_pending_item(folder, item)

    # ========================================================
    # STATS / NOTES / DOSSIERS RÉCENTS
    # ========================================================

    def refresh_all(self):
        self.refresh_stats()
        self.refresh_notes()
        self.refresh_recent_folders()
        self.refresh_pending_sidebar()

    def refresh_folder_data(self):
        self.refresh_all()

    def refresh_stats(self):
        if self.workspace is None:
            self.recordings_count_var.set("0")
            self.notes_count_var.set("0")
            self.duration_var.set("00:00:00")
            return

        recordings_folder = self.workspace / "recordings"
        notes_folder = self.workspace / "notes"

        extensions = {
            ".wav", ".mp3", ".m4a", ".aac",
            ".flac", ".ogg", ".opus", ".mp4",
        }

        recordings = [
            p for p in recordings_folder.iterdir()
            if p.is_file() and p.suffix.lower() in extensions
        ]

        notes = list(notes_folder.glob("*.md"))

        self.recordings_count_var.set(str(len(recordings)))
        self.notes_count_var.set(str(len(notes)))

        total = sum(get_audio_duration(audio) for audio in recordings)
        self.duration_var.set(format_duration(total))

    def refresh_notes(self):
        for widget in self.notes_container.winfo_children():
            widget.destroy()

        if self.workspace is None:
            self.empty_notes()
            return

        notes_dir = self.workspace / "notes"

        notes = sorted(
            notes_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not notes:
            self.empty_notes()
            return

        for note in notes[:6]:
            self.note_row(note)

    def empty_notes(self):
        row = ctk.CTkFrame(self.notes_container, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=16)

        ctk.CTkLabel(
            row,
            text="▤",
            width=42,
            height=42,
            corner_radius=21,
            fg_color=CARD_2,
            text_color="#91A0B5",
            font=ctk.CTkFont(size=17),
        ).pack(side="left")

        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", padx=(14, 0))

        ctk.CTkLabel(
            text,
            text="Aucune note dans ce dossier",
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            text,
            text="Les prochaines notes apparaîtront ici.",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w")

    def note_row(self, note):
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
            fg_color=CARD_2,
            text_color="#91A0B5",
            font=ctk.CTkFont(size=16),
        ).pack(side="left", padx=(4, 12))

        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="both", expand=True, pady=8)

        ctk.CTkLabel(
            text,
            text=note.stem.replace("_", " "),
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        ).pack(anchor="w")

        modified = datetime.fromtimestamp(
            note.stat().st_mtime
        ).strftime("%d/%m/%Y à %H:%M")

        ctk.CTkLabel(
            text,
            text=modified,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=9),
        ).pack(anchor="w")

        ctk.CTkButton(
            row,
            text="Ouvrir",
            command=lambda p=note: open_with_system(p),
            width=82,
            height=32,
            corner_radius=9,
            fg_color=CARD_2,
            hover_color=BORDER_2,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT_SOFT,
        ).pack(side="right", padx=(8, 6))

    def refresh_recent_folders(self):
        for widget in self.recent_container.winfo_children():
            widget.destroy()

        paths = [
            Path(p)
            for p in self.recent_folders[:4]
            if Path(p).is_dir()
        ]

        if not paths:
            ctk.CTkLabel(
                self.recent_container,
                text="Aucun dossier récent.",
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=10),
            ).pack(anchor="w")
            return

        for folder in paths:
            active = (
                self.workspace is not None
                and folder.resolve() == self.workspace.resolve()
            )

            ctk.CTkButton(
                self.recent_container,
                text=("●  " if active else "□  ") + folder.name,
                command=lambda p=folder: self.select_workspace(p),
                height=36,
                corner_radius=10,
                anchor="w",
                fg_color=BLUE_DARK if active else CARD_2,
                hover_color=BLUE_SOFT if active else BORDER_2,
                border_width=1,
                border_color=BLUE if active else BORDER,
                text_color=TEXT_SOFT,
            ).pack(fill="x", pady=4)

    # ========================================================
    # JOURNAL
    # ========================================================

    def add_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ========================================================
    # PROCESSUS
    # ========================================================

    def make_environment(self, workspace=None):
        environment = os.environ.copy()
        target = workspace if workspace is not None else self.workspace
        environment["PLAUD_WORKSPACE"] = str(target)
        return environment

    def start_process(
        self,
        arguments,
        recording=False,
        workspace=None,
        operation=None,
    ):
        if self.process is not None:
            return

        target_workspace = Path(
            workspace if workspace is not None else self.workspace
        ).expanduser().resolve()

        command = [
            sys.executable,
            "-u",
            str(PLAUD_PY),
        ]
        command.extend(arguments)

        self.current_operation = operation
        self.current_workspace = target_workspace

        try:
            popen_options = {
                "cwd": str(APP_ROOT),
                "env": self.make_environment(target_workspace),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
            }

            if os.name == "nt":
                popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                command,
                **popen_options,
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
                border_color=RED,
                text_color=RED,
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

                stripped = line.strip()

                if stripped.startswith("PLAUD_RECORDED="):
                    self.output_queue.put(("recorded", stripped.split("=", 1)[1]))

                elif stripped.startswith("PLAUD_IMPORTED="):
                    self.output_queue.put(("imported", stripped.split("=", 1)[1]))

        finally:
            return_code = process.wait()
            self.output_queue.put(("finished", return_code))

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"

        self.new_folder_button.configure(state=state)
        self.library_button.configure(state=state)
        self.open_folder_button.configure(state=state)
        self.import_button.configure(state=state)
        self.title_entry.configure(state=state)
        self.process_all_button.configure(state=state)

        if self.recording_active:
            self.record_circle.configure(state="normal")
        elif busy:
            self.record_circle.configure(state="disabled")
        else:
            self.record_circle.configure(state="normal")

    # ========================================================
    # ENREGISTREMENT
    # ========================================================

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
            self.scroll_to(0.25)
            self.after(150, self.title_entry.focus_set)
            return

        self.pending_capture_title = title
        self.add_log("\nDémarrage de l'enregistrement...\n")

        self.start_process(
            ["record-only", title],
            recording=True,
            workspace=self.workspace,
            operation="record-only",
        )

    def stop_recording(self):
        if self.process is None or not self.recording_active:
            return

        self.recording_active = False
        self.processing_active = True

        self.record_circle.configure(
            state="disabled",
            text="…",
            fg_color=CARD_2,
            border_color=BORDER_2,
            text_color=TEXT_MUTED,
        )

        self.record_main_label.configure(text="Sauvegarde de l'audio")
        self.record_sub_label.configure(text="Ajout à la file d'attente")

        self.set_status("Sauvegarde", "processing")
        self.add_log("\nArrêt demandé. Sauvegarde de l'audio...\n")

        try:
            if os.name == "nt":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass

    # ========================================================
    # IMPORT
    # ========================================================

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

        self.pending_capture_title = title
        self.add_log(f"\nImport du fichier :\n{audio}\n")

        self.start_process(
            ["import-only", audio, title],
            recording=False,
            workspace=self.workspace,
            operation="import-only",
        )

    # ========================================================
    # FIN DE PROCESSUS
    # ========================================================

    def process_finished(self, return_code):
        operation = self.current_operation

        self.process = None
        self.recording_active = False
        self.processing_active = False
        self.recording_started_at = None

        self.timer_var.set("00:00:00")
        self.set_busy(False)

        self.record_circle.configure(
            text="●",
            fg_color=BLUE_DARK,
            hover_color=BLUE_SOFT,
            border_color=BLUE,
            text_color="#7590FF",
            state="normal",
        )

        self.record_main_label.configure(text="Démarrer l'enregistrement")
        self.record_sub_label.configure(text="Clique pour commencer")

        if return_code == 0:
            if operation in {"record-only", "import-only"}:
                self.set_status("En attente", "success")
                self.add_log(
                    "\n✓ Audio ajouté à la file d'attente. "
                    "Aucune transcription lancée.\n"
                )
                self.title_var.set("")

            elif operation == "process-pending":
                if self.current_pending_context:
                    folder, item_id = self.current_pending_context
                    self.remove_pending(folder, item_id)

                self.current_pending_context = None
                self.set_status("Terminé", "success")
                self.add_log("\n✓ Audio traité et retiré de la file d'attente.\n")

        else:
            self.set_status("Erreur", "error")
            self.add_log("\n✕ Une erreur est survenue.\n")

            if operation == "process-pending":
                self.batch_mode = False

            messagebox.showerror(
                "Erreur PLAUD",
                (
                    "Une erreur est survenue.\n\n"
                    "L'audio reste dans la file d'attente s'il n'a pas été traité."
                ),
                parent=self,
            )

        self.current_operation = None
        self.current_workspace = None
        self.refresh_all()

        if (
            return_code == 0
            and operation == "process-pending"
            and self.batch_mode
        ):
            self.after(300, self.process_next_batch)

    # ========================================================
    # STATUS / TIMER / QUEUE
    # ========================================================

    def set_status(self, text, kind):
        self.status_var.set(text)

        if kind == "recording":
            color = "#34171C"
            text_color = RED
        elif kind == "processing":
            color = "#302716"
            text_color = ORANGE
        elif kind == "success":
            color = "#112A21"
            text_color = GREEN
        elif kind == "error":
            color = "#34171C"
            text_color = RED
        else:
            color = CARD_2
            text_color = TEXT_SOFT

        self.status_badge.configure(
            fg_color=color,
            text_color=text_color,
        )

    def update_timer(self):
        if self.recording_active and self.recording_started_at:
            elapsed = time.monotonic() - self.recording_started_at
            self.timer_var.set(format_duration(elapsed))

        self.after(500, self.update_timer)

    def check_queue(self):
        try:
            while True:
                event = self.output_queue.get_nowait()
                kind = event[0]

                if kind == "text":
                    self.add_log(event[1])

                elif kind == "recorded":
                    if self.current_workspace:
                        self.add_pending(
                            self.current_workspace,
                            self.pending_capture_title,
                            event[1],
                            "recording",
                        )

                elif kind == "imported":
                    if self.current_workspace:
                        self.add_pending(
                            self.current_workspace,
                            self.pending_capture_title,
                            event[1],
                            "import",
                        )

                elif kind == "finished":
                    self.process_finished(event[1])

        except queue.Empty:
            pass

        self.after(100, self.check_queue)

    # ========================================================
    # FERMETURE
    # ========================================================

    def close_application(self):
        if self.process is not None:
            if self.recording_active:
                text = (
                    "Un enregistrement est encore en cours.\n\n"
                    "Arrête-le avant de fermer PLAUD."
                )
            else:
                text = (
                    "PLAUD travaille encore.\n\n"
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
