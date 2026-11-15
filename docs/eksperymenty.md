# Eksperymenty, których nie powtarzać

Wyniki pomiarów z 2026-10-03 (GTX 1650 Ti). Każda sekcja opisuje pomysł, który sprawdzono i odrzucono —
zanim wrócisz do któregoś z nich, przeczytaj, dlaczego nie zadziałał.

## Nie wprowadzać ponownie: `word_timestamps` tylko w FINAL

Sprawdzone 2026-10-03 i wycofane — **nie działa**:

- Wyłączenie `word_timestamps` w przebiegach na żywo **nie skraca przebiegu** (small 0%, medium
  i turbo kilka procent, w granicach szumu). Koszt przebiegu nie siedzi w czasach słów.
- **Psuje tekst:** bez prawdziwych czasów słów giną słowa, zwłaszcza po przycięciu bufora w długich
  wypowiedziach. `LocalAgreement` i `LiveStreamer._trim` potrzebują tych czasów do odcinania
  zatwierdzonych słów i do miejsca cięcia bufora; czasy zastępcze (rozłożone w segmencie) plus
  dopasowanie po tekście przechodziły testy na atrapie, ale na prawdziwym modelu gubiły słowa.

Wniosek: `word_timestamps=True` zostaje w każdym przebiegu. Opóźnień szukać gdzie indziej.

## Nie wprowadzać ponownie: krótszy bufor (`trim_after` / `max_buffer`) dla small, medium, large-v3

Sprawdzone 2026-10-03 i wycofane — **nie działa**. Pomysł: koszt przebiegu rośnie z długością bufora
(poza turbo), więc przycinać bufor wcześniej niż przy 14 / 24 s. Test: `testy\Test_automatyczny.py`,
to samo nagranie, po 1 odtworzeniu na model, warianty 8 / 12 s, 10 / 16 s i dwa razy 14 / 24 s.

Czas przebiegu ASR na żywo, mediana / p90 w ms:

| Model    | 14 / 24 (1) | 14 / 24 (2) | 10 / 16     | 8 / 12      |
|----------|-------------|-------------|-------------|-------------|
| small    | 606 / 1093  | 605 / 1325  | 597 / 848   | 575 / 806   |
| medium   | 1379 / 1836 | 1444 / 1957 | 1370 / 1831 | 1363 / 1663 |
| large-v3 | 2566 / 3696 | 2395 / 3179 | 2208 / 2988 | 2210 / 4090 |

- **Zysk jest mały:** mediana prawie się nie zmienia, bo typowy bufor ma 4–5 s i limit go nie dotyczy.
  p90 wyraźnie spada tylko dla small; dla medium i large-v3 różnice mieszczą się w rozrzucie między
  dwiema seriami 14 / 24.
- **Psuje tekst** w długiej wypowiedzi (piosenka 34–37 s, bez kropek): przy 8 / 12 wszystkie trzy modele
  gubią refren, small także kolejny wers; przy 10 / 16 medium ma pełny tekst, ale small i large-v3 gubią
  duże fragmenty. Przy 14 / 24 tekst jest pełny w medium i large-v3.
- W wypowiedzi ok. 15,7 s ubytków nie ma, ale medium przy 8 / 12 i 10 / 16 dubluje początek
  („Dwa, trzy, cztery…” po „Raz, dwa, trzy.”).
- Przyczyny ubytków nie zbadano. Podejrzenie: bez końca zdania cięcie jest wymuszane po ostatnim
  zatwierdzonym słowie (`LiveStreamer._trim`), a przy krótszym buforze zdarza się to częściej.

Wniosek: `trim_after` = 14 s i `max_buffer` = 24 s zostają dla wszystkich modeli. Wyniki serii:
`testy\wyniki_auto\bufor_przed`, `bufor_przed_2`, `bufor_10_16`, `bufor_po` (8 / 12).

## Nie wprowadzać ponownie: niższy `final_beam` (2 lub 1 zamiast 5)

Sprawdzone 2026-10-03 i odrzucone — **zysk za mały**. Pomysł: końcowy przebieg FINAL liczy się z beam 5
(na żywo beam 1), więc niższy beam miał skrócić czas od końca mowy do FINAL. Próg zaliczenia: FINAL
krótszy o min. ok. 0,2 s przy znikomej liczbie zmienionych słów. Test: `testy\Test_automatyczny.py
--final-beam N`, to samo nagranie, po 1 odtworzeniu na model; porównanie: `testy\porownaj_beam.py`.

Skrócenie przebiegu FINAL (mediana różnicy `asr_ms` dla tych samych wypowiedzi, czyli tego samego
`bufor_s`) i liczba zmienionych słów w tych przebiegach:

| Model  | beam 2: krócej o | beam 2: zmienione słowa | beam 1: krócej o | beam 1: zmienione słowa |
|--------|------------------|-------------------------|------------------|-------------------------|
| small  | 57 ms            | 0 z 73                  | 111 ms           | 1 z 73                  |
| medium | 38 ms            | 1 z 75                  | 91 ms            | 0 z 75                  |
| turbo  | 42 ms            | 0 z 74                  | 70 ms            | 8 z 74                  |

- **Zysk jest poniżej progu** dla każdego modelu i obu ustawień. Rośnie z długością bufora: przy
  buforze 11 s beam 1 daje ok. −430 ms (small, medium) i −140 ms (turbo), ale przy typowych krótkich
  wypowiedziach (1,6–3 s) tylko 30–100 ms.
- **Tekst:** beam 2 praktycznie go nie zmienia. Beam 1 na turbo zmienił 8 słów w jednej wypowiedzi
  (wyliczanka „jedna, dwa na trzy…”).
- Medium na beam 2 i beam 1 wpadł w pętlę „pam pam pam…” w piosence (bufor 18,8 s), a FINAL trwał
  7,9 s i 5,8 s. Nie wiadomo, czy to skutek beam: nie ma przebiegu beam 5 z tym samym buforem
  i ustawieniami bufora 14 / 24.
- Słabe strony pomiaru: jedno odtworzenie na ustawienie; baza beam 5 dla small i medium to wcześniejsze
  serie `bufor_po`, `bufor_przed_2`, `bufor_10_16` (różne limity bufora, parowane po `bufor_s`), a dla
  turbo jedno odtworzenie z tej samej sesji, bez oceny szumu.

Wniosek: `final_beam` = 5 zostaje. Ustawienie jest w pliku `ustawienia_asr.json` obok programu (czytane
przy starcie; brak pliku = 5). Wyniki serii: `testy\wyniki_auto\beam5` (tylko turbo), `beam2`, `beam1`,
porównanie w `testy\wyniki_auto\porownanie_beam.txt`.

## Nie wprowadzać ponownie: `compute_type` float16 zamiast int8_float16

Sprawdzone 2026-10-03 i odrzucone — **float16 jest na GTX 1650 Ti ok. 4 razy wolniejszy**. Pomysł:
w turbo i large-v3 karta pracuje przy 90–100% obciążenia i dobija do limitu mocy (SW), więc czysty
float16 miał skrócić przebieg. Próg zaliczenia: krótszy czas przebiegu bez wzrostu błędów i bez
przekroczenia VRAM. Test: `testy\Test_automatyczny.py`, to samo nagranie, turbo, po 1 odtworzeniu
w każdym typie (final_beam 5).

| turbo                                   | int8_float16 | float16   |
|-----------------------------------------|--------------|-----------|
| przebieg na żywo, mediana `asr_ms`      | 1599 ms      | 6688 ms   |
| przebieg FINAL, mediana `asr_ms`        | 1689 ms      | 6700 ms   |
| czas na 1 s bufora (na żywo)            | 246 ms       | 1061 ms   |
| przebiegów w 101 s                      | 55           | 14        |
| 1. tekst od początku mowy, mediana      | 2,0 s        | 26,3 s    |
| FINAL po końcu mowy, mediana / maks.    | 2,9 / 4,0 s  | 3,5 / 11,7 s |
| VRAM maks. (cała karta, 4096 MB)        | 2892 MB      | 3799 MB   |
| temperatura maks.                       | 63 °C        | 61 °C     |
| moc średnia                             | 36,3 W       | 31,8 W    |
| próbki z limitem mocy (SW)              | 71 z 525     | 0 z 529   |

- **Czas:** przy float16 program nie nadąża za mową i tekst na żywo praktycznie się nie pojawia.
  Karta jest zajęta w 98%, ale pobiera mniej mocy i nie dobija do limitu — sama liczy wolniej.
- **VRAM:** sam model turbo zajmuje 1155 MB w int8_float16 i 2222 MB we float16 (pomiar po
  załadowaniu i jednym przebiegu). Z innymi programami na karcie (1,4–1,6 GB) zostaje ok. 300 MB zapasu.
- **large-v3 nie testowany:** we float16 sam model zająłby ok. 4,3 GB (szacunek z przyrostu turbo
  i rozmiaru pliku modelu), więc nie mieści się w 4 GB.
- **Tekst:** pierwsze sześć wypowiedzi prawie identyczne. Float16 zachował refren piosenki, który
  int8_float16 pominął, ale rozciął jedno zdanie i nie oddał ostatniej wypowiedzi z nagrania.
- Słabe strony pomiaru: jedno odtworzenie na typ; błędów nie policzono względem referencji.

Wniosek: zostaje `int8_float16` wpisany w `load_whisper` (`dyktowanie_asr.py`); float16 jest tam tylko
zapasem, gdy int8_float16 nie ruszy. Konfigurację typu per model w `ustawienia_asr.json` i przełącznik
`--compute-type` w teście usunięto po teście. Wyniki serii: `testy\wyniki_auto\ct_int8_float16`,
`ct_float16`.
