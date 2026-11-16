# DEVOPS — co zostało do zrobienia ręcznie

Rzeczy, których nie da się zrobić w kodzie: konta, sekrety, certyfikaty, decyzje.
Mechanizmy są gotowe i opisane w [wydawanie.md](wydawanie.md). Odhaczaj `[x]` po wykonaniu.

Kolejność ma znaczenie: kroki 1–4 przed pierwszym wydaniem, 5–6 gdy będą pieniądze/konto,
7 na końcu.

---

## 1. Kopia zapasowa kluczy — NAJWAŻNIEJSZE

- [ ] Skopiuj folder `C:\Users\barte\.mydict-klucze\` w dwa bezpieczne miejsca poza tym
      komputerem (np. menedżer haseł + pendrive w szufladzie).

Są tam dwa pliki tekstowe (po 64 znaki):

| Plik | Do czego | Co, jeśli zginie | Co, jeśli wycieknie |
|---|---|---|---|
| `klucz-wydan` | podpis każdego wydania | **żadna zainstalowana kopia nie przyjmie już automatycznej aktualizacji** — użytkownicy musieliby ręcznie pobrać nową wersję | ktoś z dostępem do wydań na GitHubie mógłby podsunąć fałszywą aktualizację |
| `klucz-zgloszen` | odczyt zgłoszeń od użytkowników | nie odczytasz zgłoszeń już wysłanych | ktoś z kopią repozytorium zgłoszeń przeczyta ich treść |

Nigdy nie wrzucaj ich do repozytorium ani nie wysyłaj mailem / komunikatorem.
Klucze publiczne (te „do sprawdzania”) są już wpisane w `dyktowanie_app.py` — mogą być jawne.

## 2. Sekret z kluczem wydań na GitHubie

Bez tego workflow **Wydanie** zatrzyma się na kroku podpisu i nic nie opublikuje.

- [ ] Otwórz plik `C:\Users\barte\.mydict-klucze\klucz-wydan` w Notatniku, skopiuj całą linię.
- [ ] GitHub → repozytorium **MyDict** → **Settings → Secrets and variables → Actions →
      New repository secret**
  - Name: `MYDICT_KLUCZ_WYDAN`
  - Secret: wklejona linia
- [ ] Zamknij Notatnik bez zapisywania zmian.

(Sekret jest szyfrowany przez GitHub; nikt, także Ty, nie zobaczy go potem w całości — tylko
workflow go używa.)

## 3. Repozytorium na zgłoszenia problemów

Bez tego przycisk **Zgłoś problem** działa, ale tylko zapisuje zgłoszenie do pliku na komputerze
użytkownika (ten musi je wysłać sam).

- [ ] GitHub → **New repository**: nazwa `MyDict-zgloszenia`, **Private**. (Inna nazwa → zmień
      `REPORTS_REPO` w `dyktowanie_app.py`.)
- [ ] Token: GitHub → zdjęcie profilu → **Settings → Developer settings → Personal access tokens →
      Fine-grained tokens → Generate new token**
  - Repository access: **Only select repositories** → tylko `MyDict-zgloszenia`
  - Permissions → Repository permissions → **Contents: Read and write** (nic więcej)
  - Expiration: najdłuższy możliwy
- [ ] Skopiuj token i dodaj w repozytorium **MyDict** jako sekret `MYDICT_ZGLOSZENIA_TOKEN`
      (jak w kroku 2).
- [ ] Zapisz w kalendarzu datę wygaśnięcia tokenu minus miesiąc: wtedy wygeneruj nowy, podmień
      sekret i wydaj nową wersję. Wersje z wygasłym tokenem przestaną wysyłać (zostaje zapis do pliku).

Dlaczego tak: token jest wbudowany w program i da się go wydobyć. Dlatego ma prawo tylko do tego
jednego repozytorium, a zgłoszenia są szyfrowane kluczem `klucz-zgloszen` — z samym tokenem nie da
się ich przeczytać.

**Czytanie zgłoszeń** (gdy już przychodzą):

```
git clone https://github.com/BartekPopinski82/MyDict-zgloszenia
python packaging/zgloszenia.py <ścieżka do klonu>
```

Pliki `.txt` pojawią się w `<klon>/odczytane/<identyfikator instalacji>-<kontakt>/`. Później
wystarczy `git pull` w klonie i ponownie to samo polecenie (już odczytane są pomijane).
Dopisz `odczytane/` do `.gitignore` klonu, żeby odszyfrowane treści nie trafiły do repozytorium.

## 4. Pełny tekst licencji LGPL-3.0

Program zawiera biblioteki Qt/PySide6 i FFmpeg na licencji **GNU LGPL 3.0**. Ta licencja wymaga,
żeby razem z programem dostarczyć jej **pełny tekst**. Żaden z pakietów go nie zawiera (pełny
tekst GPL 3.0, na który LGPL się powołuje, już jest — w pakiecie numpy). Generator listy licencji
do tego czasu wstawia tylko link i ostrzega przy budowaniu
(`UWAGA: brak packaging/licencje/LGPL-3.0.txt`).

- [x] Pobierz oficjalny tekst: https://www.gnu.org/licenses/lgpl-3.0.txt (ok. 8 KB, zwykły tekst).
- [x] Zapisz go jako `packaging/licencje/LGPL-3.0.txt` (folder `licencje` trzeba utworzyć).
- [x] Zatwierdź w git. Od następnego budowania tekst sam trafi do `THIRD-PARTY-NOTICES.txt`
      i ostrzeżenie zniknie.

(Albo napisz w rozmowie z Claude „pobierz LGPL” — zrobi to za Ciebie.)

## 5. Certyfikat do podpisu kodu — Windows

Bez niego: przy instalacji niebieskie okno SmartScreen „System Windows ochronił komputer”
(użytkownik musi kliknąć „Więcej informacji → Uruchom mimo to”), a antywirusy częściej
fałszywie oznaczają program.

- [ ] **Decyzja, u kogo kupić.** Azure Trusted Signing (najtańszy) **nie jest dostępny dla osoby
      fizycznej ani JDG z Polski** — tylko dla firm (np. spółki). Dla Ciebie: certyfikat **OV
      w chmurze**, np. SSL.com eSigner, Certum, Sectigo (rząd wielkości: kilkaset zł/rok). Przy
      wyborze pytaj, czy da się podpisywać automatycznie w GitHub Actions **bez telefonu**
      (SSL.com eSigner: tak, z kluczem TOTP; Certum SimplySign: wymaga potwierdzenia w telefonie).
- [ ] Przejście weryfikacji tożsamości u dostawcy (dokument, czasem telefon).
- [ ] Przekazać Claude nazwę dostawcy — dopisze do workflow krok, który ustawia polecenie podpisu
      (`MYDICT_SIGN_CMD`); dane logowania dostawcy trafią do sekretów repozytorium.
- [ ] Po pierwszym udanym podpisanym wydaniu: zmienna repozytorium (Settings → Secrets and
      variables → Actions → **Variables**) `WYMAGAJ_PODPISU_KODU` = `true`. Od tej chwili wydanie
      Windows bez podpisu nie wyjdzie.

Po zmianie certyfikatu SmartScreen może jeszcze przez kilka dni/tygodni ostrzegać — reputacja
rośnie z liczbą pobrań.

## 6. Konto Apple Developer — macOS

Bez niego: na Macu przy pierwszym uruchomieniu trzeba wejść w Ustawienia → Prywatność i ochrona →
„Otwórz mimo to”, a **zgoda „Dostępność” kasuje się po każdej aktualizacji** (dyktowanie przestaje
wpisywać tekst, dopóki użytkownik nie nada jej ponownie).

- [ ] Decyzja: wydajemy wersję na Maca od razu (bez podpisu, z powyższymi niedogodnościami) czy
      dopiero z kontem.
- [ ] Konto: https://developer.apple.com/programs/ — 99 USD/rok, weryfikacja tożsamości.
- [ ] Certyfikat **Developer ID Application** + hasło do notaryzacji (app-specific password).
- [ ] Przekazać Claude — dopisze podpis i notaryzację do workflow (budowanie już obsługuje
      `MACOS_SIGN_IDENTITY`; brakuje kroku `notarytool` i usunięcia z aktualizatora zdejmowania
      kwarantanny).
- [ ] Przetestować na prawdziwym Macu (punkt 4 w [plan-testow.md](plan-testow.md)) — tu Claude
      nie miał Maca, więc test DMG z workflow uruchomi się dopiero na GitHubie.

## 7. Repozytorium i pierwsze wydanie — na końcu

- [ ] Utworzyć na GitHubie publiczne repozytorium `BartekPopinski82/MyDict` (nazwa musi zgadzać
      się z `GITHUB_REPO` w `dyktowanie_app.py` — z niego programy pobierają aktualizacje).
- [ ] Przed wypchnięciem: przejrzeć, czy w repozytorium nie ma prywatnych rzeczy (sekrety są
      w `.gitignore`; kluczy w repozytorium nie ma — leżą w `C:\Users\barte\.mydict-klucze\`).
- [ ] Wypchnąć kod; sprawdzić, że workflow **Testy** przechodzi (Actions).
- [ ] Wykonać kroki 1–4 powyżej.
- [ ] Pierwsze wydanie według [wydawanie.md](wydawanie.md): wersja wstępna → test instalatora na
      czystym komputerze → **Actions → Udostępnij wydanie**.
- [ ] Lista „Przed publikacją” w [plan-testow.md](plan-testow.md) (testy na czystym komputerze,
      bez karty NVIDIA itd.).

## Do decyzji (bez pośpiechu)

- [ ] Dane licencjodawcy w [LICENSE.txt](../LICENSE.txt): teraz „BAPO”. Jeśli chcesz pełną nazwę
      (imię i nazwisko / firma, ewentualnie adres) — dopisz.
- [ ] Nazwa „MyDict”: przed promocją sprawdzić znaki towarowe (EUIPO, UPRP) — nazwa jest popularna.
- [ ] Logowanie użytkowników i banery sponsorów — plan był w rozmowie; nie zaczęte.
