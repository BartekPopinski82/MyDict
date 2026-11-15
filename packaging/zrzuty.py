"""Zrzuty ekranu interfejsu bez uruchamiania silnika rozpoznawania mowy.

Służy do oglądania zmian w wyglądzie i do obrazków w README:

    python packaging/zrzuty.py [katalog_wyjściowy]

Silnik, mikrofon, skrót i zasobnik są zastąpione atrapami, więc skrypt działa
także na maszynie budującej (QT_QPA_PLATFORM=offscreen).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Zawsze osobny katalog danych: zrzuty nie mogą dotknąć prawdziwych ustawień użytkownika.
os.environ["MYDICT_DATA"] = tempfile.mkdtemp(prefix="mydict-zrzuty-")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dyktowanie_motyw as motyw  # noqa: E402
import dyktowanie_ui as ui  # noqa: E402
from dyktowanie_aktualizacje import Asset, Release  # noqa: E402
from dyktowanie_asr import ModelInfo  # noqa: E402


class FakeEngine:
    """Atrapa silnika: niczego nie ładuje ani nie nagrywa."""

    def __init__(self, callbacks):
        self.callbacks = callbacks
        self.listening = False
        self.level = 0.0
        from dyktowanie_asr import LiveSettings

        self.settings = LiveSettings()

    def start(self):
        pass

    def load(self, key, device, resolved=None):
        info = ModelInfo(key, "large-v3-turbo", "cuda", "int8_float16", "Silero")
        QTimer.singleShot(0, lambda: self.callbacks.ready(info))

    def start_listening(self, _device=None):
        self.listening = True
        self.callbacks.listening(True)

    def stop_listening(self):
        self.listening = False
        self.callbacks.listening(False)

    def switch_microphone(self, _device):
        pass

    def shutdown(self):
        pass


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "zrzuty"
    out.mkdir(parents=True, exist_ok=True)
    ui.DictationEngine = FakeEngine
    ui.HotkeyListener.start = lambda self: True
    ui.HotkeyListener.stop = lambda self: None
    ui.Microphone.devices = staticmethod(lambda: ["Mikrofon (USB Audio)", "Zestaw słuchawkowy"])
    ui.gpu_acceleration_state = lambda: "ready"
    ui.sprzet.detect = lambda: ui.sprzet.Hardware(("NVIDIA GeForce RTX 4060",), "nvidia", True, 8192)
    ui.foreground_window = lambda: None   # zrzut nie może pokazać tytułu prywatnego okna
    ui.updates.cleanup = lambda: None
    ui.MainWindow._build_tray = lambda self: setattr(self, "tray", None)
    ui.MainWindow._auto_check_updates = lambda self: None

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(motyw.app_font())
    release = Release(
        "2.1.0",
        "## Co nowego\n\n- Szybsze uruchamianie modelu Turbo\n- Nowy tryb ciemny\n- Poprawki wpisywania w przeglądarkach",
        "https://github.com/", (Asset("MyDict-2.1.0-Windows-Setup.exe", "https://github.com/x", 150_000_000),),
    )

    for mode in ("light", "dark"):
        motyw.set_mode(app, mode)
        window = ui.MainWindow()
        window.theme_combo.setCurrentIndex(window.theme_combo.findData(mode))
        window.resize(1080, 740)
        window.show()
        app.processEvents()
        window.session.final("Dzień dobry, to jest próbka dyktowania na żywo. ")
        window.session.final("Tekst pojawia się w oknie, w którym aktualnie piszesz, a tutaj widać jego zapis.")
        window._render_transcript()
        window.target_label.setText("Pisanie do: Word — Notatka ze spotkania.docx")
        for _ in range(5):
            app.processEvents()
        window.grab().save(str(out / f"okno-{mode}.png"))

        window.pending_release = release
        window.update_banner.set_message("Dostępna jest wersja 2.1.0", "Masz wersję 2.0.0. Aktualizacja zajmie chwilę, ustawienia zostają bez zmian.")
        window.update_banner.setVisible(True)
        window.gpu_banner.set_message(
            "Przygotowuję przyspieszenie kartą graficzną (jednorazowo)",
            "Pobrano 420 MB z 1,0 GB. Możesz już dyktować — na razie nieco wolniej.",
        )
        window.gpu_banner.set_progress(420, 1020)
        window.gpu_button.setVisible(False)
        window.gpu_later.setText("Anuluj")
        window.gpu_banner.setVisible(True)
        window.engine.start_listening()
        window._update_controls()
        for _ in range(5):
            app.processEvents()
        window.grab().save(str(out / f"okno-{mode}-banery.png"))

        dialog = ui.UpdateDialog(release, window)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(out / f"aktualizacja-{mode}.png"))
        dialog.close()

        keys = ui.ApiKeysDialog(window.api, None, window)
        keys.show()
        app.processEvents()
        keys.grab().save(str(out / f"klucze-{mode}.png"))
        keys.close()

        import dyktowanie_przygotowanie as przygotowanie

        setup = przygotowanie.SetupWindow(
            [("gpu", "Obsługa karty graficznej (ok. 1 GB)"), ("model:turbo", "Model rozpoznawania mowy (1,6 GB)")],
            ui.sprzet.detect(),
        )
        setup.task.start = lambda work: None          # zrzut bez pobierania
        setup.show()
        setup._next()
        setup._on_progress(420 * 1024 ** 2, 1020 * 1024 ** 2)
        app.processEvents()
        setup.grab().save(str(out / f"instalacja-{mode}.png"))
        setup.done(0)

        overlay = window.overlay
        overlay.set_text("To jest tekst zatwierdzony, ", "a to jeszcze się zmienia")
        overlay.status.setText("Słucham")
        overlay.target.setText("→ Word — Notatka ze spotkania.docx")
        overlay.show()
        app.processEvents()
        overlay.grab().save(str(out / f"pasek-{mode}.png"))
        window.engine.stop_listening()
        overlay.hide()
        window.hide()
        window.level_timer.stop()
        window.window_timer.stop()
    sys.stdout.write(f"Zapisano zrzuty w {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
