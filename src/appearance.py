"""Appearance mode management for ColdStack.

Stores the user's light/dark/system preference in a plaintext JSON file
(appearance.json) next to the app. This file is read BEFORE the vault is
unlocked, so the login screen renders in the correct mode.

Version: v5.2.1 (August 2026) - Carved out of gui_main_v5.py
"""
import json
import os
import sys
from typing import Tuple


def _get_appearance_dir() -> str:
    """Return the directory where appearance.json should be stored.

    - Frozen EXE (PyInstaller): use the EXE's directory (sys.executable)
    - Script mode: use the project root (parent of src/)
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # Script mode: __file__ is src/appearance.py, project root is parent of src/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_appearance_path() -> str:
    """Return the full path to appearance.json."""
    return os.path.join(_get_appearance_dir(), "appearance.json")


def _get_fallback_path() -> str:
    """Return the fallback path in the user's home directory."""
    return os.path.expanduser("~/.coldstack_appearance.json")


def load_appearance_mode() -> str:
    """Read the saved appearance mode from the plaintext JSON file.

    Returns "dark", "light", or "system". Defaults to "dark" if the file
    doesn't exist or is unreadable.
    """
    candidates = [
        _get_appearance_path(),
        _get_fallback_path(),
    ]
    for path in candidates:
        try:
            with open(path, "r") as f:
                data = json.load(f)
                mode = data.get("appearance_mode", "dark")
                if mode in ("dark", "light", "system"):
                    return mode
        except (IOError, json.JSONDecodeError):
            continue
    return "dark"


def save_appearance_mode(mode: str) -> Tuple[bool, str]:
    """Write the appearance mode to the plaintext JSON file.

    Returns (success, path_or_error_message).
    """
    if mode not in ("dark", "light", "system"):
        mode = "dark"

    path = _get_appearance_path()
    try:
        with open(path, "w") as f:
            json.dump({"appearance_mode": mode}, f)
        return (True, path)
    except IOError:
        # Fallback: try user home
        fallback = _get_fallback_path()
        try:
            with open(fallback, "w") as f:
                json.dump({"appearance_mode": mode}, f)
            return (True, fallback)
        except IOError as e2:
            return (False, f"Could not write to {path} or {fallback}: {e2}")


def style_combobox(style_name: str = "Dark.TCombobox") -> None:
    """Apply theme-aware styling to a ttk Combobox.

    Adapts colors to the current CTk appearance mode.
    Does NOT call style.theme_use() — that overrides the global ttk theme
    and breaks CTkTabview rendering.
    """
    import tkinter.ttk as ttk
    import customtkinter as ctk

    mode = ctk.get_appearance_mode().lower()
    if mode == "light":
        bg = "#f0f0f0"
        field_bg = "#ffffff"
        fg = "#1a1a1a"
        border = "#cccccc"
        active_bg = "#e0e0e0"
    else:
        bg = "#3b3b3b"
        field_bg = "#2b2b2b"
        fg = "#dce4ee"
        border = "#565b73"
        active_bg = "#4a4f63"
    style = ttk.Style()
    style.configure(style_name,
                    fieldbackground=field_bg,
                    background=bg,
                    foreground=fg,
                    arrowcolor=fg,
                    bordercolor=border,
                    lightcolor=border,
                    darkcolor=border,
                    font=("Segoe UI", 13),
                    padding=6)
    style.map(style_name,
              fieldbackground=[("readonly", field_bg)],
              foreground=[("readonly", fg)],
              background=[("active", active_bg)])
