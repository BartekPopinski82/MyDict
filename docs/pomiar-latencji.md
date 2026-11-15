# Pomiar latencji

Każda wypowiedź dostaje w dzienniku (`dyktowanie.log` w katalogu danych programu) linie `[LAT #N]`:

| Znacznik | Co oznacza |
|---|---|
| DŹWIĘK START / KONIEC | kiedy mowa realnie była w mikrofonie (zegar karty dźwiękowej) |
| VAD START / KONIEC | kiedy program wykrył początek mowy / uznał ciszę za koniec |
| 1. PEWNY TEKST, FINAL | pierwsze zatwierdzone słowa / gotowy tekst po pauzie |
| OKIENKO START / PEWNY / FINAL | pierwszy tekst na pływającym pasku (także szary), pierwszy biały, tekst domknięty |
| WPISYWANIE START / KONIEC | pierwszy i ostatni znak wysłany do aplikacji docelowej |

Linia `PODSUMOWANIE` podaje opóźnienia w ms, m.in. **okienko→aplikacja** — o ile pasek wyprzedza
wpisywanie (pasek pokazuje od razu tekst niepewny, a do aplikacji trafia tylko pewny).
Te same dane, jeden wiersz na wypowiedź, trafiają do `latencja.csv` obok dziennika (separator `;`,
otwiera się w Excelu).

## Porównanie modeli na tym samym nagraniu (Windows)

1. Wybierz źródło „🔊 Dźwięk komputera: wszystkie wyjścia (automatycznie)” i model.
2. Włącz dyktowanie (np. w Notatniku), puść nagranie w dowolnym odtwarzaczu, po końcu zatrzymaj.
3. Zmień model i puść to samo nagranie jeszcze raz. Tekst porównujesz w Notatniku, a opóźnienia
   każdej wypowiedzi trafiają do `latencja.csv` (z kolumną `model`).

Głośność systemowa nie ma znaczenia, ale wyciszony odtwarzacz = cisza. Do tego samego wyjścia trafiają
też dźwięki powiadomień — na czas testu najlepiej je wyciszyć.

Skrypty diagnostyczne w folderze `testy/` (pliki `.bat`) zakładają uruchamianie ze źródeł w środowisku
`..\.venv`.
