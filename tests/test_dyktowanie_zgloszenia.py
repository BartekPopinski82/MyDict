"""Kryptografia (wektory RFC) i zgłoszenia problemów — bez sieci."""

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

import dyktowanie_krypto as krypto  # noqa: E402
import dyktowanie_zgloszenia as reports  # noqa: E402
import zgloszenia as reader  # noqa: E402


# -- kryptografia -----------------------------------------------------------------


def test_ed25519_rfc8032_vector():
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = krypto.public_key(seed)
    assert public.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    signature = krypto.sign(seed, b"")
    assert signature.hex() == (
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
        "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert krypto.verify(public, b"", signature)
    assert not krypto.verify(public, b"x", signature)
    assert not krypto.verify_hex(public.hex(), b"", "zly-podpis")


def test_x25519_rfc7748_vectors():
    alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    bob = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    assert krypto.x25519_public(bob).hex() == "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
    shared = krypto.x25519(alice, krypto.x25519_public(bob))
    assert shared.hex() == "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"


def test_sealed_data_opens_only_with_right_key():
    private = bytes(range(32))
    sealed = krypto.seal(krypto.x25519_public(private), b"tajny dziennik")
    assert b"tajny" not in sealed
    assert krypto.unseal(private, sealed) == b"tajny dziennik"
    with pytest.raises(ValueError):
        krypto.unseal(bytes(32), sealed)
    with pytest.raises(ValueError):
        krypto.unseal(private, sealed[:-1] + bytes([sealed[-1] ^ 1]))


# -- zgłoszenia ---------------------------------------------------------------------


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("MYDICT_DATA", str(tmp_path))
    return tmp_path


def test_install_id_is_random_and_stable(data):
    first = reports.install_id()
    assert len(first) == 12 and first == reports.install_id()


def test_python_crash_is_kept_until_cleared(data):
    reports.record_crash("Traceback: ZeroDivisionError")
    assert "ZeroDivisionError" in reports.pending_crash()
    reports.clear_crash()
    assert reports.pending_crash() == ""


def test_native_crash_detected_only_after_unfinished_session(data):
    console = data / "konsola.log"
    console.write_text("stare wpisy\n", encoding="utf-8")
    reports.session_started()
    reports.session_ended()                      # zwykłe zamknięcie
    with open(console, "a", encoding="utf-8") as handle:
        handle.write("Windows fatal exception: access violation\n")
    reports.session_started()                    # brak znacznika = poprzednia sesja zamknięta
    assert reports.pending_crash() == ""
    with open(console, "a", encoding="utf-8") as handle:
        handle.write("Fatal Python error: Aborted\n  File cuda.py\n")
    reports.session_started()                    # znacznik został = awaria
    crash = reports.pending_crash()
    assert "Fatal Python error" in crash and "access violation" not in crash


def test_report_contains_description_and_log_tail(data):
    (data / "dyktowanie.log").write_text("x" * 300_000 + "OSTATNI WPIS\n", encoding="utf-8")
    text = reports.build_report("Nie wpisuje tekstu", "Jan", "Traceback")
    assert "Nie wpisuje tekstu" in text and "Kontakt: Jan" in text and "OSTATNI WPIS" in text
    assert len(text) < 260_000
    assert "dyktowanie.log" not in reports.build_report("opis", include_log=False)


def test_send_uploads_encrypted_file_that_reader_decrypts(data, monkeypatch):
    private = bytes(range(1, 33))
    monkeypatch.setattr(reports, "REPORTS_PUBLIC_KEY", krypto.x25519_public(private).hex())
    monkeypatch.setenv(reports.TOKEN_ENV, "token-testowy")
    seen = {}

    def put(url, body, headers):
        seen.update(url=url, body=json.loads(body), headers=headers)
        return 201

    report = reports.build_report("Opis błędu", "Ola Nowak")
    path = reports.send(report, "awaria", put=put)
    assert seen["headers"]["Authorization"] == "Bearer token-testowy"
    assert seen["url"].endswith(path) and path.startswith(f"zgloszenia/{reports.install_id()}/")
    payload = base64.b64decode(seen["body"]["content"])
    assert b"Opis" not in payload

    clone = data / "klon"
    (clone / path).parent.mkdir(parents=True)
    (clone / path).write_bytes(payload)
    assert reader.read_all(clone, private, clone / "odczytane") == (1, 0)
    files = list((clone / "odczytane").glob("*/*.txt"))
    assert files[0].parent.name.endswith("Ola_Nowak")
    assert "Opis błędu" in files[0].read_text(encoding="utf-8")
    assert reader.read_all(clone, private, clone / "odczytane") == (0, 0)   # bez powtórek


def test_without_token_only_saving_to_file_works(data, monkeypatch):
    monkeypatch.delenv(reports.TOKEN_ENV, raising=False)
    monkeypatch.setattr(reports, "resource_dir", lambda: data)
    assert not reports.can_send()
    with pytest.raises(reports.ReportError):
        reports.send("x", put=lambda *a: pytest.fail("bez tokenu nie wolno wysyłać"))
    assert reports.save_to_file("treść").read_text(encoding="utf-8") == "treść"


def test_server_rejection_is_reported(data, monkeypatch):
    import urllib.error

    monkeypatch.setenv(reports.TOKEN_ENV, "t")

    def put(url, body, headers):
        raise urllib.error.HTTPError(url, 401, "Bad credentials", {}, None)

    with pytest.raises(reports.ReportError, match="401"):
        reports.send("x", put=put)
