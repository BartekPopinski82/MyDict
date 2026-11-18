"""Deinstalacja programu z poziomu okna Pomoc.

Windows: deinstalator Inno Setup (unins000.exe w folderze programu) — usuwa program,
skróty z pulpitu i menu Start, autostart, ustawienia, dane z pobranymi składnikami
oraz modele mowy (packaging/windows/installer.iss).
macOS: skrypt deinstalacja/Odinstaluj-MyDict.command dołączony do pakietu .app.

Program nie może usunąć sam siebie, więc deinstalacja rusza dopiero po jego zamknięciu.
Dostępna tylko w zainstalowanej kopii — nigdy przy uruchomieniu ze źródeł.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dyktowanie_app import APP_NAME, FROZEN, IS_MAC, IS_WINDOWS, resource_dir


def _windows_uninstaller() -> Path:
    return Path(sys.executable).resolve().parent / "unins000.exe"


def _mac_bundle() -> Path | None:
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _mac_script() -> Path:
    return resource_dir() / "deinstalacja" / "Odinstaluj-MyDict.command"


def can_uninstall() -> bool:
    if not FROZEN:
        return False
    if IS_WINDOWS:
        return _windows_uninstaller().is_file()
    if IS_MAC:
        bundle = _mac_bundle()
        return bundle is not None and bundle.name == f"{APP_NAME}.app" and _mac_script().is_file()
    return False


def _hf_hub_cache() -> str:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        return str(HF_HUB_CACHE)
    except Exception:
        home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
        return os.environ.get("HF_HUB_CACHE") or str(Path(home) / "hub")


def start_uninstall() -> None:
    """Uruchamia deinstalację niezależnie od programu; program powinien się zaraz zamknąć."""
    pid = str(os.getpid())
    if IS_WINDOWS:
        # Czekamy na zamknięcie programu, potem deinstalator Inno (/SILENT: bez pytania,
        # z paskiem postępu — użytkownik potwierdził już w programie).
        uninstaller = str(_windows_uninstaller()).replace("'", "''")
        script = (
            f"Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue; "
            f"Start-Process -FilePath '{uninstaller}' -ArgumentList '/SILENT','/NORESTART'"
        )
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                         creationflags=flags, close_fds=True)
        return
    command = ["/bin/bash", str(_mac_script()), "--app", str(_mac_bundle()),
               "--hf-cache", _hf_hub_cache(), "--wait-pid", pid]
    subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
