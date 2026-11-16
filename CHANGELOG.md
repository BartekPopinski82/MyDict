# Historia zmian

Sekcja danej wersji trafia do opisu wydania na GitHubie i do okna aktualizacji w programie.

## 2.0.0

Pierwsza wersja samodzielna — program instaluje się jak zwykła aplikacja i nie wymaga już
Pythona ani folderu BAPO-transcript.

### Nowe
- Instalator dla Windows i aplikacja dla macOS (Apple Silicon).
- Aktualizacje są podpisane cyfrowo — program instaluje tylko wydania podpisane przez autora.
- Przycisk **Zgłoś problem**: szyfrowane zgłoszenie do autora; po awarii program sam je zaproponuje.
- Licencja programu i lista licencji dołączonych składników (w instalatorze i folderze programu).
- Automatyczne aktualizacje: program sam sprawdza nowe wydania, pobiera je i instaluje.
- Obsługa macOS: wpisywanie tekstu do aktywnej aplikacji, globalny skrót, klucze API w pęku kluczy.
- Instalator rozpoznaje kartę graficzną i sam pobiera potrzebne składniki (biblioteki NVIDIA, model
  mowy dobrany do sprzętu) — po instalacji program jest od razu gotowy.
- Drugi silnik rozpoznawania (whisper.cpp): karty AMD i Intel liczą przez Vulkan, a Mac przez Metal —
  przyspieszenie nie wymaga już karty NVIDIA.
- Nowy wygląd: motyw jasny i ciemny (zgodny z systemem albo wybrany ręcznie), większa czytelność,
  ustawienia podzielone na sekcje, nowa ikona.

### Zmienione
- Dziennik, pomiary i pobrane składniki są w katalogu danych użytkownika; własne wersje plików
  `dostawcy.json` i `ustawienia_asr.json` umieszczone tam mają pierwszeństwo i przetrwają aktualizację.
- Połączenia HTTPS korzystają z certyfikatów systemu (działa także w sieciach firmowych).
