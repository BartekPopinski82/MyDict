"""Aktualizacje z GitHuba, pobieranie plików i pakiet GPU — bez sieci."""

import hashlib
import io
import json
import sys
import threading
import urllib.error
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_aktualizacje as upd  # noqa: E402
import dyktowanie_gpu_pakiet as gpu_pack  # noqa: E402
import dyktowanie_krypto as krypto  # noqa: E402
import dyktowanie_pobieranie as dl  # noqa: E402


# -- wersje -------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("v2.1.0", (2, 1, 0)), ("2.0", (2, 0)), (" V3.4.5-beta1", (3, 4, 5)), ("", ()), ("nightly", ()),
])
def test_parse_version(text, expected):
    assert upd.parse_version(text) == expected


@pytest.mark.parametrize("candidate,current,newer", [
    ("2.0.1", "2.0.0", True), ("v2.1", "2.0.9", True), ("2.0.0", "2.0.0", False),
    ("2.0", "2.0.0", False), ("1.9.9", "2.0.0", False), ("2.10.0", "2.9.0", True),
    ("śmieci", "2.0.0", False),
])
def test_is_newer(candidate, current, newer):
    assert upd.is_newer(candidate, current) is newer


# -- wydania --------------------------------------------------------------------

RELEASE_JSON = {
    "tag_name": "v2.1.0",
    "body": "Zmiany",
    "html_url": "https://github.com/x/y/releases/tag/v2.1.0",
    "assets": [
        {"name": "MyDict-2.1.0-Windows-Setup.exe", "size": 10,
         "browser_download_url": "https://github.com/x/y/releases/download/v2.1.0/setup.exe",
         "digest": "sha256:" + "ab" * 32},
        {"name": "MyDict-2.1.0-macOS-arm64.zip", "size": 20,
         "browser_download_url": "https://github.com/x/y/releases/download/v2.1.0/mac.zip"},
        {"name": "SHA256SUMS.txt", "size": 1,
         "browser_download_url": "https://github.com/x/y/releases/download/v2.1.0/SHA256SUMS.txt"},
    ],
}


def test_release_parsing_and_asset_choice():
    release = upd.release_from_json(RELEASE_JSON)
    assert release.version == "2.1.0"
    windows = upd.pick_asset(release, upd.platform_asset_suffix("win32", "AMD64"))
    assert windows.name.endswith("Windows-Setup.exe") and windows.sha256 == "ab" * 32
    assert upd.pick_asset(release, upd.platform_asset_suffix("darwin", "arm64")).name.endswith("macOS-arm64.zip")
    assert upd.pick_asset(release, upd.platform_asset_suffix("darwin", "x86_64")) is None
    assert upd.pick_asset(release, upd.platform_asset_suffix("linux", "x86_64")) is None


def test_check_returns_only_newer_release():
    assert upd.check("x/y", "2.0.0", fetch=lambda url, headers=None: RELEASE_JSON).version == "2.1.0"
    assert upd.check("x/y", "2.1.0", fetch=lambda url, headers=None: RELEASE_JSON) is None


def test_check_reports_network_problem_as_update_error():
    def broken(url, headers=None):
        raise dl.DownloadError("brak sieci")

    with pytest.raises(upd.UpdateError):
        upd.check("x/y", "2.0.0", fetch=broken)


def test_checksum_from_sums_file_when_digest_missing():
    release = upd.release_from_json(RELEASE_JSON)
    mac = upd.pick_asset(release, "-macOS-arm64.zip")
    sums = f"{'cd' * 32}  MyDict-2.1.0-macOS-arm64.zip\n{'ef' * 32} *inny.exe\nto nie jest suma\n"
    assert upd.expected_sha256(release, mac, lambda url: sums, public_key="") == "cd" * 32
    assert upd.parse_checksums(sums)["inny.exe"] == "ef" * 32


# -- podpis wydania -------------------------------------------------------------

SEED = bytes(range(32))
PUBLIC = krypto.public_key(SEED).hex()
SUMS = f"{'ab' * 32}  MyDict-2.1.0-Windows-Setup.exe\n{'cd' * 32}  MyDict-2.1.0-macOS-arm64.zip\n"


def signed_release(sums=SUMS, signature=None):
    assets = list(RELEASE_JSON["assets"]) + [
        {"name": "SHA256SUMS.txt.sig", "size": 1,
         "browser_download_url": "https://github.com/x/y/releases/download/v2.1.0/SHA256SUMS.txt.sig"}]
    files = {"SHA256SUMS.txt": sums,
             "SHA256SUMS.txt.sig": signature if signature is not None else krypto.sign(SEED, sums.encode()).hex()}
    return upd.release_from_json(dict(RELEASE_JSON, assets=assets)), lambda url: files[url.rsplit("/", 1)[1]]


def test_signed_sums_are_accepted():
    release, fetch = signed_release()
    mac = upd.pick_asset(release, "-macOS-arm64.zip")
    assert upd.expected_sha256(release, mac, fetch, public_key=PUBLIC) == "cd" * 32


def test_bad_signature_is_refused():
    release, fetch = signed_release(signature=krypto.sign(bytes(32), SUMS.encode()).hex())
    with pytest.raises(upd.UpdateError, match="Podpis wydania"):
        upd.expected_sha256(release, upd.pick_asset(release, "-macOS-arm64.zip"), fetch, public_key=PUBLIC)


def test_tampered_sums_are_refused():
    release, fetch = signed_release()
    original = fetch

    def tampered(url):
        text = original(url)
        return text.replace("cd" * 32, "00" * 32) if url.endswith(".txt") else text

    with pytest.raises(upd.UpdateError, match="Podpis wydania"):
        upd.expected_sha256(release, upd.pick_asset(release, "-macOS-arm64.zip"), tampered, public_key=PUBLIC)


def test_unsigned_release_is_refused_when_key_is_built_in():
    release = upd.release_from_json(RELEASE_JSON)
    with pytest.raises(upd.UpdateError, match="nie jest podpisane"):
        upd.expected_sha256(release, upd.pick_asset(release, "-Windows-Setup.exe"), lambda url: "", public_key=PUBLIC)


def test_github_digest_must_match_signed_sum():
    release, fetch = signed_release(sums=SUMS.replace("ab" * 32, "11" * 32))
    with pytest.raises(upd.UpdateError, match="nie zgadza się"):
        upd.expected_sha256(release, upd.pick_asset(release, "-Windows-Setup.exe"), fetch, public_key=PUBLIC)


def test_release_tool_signature_is_accepted_by_updater(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
    import wydanie

    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(SUMS, encoding="utf-8", newline="\n")
    wydanie.code_constant = lambda name: ""          # klucz testowy zamiast klucza z programu
    signature = wydanie.sign_file(sums, SEED.hex()).read_text(encoding="ascii")
    assert krypto.verify_hex(PUBLIC, sums.read_bytes(), signature)


def test_built_in_release_key_is_valid():
    assert len(bytes.fromhex(upd.RELEASE_PUBLIC_KEY)) == 32


def test_update_without_checksum_is_refused(monkeypatch, tmp_path):
    data = dict(RELEASE_JSON, assets=[dict(RELEASE_JSON["assets"][1])])
    release = upd.release_from_json(data)
    monkeypatch.setattr(upd, "RELEASE_PUBLIC_KEY", "")
    monkeypatch.setattr(upd, "platform_asset_suffix", lambda *a: "-macOS-arm64.zip")
    monkeypatch.setattr(upd, "updates_dir", lambda: tmp_path)
    with pytest.raises(upd.UpdateError, match="sumy kontrolnej"):
        upd.fetch_update(release, fetch_file=lambda *a, **k: pytest.fail("nie wolno pobierać"))


def test_update_from_untrusted_host_is_refused(monkeypatch, tmp_path):
    asset = dict(RELEASE_JSON["assets"][0], browser_download_url="https://example.com/setup.exe")
    release = upd.release_from_json(dict(RELEASE_JSON, assets=[asset]))
    monkeypatch.setattr(upd, "platform_asset_suffix", lambda *a: "-Windows-Setup.exe")
    monkeypatch.setattr(upd, "updates_dir", lambda: tmp_path)
    with pytest.raises(upd.UpdateError, match="spoza GitHuba"):
        upd.fetch_update(release, fetch_file=lambda *a, **k: pytest.fail("nie wolno pobierać"))


def test_fetch_update_passes_checksum_and_cleans_old_files(monkeypatch, tmp_path):
    release, fetch = signed_release()
    monkeypatch.setattr(upd, "RELEASE_PUBLIC_KEY", PUBLIC)
    monkeypatch.setattr(upd, "platform_asset_suffix", lambda *a: "-Windows-Setup.exe")
    monkeypatch.setattr(upd, "updates_dir", lambda: tmp_path)
    (tmp_path / "stara-wersja.exe").write_bytes(b"x")
    seen = {}

    def fake(url, destination, *, sha256, progress, cancel):
        seen.update(url=url, sha256=sha256, destination=destination)
        return destination

    path = upd.fetch_update(release, fetch_file=fake, fetch_text_fn=fetch)
    assert path.name == "MyDict-2.1.0-Windows-Setup.exe"
    assert seen["sha256"] == "ab" * 32
    assert not (tmp_path / "stara-wersja.exe").exists()


def test_source_checkout_cannot_self_install():
    ok, reason = upd.can_self_install()
    assert ok is False and reason


# -- pobieranie -------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200, length: int | None = None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def opener_for(payload: bytes, calls: list):
    def opener(request, timeout=None):
        calls.append(dict(request.headers))
        start = 0
        header = request.headers.get("Range")
        if header:
            start = int(header.split("=")[1].rstrip("-"))
            if start >= len(payload):
                raise urllib.error.HTTPError(request.full_url, 416, "Range", {}, None)
            return FakeResponse(payload[start:], 206)
        return FakeResponse(payload)
    return opener


def test_download_verifies_checksum_and_reports_progress(tmp_path):
    payload = b"dane" * 1000
    seen = []
    target = dl.download("https://github.com/plik", tmp_path / "plik.bin",
                         sha256=hashlib.sha256(payload).hexdigest(),
                         progress=lambda done, total: seen.append((done, total)),
                         opener=opener_for(payload, []))
    assert target.read_bytes() == payload
    assert seen[-1] == (len(payload), len(payload))
    assert not (tmp_path / "plik.bin.part").exists()


def test_download_rejects_corrupted_file(tmp_path):
    with pytest.raises(dl.DownloadError, match="uszkodzony"):
        dl.download("https://github.com/plik", tmp_path / "plik.bin", sha256="00" * 32,
                    opener=opener_for(b"cos innego", []))
    assert not (tmp_path / "plik.bin").exists()
    assert not (tmp_path / "plik.bin.part").exists()


def test_download_resumes_partial_file(tmp_path):
    payload = bytes(range(256)) * 40
    (tmp_path / "plik.bin.part").write_bytes(payload[:3000])
    calls = []
    target = dl.download("https://github.com/plik", tmp_path / "plik.bin",
                         sha256=hashlib.sha256(payload).hexdigest(), opener=opener_for(payload, calls))
    assert calls[0]["Range"] == "bytes=3000-"
    assert target.read_bytes() == payload


def test_download_detects_truncated_transfer(tmp_path):
    def opener(request, timeout=None):
        return FakeResponse(b"abc", length=10)

    with pytest.raises(dl.DownloadError, match="zerwane"):
        dl.download("https://github.com/plik", tmp_path / "plik.bin", opener=opener)
    assert not (tmp_path / "plik.bin").exists()


def test_download_can_be_cancelled_and_keeps_part(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(dl.Cancelled):
        dl.download("https://github.com/plik", tmp_path / "plik.bin", cancel=cancel,
                    opener=opener_for(b"x" * 10, []))
    assert not (tmp_path / "plik.bin").exists()


def test_download_refuses_plain_http(tmp_path):
    with pytest.raises(dl.DownloadError, match="HTTPS"):
        dl.download("http://github.com/plik", tmp_path / "plik.bin")


def test_existing_verified_file_is_not_downloaded_again(tmp_path):
    payload = b"gotowe"
    (tmp_path / "plik.bin").write_bytes(payload)
    result = dl.download("https://github.com/plik", tmp_path / "plik.bin",
                         sha256=hashlib.sha256(payload).hexdigest(),
                         opener=lambda *a, **k: pytest.fail("nie wolno pobierać"))
    assert result.read_bytes() == payload


# -- pakiet GPU ---------------------------------------------------------------------


def _wheel(path: Path, files: dict) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path.read_bytes()


def test_gpu_pack_install_extracts_only_libraries(tmp_path):
    source = tmp_path / "zrodlo"
    source.mkdir()
    wheels = {
        "nvidia-cublas-cu12": _wheel(source / "cublas.whl", {
            "nvidia/cublas/bin/cublas64_12.dll": b"A", "nvidia/cublas/include/cublas.h": b"h"}),
        "nvidia-cudnn-cu12": _wheel(source / "cudnn.whl", {
            "nvidia/cudnn/bin/cudnn64_9.dll": b"B", "nvidia/cudnn/bin/cudnn_ops64_9.dll": b"C",
            "nvidia/cudnn/lib/x64/cudnn.lib": b"lib"}),
    }

    def fetch(url, headers=None):
        name = url.split("/")[4]
        data = wheels[name]
        return {"urls": [
            {"packagetype": "sdist", "filename": f"{name}.tar.gz"},
            {"packagetype": "bdist_wheel", "filename": f"{name}-linux_x86_64.whl", "url": "https://x/l", "size": 1},
            {"packagetype": "bdist_wheel", "filename": f"{name}-py3-none-win_amd64.whl",
             "url": f"https://files.pythonhosted.org/{name}.whl", "size": len(data),
             "digests": {"sha256": hashlib.sha256(data).hexdigest()}},
        ]}

    def fetch_file(url, destination, *, sha256, progress, cancel):
        data = wheels[url.rsplit("/", 1)[1][:-4]]
        assert hashlib.sha256(data).hexdigest() == sha256
        destination.write_bytes(data)
        progress(len(data), len(data))
        return destination

    target = tmp_path / "gpu"
    progress = []
    assert not gpu_pack.installed(target)
    gpu_pack.install(lambda done, total: progress.append((done, total)),
                     directory=target, fetch=fetch, fetch_file=fetch_file)
    assert sorted(p.name for p in target.glob("*.dll")) == ["cublas64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll"]
    assert not (target / "pobieranie").exists()
    assert gpu_pack.installed(target)
    total = sum(len(data) for data in wheels.values())
    assert progress[-1] == (total, total)
    assert json.loads((target / gpu_pack.MARKER).read_text())["packages"] == dict(gpu_pack.PACKAGES)


def test_gpu_pack_not_installed_when_marker_is_from_other_versions(tmp_path):
    (tmp_path / "cudnn64_9.dll").write_bytes(b"x")
    (tmp_path / gpu_pack.MARKER).write_text(json.dumps({"packages": {"nvidia-cublas-cu12": "0"}}))
    assert not gpu_pack.installed(tmp_path)


def test_gpu_pack_missing_windows_wheel_is_reported(tmp_path):
    with pytest.raises(dl.DownloadError, match="Windows"):
        gpu_pack.install(directory=tmp_path, fetch=lambda url, headers=None: {"urls": []})
