import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dyktowanie_komendy import COMMANDS, format_dictation
from dyktowanie_core import SessionText


@pytest.mark.parametrize("phrase,value", COMMANDS.items())
def test_every_command(phrase, value):
    assert format_dictation(phrase) == value[0]


@pytest.mark.parametrize("raw,expected", [
    ("Uwaga dwukropek to ważne wykrzyknik", "Uwaga: to ważne!"),
    ("Zakupy dwukropek nowa linia myślnik mleko nowa linia myślnik chleb",
     "Zakupy:\n- mleko\n- chleb"),
    ("Akapit nowy akapit Dalej", "Akapit\n\nDalej"),
    ("otwórz nawias uwaga zamknij nawias kropka", "(uwaga)."),
    ("otwórz nawias kwadratowy test zamknij nawias kwadratowy", "[test]"),
    ("otwórz cudzysłów Ala zamknij cudzysłów przecinek tak", "„Ala”, tak"),
    ("biało łącznik czerwony", "biało-czerwony"),
    ("jan małpa firma kropka pl", "jan@firma. pl"),
    ("plik podkreślnik test ukośnik plik", "plik_test/plik"),
    ("dwa plus dwa równa się cztery", "dwa + dwa = cztery"),
    ("Uwaga dwukropek. nowa linia. myślnik, mleko kropka.", "Uwaga:\n- mleko."),
    ("DWUKROPEK", ":"),
    ("srednik myslnik nowa   linia", "; -\n"),
    ("dosłownie dwukropek", "dwukropek"),
    ("dosłownie otwórz nawias kwadratowy", "otwórz nawias kwadratowy"),
    ("To jest dosłownie prawda.", "To jest dosłownie prawda."),
    ("przecinek przecinek", ",,"),
    ("Kropkami i przecinkami, zwykłe zdanie.", "Kropkami i przecinkami, zwykłe zdanie."),
])
def test_examples(raw, expected):
    assert format_dictation(raw) == expected


@pytest.mark.parametrize("raw", [
    "Uwaga dwukropek to ważne wykrzyknik",
    "Zakupy dwukropek nowa linia myślnik mleko",
    "otwórz nawias kwadratowy test zamknij nawias kwadratowy",
    "dosłownie dwukropek to nazwa",
    "Zwykłe kropkami malowane zdanie.",
    "otwórz cudzysłów Ala zamknij cudzysłów przecinek tak",
])
def test_streaming_every_character_is_append_only(raw):
    session = SessionText()
    before = ""
    for char in raw:
        session.partial(char, "")
        stable, tentative = session.split()
        assert stable.startswith(before), (before, stable)
        assert session.full() == stable + tentative
        before = stable
    session.final("")
    assert session.full().startswith(before)
    assert session.full() == format_dictation(raw)


def test_commands_across_events_and_utterances():
    session = SessionText()
    session.final("Zakupy")
    session.partial(" dwu", "kropek")
    assert session.stable() == "Zakupy"
    assert session.full() == "Zakupy:"
    session.final("kropek")
    session.final(" nowa linia")
    session.final(" myślnik mleko")
    assert session.full() == "Zakupy:\n- mleko"


def test_open_bracket_across_utterances():
    session = SessionText()
    for phrase in ["otwórz nawias", "tekst", "zamknij nawias", "kropka"]:
        session.final(phrase)
    assert session.full() == "(tekst)."


@pytest.mark.parametrize("command,expected", [("łącznik", "biało-czerwony"),
                                             ("myślnik", "biało - czerwony")])
def test_hyphen_spacing_across_utterances(command, expected):
    session = SessionText()
    for phrase in ["biało", command, "czerwony"]:
        session.final(phrase)
    assert session.full() == expected


def test_unfinished_command_becomes_text_on_stop():
    session = SessionText()
    session.partial("To nowa", "")
    assert session.stable() == "To"
    session.final("")
    assert session.full() == "To nowa"
