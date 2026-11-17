# -*- mode: python ; coding: utf-8 -*-
"""Przepis PyInstallera: jedna definicja dla Windows i macOS.

    pyinstaller packaging/MyDict.spec --noconfirm

Wynik: ``dist/MyDict/`` (Windows) albo ``dist/MyDict.app`` (macOS).
Zwykle uruchamiany przez ``packaging/build.py``, który robi też instalator.
"""

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 — SPECPATH podaje PyInstaller
APP_NAME = "MyDict"
APP_ID = "pl.bapo.mydict"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "dyktowanie_app.py").read_text(encoding="utf-8")).group(1)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

datas = [
    (str(ROOT / "dostawcy.json"), "."),
    (str(ROOT / "ustawienia_asr.json"), "."),
    (str(ROOT / "zasoby" / "logo.svg"), "zasoby"),
    (str(ROOT / "LICENSE.txt"), "."),
    (str(ROOT / "THIRD-PARTY-NOTICES.txt"), "."),
]
# Token zapisu zgłoszeń (sekret CI) — bez niego program tylko zapisuje zgłoszenia do pliku.
_token = os.environ.get("MYDICT_ZGLOSZENIA_TOKEN", "").strip()
if _token:
    _token_file = Path(workpath) / "zgloszenia.token"  # noqa: F821 — workpath podaje PyInstaller
    _token_file.parent.mkdir(parents=True, exist_ok=True)
    _token_file.write_text(_token, encoding="utf-8")
    datas.append((str(_token_file), "."))
datas += collect_data_files("faster_whisper")        # model wykrywania mowy (Silero VAD)
binaries = collect_dynamic_libs("ctranslate2")
# Drugi silnik (whisper.cpp z Vulkan / Metal) — budowany przez packaging/build_whispercpp.py.
whispercpp = ROOT / "whispercpp"
if whispercpp.is_dir():
    for item in whispercpp.iterdir():
        if item.suffix.lower() in (".txt", ".md"):
            datas.append((str(item), "whispercpp"))
        elif item.is_file():
            binaries.append((str(item), "whispercpp"))
hiddenimports = collect_submodules("websockets")
if IS_MAC:
    hiddenimports += collect_submodules("keyring.backends")

# Pakiety wykluczone z programu (packaging/wykluczenia.py): nie są dołączane, a zaślepka
# w sys.modules pozwala zależnościom je zaimportować; dopiero użycie zgłasza czytelny błąd.
_wykluczenia = {}
exec((ROOT / "packaging" / "wykluczenia.py").read_text(encoding="utf-8"), _wykluczenia)
EXCLUDED_PACKAGES = _wykluczenia["EXCLUDED_PACKAGES"]
_stub_hook = Path(workpath) / "zaslepki_wykluczonych.py"  # noqa: F821 — workpath podaje PyInstaller
_stub_hook.parent.mkdir(parents=True, exist_ok=True)
_stub_hook.write_text(f'''
import sys
import types

class _Zaslepka(types.ModuleType):
    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        raise RuntimeError(f"{{self.__name__}}: {{self._powod}}")

for _nazwa, _powod in {EXCLUDED_PACKAGES!r}.items():
    _modul = _Zaslepka(_nazwa)
    _modul._powod = _powod
    sys.modules.setdefault(_nazwa, _modul)
''', encoding="utf-8")

analysis = Analysis(  # noqa: F821
    [str(ROOT / "MyDict.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "torch", "torchaudio", "PIL", "pytest", "IPython", "matplotlib", "pandas", "scipy",
              *EXCLUDED_PACKAGES],
    runtime_hooks=[str(_stub_hook)],
    noarchive=False,
)
pyz = PYZ(analysis.pure)  # noqa: F821

version_file = None
if IS_WIN:
    numbers = tuple(int(part) for part in (VERSION.split(".") + ["0"] * 4)[:4])
    version_file = str(Path(workpath) / "wersja.txt")  # noqa: F821 — workpath podaje PyInstaller
    Path(version_file).parent.mkdir(parents=True, exist_ok=True)
    Path(version_file).write_text(f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041504B0', [
      StringStruct('CompanyName', 'BAPO'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [0x0415, 1200])])
  ]
)
""", encoding="utf-8")

exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    upx=False,
    icon=str(ROOT / "zasoby" / ("ikona.icns" if IS_MAC else "ikona.ico")),
    version=version_file,
    codesign_identity=os.environ.get("MACOS_SIGN_IDENTITY") or None,
    entitlements_file=str(ROOT / "packaging" / "macos" / "entitlements.plist") if IS_MAC else None,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, upx=False, name=APP_NAME)  # noqa: F821

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ROOT / "zasoby" / "ikona.icns"),
        bundle_identifier=APP_ID,
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription":
                "MyDict słucha mikrofonu, aby zamieniać mowę na tekst.",
            "NSAppleEventsUsageDescription":
                "MyDict wpisuje podyktowany tekst do aktywnej aplikacji.",
            "NSHumanReadableCopyright": "© 2025-2026 BAPO. Freeware — zob. LICENSE.txt",
        },
    )
