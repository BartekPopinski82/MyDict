# Plan testów przed publikacją

Stan na 2026-11-15, wersja 2.0.0. „Sprawdzone” = wykonane i potwierdzone w dzienniku programu;
„Do zrobienia” = jeszcze niewykonane, z opisem, jak to zrobić i po czym poznać wynik.

## 1. Sprawdzone automatycznie

| Co | Jak | Wynik |
|---|---|---|
| Logika programu | `python -m pytest tests` | 157 testów przechodzi |
| Zbudowana aplikacja ma komplet bibliotek | `MyDict.exe --selftest` | OK |
| Instalator Windows | instalacja `/SILENT` na Windows 11 | 159 s razem z pobraniem bibliotek NVIDIA; skróty i deinstalator są |
| Pobieranie składników w trakcie instalacji | dziennik: „Kończenie instalacji: ['gpu']” | biblioteki NVIDIA pobrane (1,5 GB na dysku) |
| Pierwsze uruchomienie po instalacji | dziennik | model Turbo na karcie (`cuda/int8_float16`) po 5,8 s |
| Cicha aktualizacja | instalator z `/SILENT /AKTUALIZACJA` przy działającym programie | stary proces zamknięty, nowy uruchomiony, model gotowy po ok. 25 s |
| Przeniesienie ustawień ze starej nazwy | rejestr `HKCU\Software\BAPO\MyDict` | 16 wartości przeniesionych |
| Rozpoznanie kart | dziennik „Sprzęt: …” | wykryte AMD Radeon (zintegrowana) i GeForce GTX 1650 Ti |

Sprzęt testowy: laptop z GTX 1650 Ti (4 GB) i zintegrowaną kartą AMD, Windows 11.

## 2. Do zrobienia ręcznie — Windows

Każdy punkt: wykonaj, zapisz wynik (działa / nie działa / uwagi) i dołącz `dyktowanie.log`
(przycisk **Otwórz folder dziennika**).

1. **Dyktowanie głosem** — Notatnik, Word, Gmail w Chrome, Teams. Powiedz 5 zdań z polskimi znakami
   i liczbami. Oczekiwane: tekst w oknie ok. 1 s za mową, bez zgubionych słów.
2. **Skrót** — start/stop i „przytrzymaj i mów” (Ctrl+Alt+Spacja oraz F8), gdy aktywne jest inne okno.
3. **Pływający pasek** — nie zabiera fokusu po kliknięciu; da się go przesunąć; pozycja zostaje po restarcie.
4. **Motyw** — przełączenie Jasny / Ciemny / Jak w systemie w trakcie pracy.
5. **Instalacja „z kreatorem”** — dwuklik w `Setup.exe` (dotąd testowany tylko tryb cichy): strony
   kreatora po polsku, okno „Kończenie instalacji”, pole „Uruchom MyDict”.
6. **Instalacja bez internetu** — odłącz sieć, zainstaluj. Oczekiwane: instalacja się kończy,
   okno informuje o braku połączenia, a program po podłączeniu sieci dokańcza pobieranie sam.
7. **Odinstalowanie** — Ustawienia → Aplikacje → MyDict → Odinstaluj; folder programu znika.
8. **Czysty komputer** — instalacja na Windows 10/11 bez Pythona i bez wcześniejszych modeli
   (pobierze się też model, ok. 1,6 GB). To jedyny sposób, by potwierdzić, że niczego nie brakuje.

## 3. Do zrobienia — inny sprzęt

Nie mamy tych konfiguracji; potrzebni testerzy. Dla każdej: czas instalacji, wybrany model,
opóźnienie tekstu względem mowy (kolumny w `latencja.csv`), czy tekst nadąża.

| Sprzęt | Oczekiwane zachowanie | Co zmierzyć |
|---|---|---|
| Tylko karta **Intel** (typowy laptop biurowy) | model Lekki na procesorze, baner z wyjaśnieniem | czy Lekki nadąża za mową; jakość tekstu |
| Tylko karta **AMD** | jak wyżej | jak wyżej |
| **NVIDIA RTX** (8 GB i więcej) | biblioteki NVIDIA + Turbo | opóźnienie; czy warto proponować model Najdokładniejszy |
| NVIDIA ze starym sterownikiem | komunikat o sterowniku, praca na procesorze | czy komunikat jest zrozumiały |
| NVIDIA z 2 GB pamięci | model Lekki na karcie | czy mieści się w pamięci |

**Decyzja do podjęcia po tych testach:** jeśli model Lekki na procesorze okaże się za wolny albo za
mało dokładny, karty AMD i Intel wymagają drugiego silnika rozpoznawania (np. whisper.cpp z Vulkan
albo ONNX Runtime z DirectML). To osobny, duży etap — obecny silnik (CTranslate2) liczy na karcie
tylko przez NVIDIA CUDA.

## 4. Do zrobienia — macOS

Kod dla macOS jest napisany, ale **nie był uruchamiany na Macu**. Kolejność:

1. Zbudować aplikację (GitHub Actions albo `python packaging/build.py` na Macu) — autotest musi przejść.
2. Otworzyć DMG, przeciągnąć do Programy, uruchomić („Otwórz mimo to”).
3. Okno „Kończenie instalacji” pobiera model Lekki.
4. Zgody: Mikrofon oraz Dostępność (baner w programie otwiera właściwy panel).
5. Wpisywanie tekstu do Notatek, Pages, Safari, Worda — polskie znaki.
6. Skrót Ctrl+Option+Spacja: start/stop i „przytrzymaj i mów”.
7. Pasek: widoczny nad innymi aplikacjami, kliknięcie nie zabiera fokusu.
8. Klucz API zapisany i odczytany po restarcie (pęk kluczy).
9. Aktualizacja: zainstalować wersję N, wydać N+1, sprawdzić podmianę aplikacji i ponowne uruchomienie.

## 5. Do zrobienia — modele w chmurze

Żaden model w chmurze nie był mierzony w tej wersji. Dopóki nie ma wyników, w programie i w README
są oznaczone jako „w trakcie testów” i nie są nikomu polecane jako sposób na przyspieszenie.

Założenia: użytkownik sam zakłada konto i tworzy klucz; program nie zawiera żadnego klucza.

| Usługa | Co sprawdzić |
|---|---|
| Groq (Whisper large-v3 turbo, large-v3) | czas od końca wypowiedzi do tekstu; **limity planu darmowego**: po ilu wypowiedziach pojawia się błąd 429 i jaki komunikat widzi użytkownik |
| OpenAI (GPT-4o Transcribe, mini, Whisper) | czas odpowiedzi, jakość polskiego, koszt godziny dyktowania |
| ElevenLabs Scribe v2 | jak wyżej |
| Deepgram Nova-3 (strumieniowo) | opóźnienie słów na żywo; koszt — opłata za cały czas połączenia, także za ciszę |
| AssemblyAI Universal-2 | opóźnienie (usługa kolejkowa — spodziewane kilka sekund) |

Dla każdej usługi: 20 wypowiedzi po 5–15 s, mediana i najgorszy czas z `latencja.csv`, liczba błędów,
zachowanie przy braku internetu i przy błędnym kluczu. Porównać z modelem lokalnym na tej samej
karcie — chmura może być **wolniejsza**, bo tekst pojawia się dopiero po pauzie i dochodzi czas sieci.

Błąd limitu (HTTP 429) ma już własny komunikat („przekroczono limit zapytań albo skończyły się
środki”) — do potwierdzenia na prawdziwym koncie, czy pojawia się na pasku w zrozumiałej formie.

## 6. Przed publikacją

- [ ] Punkty 2.1–2.8 wykonane na co najmniej jednym czystym komputerze.
- [ ] Co najmniej jeden test na sprzęcie bez karty NVIDIA (punkt 3).
- [ ] Decyzja: wydajemy macOS razem z Windows czy później (punkt 4).
- [ ] Ustalona nazwa konta/repozytorium GitHub — wpisać w `dyktowanie_app.py` (`GITHUB_REPO`).
- [ ] Unieważniony stary klucz Groq, który był w `dostawcy.json`.
- [ ] Decyzja o podpisywaniu kodu (bez podpisu: ostrzeżenie SmartScreen / „Otwórz mimo to”).
