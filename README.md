# MyDict

Dyktowanie na żywo dla Windows i macOS. Program słucha mikrofonu, pokazuje transkrypcję w trakcie
mówienia i wpisuje tekst tam, gdzie akurat piszesz — w Wordzie, przeglądarce (Gmail, Google Docs,
formularze), poczcie, komunikatorze, notatniku.

![Okno programu](docs/zrzuty/okno-light.png)

- **Rozpoznawanie na Twoim komputerze** (Whisper, bez internetu) albo **w chmurze** (Groq, OpenAI,
  ElevenLabs, Deepgram, AssemblyAI, własny serwer zgodny z OpenAI).
- **Tekst na żywo** — pływający pasek pokazuje słowa w trakcie mówienia, do okna trafiają tylko pewne.
- **Globalny skrót** — start/stop albo „przytrzymaj i mów”, w dowolnej aplikacji.
- **Automatyczne aktualizacje** z wydań GitHub.
- Motyw jasny i ciemny.

## Instalacja

Pobierz najnowsze wydanie ze strony **[Releases](../../releases/latest)**.

### Windows 10 / 11

1. Pobierz `MyDict-<wersja>-Windows-Setup.exe` i uruchom.
2. Instalator nie wymaga uprawnień administratora. Windows może pokazać ostrzeżenie SmartScreen
   („Nieznany wydawca”) — wybierz **Więcej informacji → Uruchom mimo to**.

### macOS 12 lub nowszy (Apple Silicon)

1. Pobierz `MyDict-<wersja>-macOS-arm64.dmg`, otwórz i przeciągnij aplikację do **Programy**.
2. Pierwsze uruchomienie: kliknij aplikację prawym przyciskiem → **Otwórz** (aplikacja nie jest
   jeszcze notaryzowana przez Apple). Gdy system odmówi, wejdź w **Ustawienia systemowe →
   Prywatność i ochrona** i wybierz **Otwórz mimo to**.
3. Zezwól na **Mikrofon**, a w banerze programu kliknij **Otwórz ustawienia** i włącz MyDict
   na liście **Dostępność** — bez tej zgody macOS nie pozwala wpisywać tekstu do innych aplikacji.

### Pierwsze uruchomienie

Model **Turbo** (ok. 1,6 GB) pobiera się jednorazowo. Program działa od razu na procesorze.
Jeśli masz kartę **NVIDIA**, pojawi się propozycja pobrania bibliotek przyspieszających (ok. 1 GB,
jednorazowo) — rozpoznawanie jest wtedy kilka razy szybsze.

## Jak dyktować

- Kliknij w miejsce, gdzie ma się pojawić tekst.
- Naciśnij skrót (Windows: **Ctrl+Alt+Spacja**, macOS: **Ctrl+Option+Spacja**) albo mikrofon
  na pływającym pasku i mów.
- Pasek pokazuje tekst na bieżąco: **biały = pewny**, **szary = jeszcze się zmienia**. Do okna
  trafiają tylko słowa pewne — zwykle ok. 1 s za mową.
- Krótka pauza kończy wypowiedź: model robi dokładniejszy przebieg i domyka zdanie.
- Ten sam skrót kończy dyktowanie.

Tryb **„Przytrzymaj i mów”**: trzymasz skrót, mówisz, puszczasz. Najlepiej z klawiszem bez
modyfikatorów (F8, F9).

Przycisk klawiatury na pasku wstrzymuje wpisywanie — tekst zostaje wtedy tylko w podglądzie.

![Pływający pasek](docs/zrzuty/pasek-light.png)

## Aktualizacje

Program raz dziennie sprawdza, czy jest nowsze wydanie (można to wyłączyć w ustawieniach albo
sprawdzić ręcznie: **Sprawdź aktualizacje** na dole okna). Po kliknięciu **Zainstaluj i uruchom
ponownie** pobiera plik, sprawdza jego sumę SHA-256, instaluje i wraca do pracy. Ustawienia, klucze
API i pobrane modele zostają bez zmian.

Na macOS, dopóki aplikacja nie jest podpisana certyfikatem Apple, po aktualizacji system może
ponownie poprosić o zgodę **Dostępność**.

## Modele w chmurze (API)

Na liście **Model** poniżej modeli lokalnych są modele w chmurze.

- **Klucze API…** — wklejasz klucz usługi; „Sprawdź” testuje go bez wysyłania dźwięku. Klucze są
  szyfrowane kontem Windows (DPAPI), a na macOS trafiają do pęku kluczy. Klucze ze zmiennych
  środowiskowych (`OPENAI_API_KEY`, `GROQ_API_KEY`, …) i z pliku `secrets.env` w katalogu danych
  programu są używane automatycznie.
- **Własny model…** — podajesz rodzaj API, adres, nazwę modelu i klucz. Pozwala użyć nowego modelu
  albo własnego serwera zgodnego z OpenAI.

Model w chmurze dostaje **całą wypowiedź po pauzie** (plik WAV 16 kHz), więc tekst pojawia się po
pauzie, a nie słowo po słowie. Wyjątkiem jest Deepgram w trybie strumieniowym. Każda wypowiedź jest
płatna według cennika dostawcy.

### Plik `dostawcy.json`

Adresy API, lista modeli i parametry zapytań są w pliku `dostawcy.json`. Aby je zmienić, skopiuj
plik do katalogu danych programu (patrz niżej) i edytuj kopię — ma pierwszeństwo przed wersją
dołączoną do programu i przetrwa aktualizację.

* `params` — parametry dopisywane do zapytania (np. `smart_format`, `punctuate`); model może mieć własne.
* `stream: true` (Deepgram) — dźwięk płynie na bieżąco przez WebSocket, tekst pojawia się w trakcie
  mówienia, a koniec wypowiedzi wykrywa serwer.
* `streaming_params` — `interim_results` (wyniki pośrednie) i `endpointing` (ms ciszy kończące
  wypowiedź, domyślnie 200). `local_finalize_ms` (domyślnie 400) działa po stronie programu: po tylu ms
  ciszy program wysyła `Finalize`, więc tekst jest zatwierdzany od razu. `0` wyłącza.
* W trybie strumieniowym Deepgram nalicza opłatę za cały czas połączenia, także za ciszę.

## Dźwięk komputera zamiast mikrofonu (Windows)

Na liście źródeł dźwięku są pozycje **„🔊 Dźwięk komputera: …”** — program słucha wtedy tego, co gra
komputer (WASAPI loopback). Przydatne do testów na nagraniu i do transkrypcji rozmów.
„Wszystkie wyjścia (automatycznie)” bierze dźwięk z tego wyjścia, na którym akurat coś gra.

## Gdzie są dane

| | Windows | macOS |
|---|---|---|
| Dziennik, pomiary | `%LOCALAPPDATA%\MyDict` | `~/Library/Logs/MyDict` |
| Składniki, własna konfiguracja | `%LOCALAPPDATA%\MyDict` | `~/Library/Application Support/MyDict` |
| Modele Whisper | `%USERPROFILE%\.cache\huggingface` | `~/.cache/huggingface` |

Przycisk **Otwórz folder dziennika** na dole okna prowadzi wprost do dziennika `dyktowanie.log`.
Plik `ustawienia_asr.json` (np. `{"final_beam": 5}`) skopiowany do katalogu danych zmienia ustawienia
dekodera.

## Ograniczenia

- Windows nie pozwala wpisywać do programów uruchomionych **jako administrator**, jeśli
  MyDict nie działa tak samo — pasek pokaże ostrzeżenie.
- macOS: program zna nazwę aktywnej aplikacji, ale nie tytuł okna; dźwięk komputera (loopback)
  i przyspieszenie GPU nie są dostępne — model liczy procesor.
- Interpunkcję i wielkie litery dodaje model.

## Dla programistów

```
python -m venv .venv
.venv\Scripts\activate            # macOS: source .venv/bin/activate
pip install -r requirements-build.txt
python MyDict.py         # uruchomienie ze źródeł
python -m pytest tests            # testy
python packaging/build.py         # aplikacja + instalator dla bieżącego systemu
```

| Plik | Rola |
|---|---|
| `dyktowanie_core.py` | strumieniowanie: wykrywanie mowy (Silero VAD), LocalAgreement, filtr halucynacji |
| `dyktowanie_asr.py` | model faster-whisper, mikrofon, dźwięk komputera, wątek roboczy |
| `dyktowanie_api.py`, `dyktowanie_stream.py` | modele w chmurze (HTTP i WebSocket), klucze |
| `dyktowanie_windows.py`, `dyktowanie_macos.py` | aktywne okno, wpisywanie, globalny skrót; wybór w `dyktowanie_platforma.py` |
| `dyktowanie_ui.py`, `dyktowanie_api_ui.py`, `dyktowanie_motyw.py` | interfejs i motywy |
| `dyktowanie_aktualizacje.py`, `dyktowanie_pobieranie.py` | aktualizacje z GitHuba, pobieranie z kontrolą sumy |
| `dyktowanie_gpu_pakiet.py` | biblioteki NVIDIA pobierane na żądanie |
| `dyktowanie_app.py` | wersja, nazwy, katalogi |
| `packaging/` | PyInstaller, Inno Setup, skrypty wydań |

- Jak wydać nową wersję: [docs/wydawanie.md](docs/wydawanie.md).
- Pomiar opóźnień (`[LAT #N]`, `latencja.csv`): [docs/pomiar-latencji.md](docs/pomiar-latencji.md).
- Pomysły sprawdzone i odrzucone (nie powtarzać): [docs/eksperymenty.md](docs/eksperymenty.md).
