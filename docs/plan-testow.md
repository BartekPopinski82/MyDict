# Plan testów przed publikacją

Stan na 2026-11-15, wersja 2.0.0. „Sprawdzone” = wykonane i potwierdzone w dzienniku programu;
„Do zrobienia” = jeszcze niewykonane, z opisem, jak to zrobić i po czym poznać wynik.

## 1. Sprawdzone automatycznie

| Co | Jak | Wynik |
|---|---|---|
| Logika programu | `python -m pytest tests` | 255 testów przechodzi |
| Zbudowana aplikacja ma komplet bibliotek | `MyDict.exe --selftest` | OK |
| Instalator Windows | instalacja `/SILENT` na Windows 11 | 159 s razem z pobraniem bibliotek NVIDIA; skróty i deinstalator są |
| Pobieranie składników w trakcie instalacji | dziennik: „Kończenie instalacji: ['gpu']” | biblioteki NVIDIA pobrane (1,5 GB na dysku) |
| Pierwsze uruchomienie po instalacji | dziennik | model Turbo na karcie (`cuda/int8_float16`) po 5,8 s |
| Cicha aktualizacja | instalator z `/SILENT /AKTUALIZACJA` przy działającym programie | stary proces zamknięty, nowy uruchomiony, model gotowy po ok. 25 s |
| Przeniesienie ustawień ze starej nazwy | rejestr `HKCU\Software\BAPO\MyDict` | 16 wartości przeniesionych |
| Rozpoznanie kart | dziennik „Sprzęt: …” | wykryte AMD Radeon (zintegrowana) i GeForce GTX 1650 Ti |

| Drugi silnik (whisper.cpp, Vulkan) — kompilacja | `python packaging/build_whispercpp.py` | jeden plik `whisper-server.exe`, 45 MB |
| Drugi silnik wykrywa karty | dziennik `whispercpp.log` | obie karty widoczne mimo nakładek Steam / OBS / Epic / GOG (silnik wyłącza je tylko dla siebie) |
| Dyktowanie na karcie AMD (zintegrowanej) przez Vulkan | podanie nagrania w tempie mowy przez cały tor programu | pełny, poprawny tekst; model Lekki |
| Dyktowanie na GeForce przez Vulkan | jak wyżej, model Turbo | pełny, poprawny tekst |
| Pierwsze uruchomienie „komputera tylko z kartą AMD” | zbudowany `MyDict.exe` z `MYDICT_BEZ_CUDA=1`, `MYDICT_VULKAN_DEVICE=1`, pusty katalog danych | wykrycie karty → pobranie modelu (181 MB) → Vulkan → gotowy po 18 s |
| Proces silnika nie zostaje po programie | zabicie `MyDict.exe` | `whisper-server.exe` znika razem z nim |

Sprzęt testowy: laptop z GTX 1650 Ti (4 GB) i zintegrowaną kartą AMD, Windows 11.

### Pomiar szybkości drugiego silnika (jedno nagranie, 10 s, głos syntezatora)

| Karta (Vulkan) | Model | 1. przebieg po starcie | Fragment 4 s na żywo | Fragment 8 s na żywo | Końcowy, 10 s |
|---|---|---|---|---|---|
| AMD Radeon zintegrowana | Lekki | 2,7 s | 0,65 s | 1,26 s | 3,7 s |
| AMD Radeon zintegrowana | Turbo | 2,0 s | 1,65 s | 2,98 s | 4,8 s |
| GeForce GTX 1650 Ti | Lekki | 0,3 s | 0,22 s | 0,40 s | 0,9 s |
| GeForce GTX 1650 Ti | Turbo | 1,8 s | 0,38 s | 0,48 s | 1,1 s |

Wnioski: karta zintegrowana nadąża z modelem Lekkim, z Turbo nie — stąd domyślny wybór. To jedno
nagranie i głos syntezatora: liczby pokazują rząd wielkości, nie zastępują testu żywym głosem.

Dwa błędy znalezione i usunięte w trakcie tych prób (oba mają testy automatyczne):
- zwykłe czasy słów z whisper.cpp różniły się między przebiegami i program gubił słowa — silnik
  używa teraz wyrównania DTW (stabilne do ok. 0,02 s);
- model Turbo przy skróconym kontekście dźwięku powtarzał całe zdanie — powtórki są odcinane.

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
| Tylko karta **Intel** zintegrowana (typowy laptop biurowy) | model Lekki na karcie przez Vulkan | czy silnik startuje na sterowniku Intela; czy Lekki nadąża; jakość tekstu |
| Intel starszy niż ok. 2016 r. / bez Vulkan | automatyczne przejście na procesor, baner z wyjaśnieniem | czy przejście następuje bez błędu |
| Tylko karta **AMD** zintegrowana (inna niż nasza) | model Lekki przez Vulkan | jak wyżej |
| Karta **AMD dedykowana** (Radeon RX) albo **Intel Arc** | model Turbo przez Vulkan | czy Turbo nadąża; czy program wybrał kartę dedykowaną, a nie zintegrowaną |
| **NVIDIA RTX** (8 GB i więcej) | biblioteki NVIDIA + Turbo | opóźnienie; czy warto proponować model Najdokładniejszy |
| NVIDIA ze starym sterownikiem | komunikat o sterowniku, praca na procesorze | czy komunikat jest zrozumiały |
| NVIDIA z 2 GB pamięci | model Lekki na karcie | czy mieści się w pamięci |

**Decyzja do podjęcia po testach żywym głosem — karty NVIDIA przez Vulkan zamiast CUDA.**
Na GTX 1650 Ti drugi silnik z modelem Turbo liczył fragment 4 s w 0,38 s, podczas gdy wcześniejsze
pomiary pierwszego silnika (CUDA, `docs/eksperymenty.md`) dawały medianę ok. 1,6 s na przebieg.
Gdyby to się potwierdziło, karty NVIDIA mogłyby używać Vulkan i nie pobierać 1 GB bibliotek NVIDIA.
Tego **nie wolno** przyjąć na podstawie obecnych danych: to inne nagranie, inna kwantyzacja modelu
(q5) i skrócony kontekst dźwięku, a dokładność tekstu nie była porównana. Test: to samo nagranie
żywym głosem, w ustawieniach „Karta graficzna, jeśli dostępna” (CUDA) i „Karta graficzna — silnik
Vulkan”, porównanie `latencja.csv` i tekstu.

## 4. Do zrobienia — macOS

Kod dla macOS jest napisany, ale **nie był uruchamiany na Macu** — dotyczy to także drugiego silnika
w wersji Metal (kompilacja i działanie niesprawdzone; wersja Vulkan z tego samego kodu działa). Kolejność:

1. Zbudować aplikację (GitHub Actions albo `python packaging/build.py` na Macu) — autotest musi przejść.
2. Otworzyć DMG, przeciągnąć do Programy, uruchomić („Otwórz mimo to”).
3. Okno „Kończenie instalacji” pobiera model Turbo (0,6 GB); w ustawieniach widać „GPU (Metal)”.
   Zmierzyć opóźnienie — jeśli Turbo nie nadąża na starszym M1, zmienić domyślny model na Lekki.
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
- [x] Stary klucz Groq: nie ma go w `dostawcy.json` ani w historii git (sprawdzone 2026-11-16);
      klucze trzymane są w `secrets.env`, który jest w `.gitignore`.
- [ ] Konta, sekrety, klucze, certyfikaty, licencja LGPL — lista kroków w [DEVOPS.md](DEVOPS.md).
