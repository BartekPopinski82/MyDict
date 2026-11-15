# Wydawanie nowej wersji

Program aktualizuje się z wydań (Releases) repozytorium wskazanego w `dyktowanie_app.py`
(`GITHUB_REPO`). Wydanie buduje się samo na GitHub Actions po wypchnięciu znacznika wersji.

## Krok po kroku

1. Zmień numer w `dyktowanie_app.py`: `__version__ = "2.1.0"`.
2. Dopisz sekcję `## 2.1.0` na górze `CHANGELOG.md` — jej treść zobaczy użytkownik w oknie
   aktualizacji, więc pisz dla niego (co nowego, co naprawione).
3. Zatwierdź zmiany, oznacz i wypchnij:

   ```
   git commit -am "Wersja 2.1.0"
   git tag v2.1.0
   git push origin main v2.1.0
   ```

4. Workflow **Wydanie** (`.github/workflows/wydanie.yml`):
   - sprawdza, że znacznik zgadza się z `__version__`,
   - uruchamia testy,
   - buduje aplikację na Windows i macOS i uruchamia jej autotest (`--selftest`),
   - robi instalator Windows (Inno Setup) oraz ZIP i DMG dla macOS,
   - publikuje wydanie z plikami i `SHA256SUMS.txt`.
5. Od tej chwili zainstalowane kopie programu zobaczą aktualizację (sprawdzają raz dziennie albo
   po kliknięciu **Sprawdź aktualizacje**).

Gdy coś pójdzie źle po publikacji: usuń wydanie na GitHubie (albo oznacz jako „pre-release” —
program bierze tylko najnowsze pełne wydanie), popraw i wydaj kolejny numer. Nie podmieniaj plików
w istniejącym wydaniu.

## Nazwy plików — umowa z aktualizatorem

Program szuka w wydaniu pliku o końcówce odpowiedniej dla systemu (`dyktowanie_aktualizacje.py`):

| System | Plik |
|---|---|
| Windows | `MyDict-<wersja>-Windows-Setup.exe` |
| macOS Apple Silicon | `MyDict-<wersja>-macOS-arm64.zip` |
| macOS Intel (jeśli kiedyś budowany) | `MyDict-<wersja>-macOS-x64.zip` |

Plik `.dmg` służy tylko do pierwszej instalacji. Aktualizator instaluje wyłącznie plik, którego suma
SHA-256 zgadza się z wydaniem (pole `digest` z GitHuba albo `SHA256SUMS.txt`).

Repozytorium z wydaniami musi być **publiczne** — program pobiera aktualizacje bez logowania.

## Budowanie lokalne

```
pip install -r requirements-build.txt
python packaging/build.py
```

Drugi silnik (whisper.cpp) buduje `python packaging/build_whispercpp.py` — wynik trafia do folderu
`whispercpp/` (poza repozytorium), skąd zabiera go `build.py`. Wymaga CMake; na Windows także
Visual Studio 2022 (C++) i Vulkan SDK ze zmienną `VULKAN_SDK`. Wersja whisper.cpp jest przypięta
w skrypcie — po jej zmianie trzeba ponownie sprawdzić dyktowanie (czasy słów, powtórzenia).

Przełączniki diagnostyczne (zmienne środowiskowe) do testów na jednym komputerze:
`MYDICT_DATA` — osobny katalog danych i ustawień (niezależna kopia programu),
`MYDICT_BEZ_CUDA=1` — zachowuj się jak komputer bez karty NVIDIA,
`MYDICT_VULKAN_DEVICE=N` — użyj karty Vulkan o numerze N,
`MYDICT_SILNIK=whispercpp` — wymuś drugi silnik.

Windows wymaga [Inno Setup 6](https://jrsoftware.org/isinfo.php) (polecenie `iscc`); bez niego
powstaje sama aplikacja w `dist/MyDict/`. Ikony z logo: `python packaging/make_icons.py`.
Zrzuty ekranu do README: `python packaging/zrzuty.py`.

## Podpisywanie (do zrobienia, gdy będą certyfikaty)

Wydania nie są podpisane, dlatego:

- **Windows** pokazuje ostrzeżenie SmartScreen przy instalacji. Rozwiązanie: certyfikat do podpisu
  kodu (np. Azure Trusted Signing) i podpisanie `MyDict.exe` oraz instalatora w workflow.
- **macOS** wymaga „Otwórz mimo to” przy pierwszym uruchomieniu, a zgoda „Dostępność” bywa kasowana
  po aktualizacji (system wiąże ją z podpisem). Rozwiązanie: konto Apple Developer, podpis
  „Developer ID Application” (zmienna `MACOS_SIGN_IDENTITY` jest już obsługiwana przy budowaniu)
  i notaryzacja (`xcrun notarytool`).
