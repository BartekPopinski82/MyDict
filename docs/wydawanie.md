# Wydawanie nowej wersji

Program aktualizuje się z wydań (Releases) repozytorium wskazanego w `dyktowanie_app.py`
(`GITHUB_REPO`). Wydanie buduje się samo na GitHub Actions po wypchnięciu znacznika wersji.

> Ręczne kroki do wykonania przed pierwszym wydaniem (sekrety, klucze, certyfikaty) —
> lista z odhaczaniem w [DEVOPS.md](DEVOPS.md).

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
   - instaluje biblioteki z `requirements-lock.txt` (dokładne wersje i sumy),
   - uruchamia testy,
   - buduje aplikację na Windows i macOS (z podpisem kodu, jeśli skonfigurowany) i uruchamia jej
     autotest (`--selftest`),
   - robi instalator Windows (Inno Setup) oraz ZIP i DMG dla macOS,
   - **testuje instalator** tak, jak dostanie go użytkownik (`packaging/test_instalatora.py`):
     cicha instalacja, autotest zainstalowanego programu, odinstalowanie,
   - podpisuje `SHA256SUMS.txt` kluczem wydań i publikuje wydanie jako **wersję wstępną**.
5. Pobierz instalator z wersji wstępnej i sprawdź go na czystym komputerze. Zainstalowane programy
   jeszcze go nie widzą.
6. Udostępnij: **Actions → Udostępnij wydanie → Run workflow**, znacznik `v2.1.0`. Od tej chwili
   zainstalowane kopie zobaczą aktualizację (sprawdzają raz dziennie albo po kliknięciu
   **Sprawdź aktualizacje**).

Gdy coś pójdzie źle po udostępnieniu: oznacz wydanie z powrotem jako „pre-release” (program bierze
tylko najnowsze pełne wydanie) — ci, którzy jeszcze nie zaktualizowali, zostaną na poprzedniej
wersji. Popraw i wydaj kolejny numer. Nie podmieniaj plików w istniejącym wydaniu.

## Jednorazowa konfiguracja repozytorium

**Settings → Secrets and variables → Actions**:

| Rodzaj | Nazwa | Wartość |
|---|---|---|
| sekret | `MYDICT_KLUCZ_WYDAN` | zawartość pliku `~/.mydict-klucze/klucz-wydan` — **wymagany**, bez niego wydanie nie wyjdzie |
| sekret | `MYDICT_ZGLOSZENIA_TOKEN` | token zapisu zgłoszeń (niżej) — bez niego program tylko zapisuje zgłoszenia do pliku |
| zmienne/sekrety | `AZURE_*`, `WYMAGAJ_PODPISU_KODU` | podpis kodu Windows (niżej) |

## Klucze — kopia zapasowa obowiązkowa

`python packaging/wydanie.py nowe-klucze <katalog>` tworzy dwa klucze prywatne (obecne leżą
w `~/.mydict-klucze/`, poza repozytorium) i wypisuje klucze publiczne wpisane w `dyktowanie_app.py`.

- `klucz-wydan` — podpis wydań. **Utrata** = żadna zainstalowana kopia nie przyjmie już
  automatycznej aktualizacji; użytkownicy musieliby ręcznie pobrać wersję z nowym kluczem.
  **Wyciek** = ktoś z dostępem do wydań mógłby podsunąć fałszywą aktualizację. Trzymaj kopię
  offline (np. menedżer haseł + pendrive), nigdy w repozytorium.
- `klucz-zgloszen` — odczyt zgłoszeń. Utrata = nie odczytasz zgłoszeń wysłanych do tej pory.

Ręczna kontrola podpisu: `python packaging/wydanie.py sprawdz-podpis dist/SHA256SUMS.txt`.

## Zgłoszenia problemów

1. Utwórz **prywatne** repozytorium `MyDict-zgloszenia` (nazwa w `REPORTS_REPO`, `dyktowanie_app.py`).
2. Token: GitHub → Settings → Developer settings → Fine-grained tokens → dostęp **tylko** do tego
   repozytorium, uprawnienie **Contents: Read and write**, najdłuższy dostępny termin ważności.
   Wpisz go jako sekret `MYDICT_ZGLOSZENIA_TOKEN`. Token trafia do programu i da się go wydobyć —
   dlatego zgłoszenia są szyfrowane, a token nie może nic poza tym jednym repozytorium.
   Po wygaśnięciu tokenu starsze wersje przestają wysyłać (proponują zapis do pliku) — odnów go
   przed terminem i wydaj nową wersję.
3. Odczyt: sklonuj repozytorium i uruchom
   `python packaging/zgloszenia.py <folder klonu>` — w `odczytane/<instalacja>[-<kontakt>]/`
   powstają pliki `.txt`. Dopisz `odczytane/` do `.gitignore` klonu.

Przy setkach użytkowników ten sposób wystarcza. Gdyby zgłoszeń było bardzo dużo albo pojawił się
spam, następnym krokiem jest mały serwer pośredniczący, który trzyma token u siebie.

## Licencje

`LICENSE.txt` — licencja programu (freeware, także komercyjnie, bez otwartego kodu); instalator
pokazuje ją przed instalacją. `THIRD-PARTY-NOTICES.txt` generuje `packaging/licencje.py` przy
każdym budowaniu (lista bibliotek różni się między systemami), dlatego nie ma go w repozytorium.
Brakuje pełnego tekstu LGPL-3.0 (Qt/PySide6, FFmpeg): połóż go jako `packaging/licencje/LGPL-3.0.txt`
(https://www.gnu.org/licenses/lgpl-3.0.txt), a generator go dołączy. Do tego czasu ostrzega przy budowaniu.

## Biblioteki

`requirements-lock.txt` przypina wszystkie biblioteki (z zależnościami) z sumami SHA-256, wspólnie
dla Windows i macOS. Po zmianie `requirements*.txt` albo przy aktualizacji bibliotek:

```
uv pip compile requirements-build.txt --universal --python-version 3.12 --generate-hashes --no-header -o requirements-lock.txt
```

Akcje GitHub są przypięte do commitów; Dependabot (`.github/dependabot.yml`) co tydzień proponuje
aktualizacje akcji i bibliotek.

## Nazwy plików — umowa z aktualizatorem

Program szuka w wydaniu pliku o końcówce odpowiedniej dla systemu (`dyktowanie_aktualizacje.py`):

| System | Plik |
|---|---|
| Windows | `MyDict-<wersja>-Windows-Setup.exe` |
| macOS Apple Silicon | `MyDict-<wersja>-macOS-arm64.zip` |
| macOS Intel (jeśli kiedyś budowany) | `MyDict-<wersja>-macOS-x64.zip` |

Plik `.dmg` służy tylko do pierwszej instalacji. Aktualizator instaluje wyłącznie plik, którego suma
SHA-256 zgadza się z **podpisanym** `SHA256SUMS.txt` (podpis w `SHA256SUMS.txt.sig`, klucz publiczny
`RELEASE_PUBLIC_KEY`) i z polem `digest` z GitHuba. Wydanie bez podpisu albo ze złym podpisem jest
odrzucane — chroni to także przed przejęciem konta GitHub.

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

## Podpis kodu

**Windows** — mechanizm jest gotowy, brakuje certyfikatu. Bez niego SmartScreen ostrzega przy
instalacji, a antywirusy częściej mylą program z zagrożeniem.

- `packaging/build.py` podpisuje `MyDict.exe`, drugi silnik, instalator i deinstalator, gdy
  zmienna `MYDICT_SIGN_CMD` zawiera polecenie podpisu z `{plik}` w miejscu ścieżki, np.
  `signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /n "BAPO" {plik}`.
  Potem sprawdza podpisy (Authenticode) i przerywa, gdy któryś jest zły.
- Ustaw zmienną repozytorium `WYMAGAJ_PODPISU_KODU=true`, gdy podpis działa — wtedy wydanie
  Windows nie wyjdzie bez podpisu.
- **Wybór certyfikatu** (od 2023 klucz musi być w sprzęcie albo w chmurze, zwykły plik .pfx odpada):
  - *Azure Trusted Signing* — najtańszy (ok. 10 USD/mies.), workflow ma gotowy krok
    (zmienne `AZURE_SIGNING_ENDPOINT/ACCOUNT/PROFILE`, sekrety `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET`).
    **Ale:** dla osób fizycznych tylko USA i Kanada; w UE wyłącznie firmy (jednoosobowa działalność
    jest traktowana jak osoba fizyczna). Dostępne, jeśli program wydaje np. spółka.
  - *Certyfikat OV w chmurze* (np. SSL.com eSigner, Certum, Sectigo) — dostępny dla osoby
    fizycznej i JDG z Polski; polecenie podpisu dostawcy wpisuje się jako sekret i krok ustawiający
    `MYDICT_SIGN_CMD`. Do podpisywania w GitHub Actions wybierz dostawcę z podpisem bez telefonu
    (np. eSigner z kluczem TOTP); Certum SimplySign wymaga zatwierdzania na telefonie.
- Reputacja SmartScreen rośnie z liczbą pobrań podpisanego programu — pierwsze dni po zmianie
  certyfikatu ostrzeżenie może jeszcze się pojawiać. Fałszywe alarmy antywirusa zgłasza się
  na https://www.microsoft.com/wdsi/filesubmission.

**macOS** wymaga „Otwórz mimo to” przy pierwszym uruchomieniu, a zgoda „Dostępność” bywa kasowana
  po aktualizacji (system wiąże ją z podpisem). Rozwiązanie: konto Apple Developer, podpis
  „Developer ID Application” (zmienna `MACOS_SIGN_IDENTITY` jest już obsługiwana przy budowaniu)
  i notaryzacja (`xcrun notarytool`).
