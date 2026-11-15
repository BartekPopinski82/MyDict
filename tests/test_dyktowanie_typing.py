import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dyktowanie_windows import OWN_PID, TypingEngine, WindowInfo  # noqa: E402


class FakeDesktop:
    """Atrapa pulpitu: zapamiętuje, co trafiło do którego okna."""

    def __init__(self):
        self.active = WindowInfo(1, 111, "Dokument1 - Word", "WINWORD.EXE", "OpusApp")
        self.documents = {}
        self.modifiers = False

    def window(self):
        return self.active

    def inject(self, text):
        self.documents[self.active.hwnd] = self.documents.get(self.active.hwnd, "") + text
        return True


def make_engine(desktop, smooth=True):
    engine = TypingEngine(inject=desktop.inject, window_provider=desktop.window,
                          modifiers=lambda: desktop.modifiers)
    engine.smooth = smooth
    return engine


def drain(engine, limit=500):
    for _ in range(limit):
        if not engine.step() and engine.pending_chars == 0:
            return
    raise AssertionError("wpisywanie nie zakończyło się")


def test_types_growing_text_char_by_char():
    desktop = FakeDesktop()
    engine = make_engine(desktop)
    engine.set_text("Ala ma")
    assert engine.step()
    assert desktop.documents[1] == "A"  # płynne pisanie: pojedyncze znaki
    drain(engine)
    engine.set_text("Ala ma kota, a kot ma Alę.")
    drain(engine)
    assert desktop.documents[1] == "Ala ma kota, a kot ma Alę."


def test_waits_for_own_window_and_modifiers():
    desktop = FakeDesktop()
    engine = make_engine(desktop, smooth=False)
    own = WindowInfo(9, OWN_PID, "BAPO-dyktowanie", "python.exe", "Qt")
    desktop.active = own
    engine.set_text("Tekst")
    assert not engine.step()
    desktop.active = WindowInfo(1, 111, "Dokument1 - Word", "WINWORD.EXE", "OpusApp")
    desktop.modifiers = True
    assert not engine.step()
    desktop.modifiers = False
    assert engine.step()
    assert desktop.documents == {1: "Tekst"}


def test_window_switch_continues_in_new_window():
    desktop = FakeDesktop()
    engine = make_engine(desktop, smooth=False)
    engine.set_text("Pierwsze.")
    drain(engine)
    desktop.active = WindowInfo(2, 222, "Gmail - Chrome", "chrome.exe", "Chrome_WidgetWin_1")
    engine.set_text("Pierwsze. Drugie.")
    drain(engine)
    assert desktop.documents == {1: "Pierwsze.", 2: " Drugie."}


def test_pause_skips_text_and_reset_starts_new_session():
    desktop = FakeDesktop()
    engine = make_engine(desktop, smooth=False)
    engine.enabled = False
    engine.set_text("Nie wpisuj.")
    engine.step()
    engine.enabled = True
    engine.set_text("Nie wpisuj. Wpisz.")
    drain(engine)
    assert desktop.documents == {1: " Wpisz."}
    engine.reset()
    engine.set_text("Nowa sesja")
    drain(engine)
    assert desktop.documents[1] == " Wpisz.Nowa sesja"


def test_polish_and_long_backlog_speeds_up():
    desktop = FakeDesktop()
    engine = make_engine(desktop)
    text = "Zażółć gęślą jaźń. " * 10
    engine.set_text(text)
    steps = 0
    while engine.pending_chars:
        engine.step()
        steps += 1
    assert desktop.documents[1] == text
    assert steps < len(text) / 2  # przy zaległościach porcje rosną
