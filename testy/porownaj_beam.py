"""Porównanie serii testu automatycznego z różnym final_beam: czas FINAL i zmiany tekstu.

Pierwszy folder to odniesienie (np. beam 5), kolejne są z nim porównywane:
  python porownaj_beam.py wyniki_auto\\beam5 wyniki_auto\\beam2 wyniki_auto\\beam1

Z każdego folderu serii (testy\\wyniki_auto\\<nazwa>) czytane są:
  teksty.txt     — tekst końcowy każdej wypowiedzi (zatwierdzone fragmenty + FINAL), per odtworzenie,
  przebiegi.csv  — czas przebiegu FINAL (asr_ms, final=True) i użyty beam_size,
  latencja.csv   — czas od końca mowy do FINAL (final_po_koncu_ms).

Dwa pomiary tekstu, oba jako odległość edycyjna w słowach (Levenshtein: wstawienie, usunięcie,
zamiana słowa = 1), bez wielkości liter i interpunkcji:

1. Przebiegi FINAL dla tych samych wypowiedzi (podstawa werdyktu). Wypowiedź rozpoznawana jest po
   długości bufora przebiegu FINAL (bufor_s) — przy odtwarzaniu tego samego nagrania jest ona
   powtarzalna co do 0,01 s, więc ten sam bufor_s = ten sam dźwięk na wejściu modelu. Porównywany
   jest tekst samego przebiegu FINAL (kolumna „tekst”, do 300 znaków) i jego czas asr_ms.
   Wypowiedzi, których bufor FINAL nie powtarza się w obu seriach (np. długi śpiew, cięty
   za każdym razem inaczej), są w tym pomiarze pomijane.
2. Cały tekst odtworzenia z teksty.txt (zatwierdzone fragmenty + FINAL) — obejmuje wszystko, ale
   jest mało czuły, bo tekst na żywo różni się między odtworzeniami także bez zmiany ustawień.

W obu pomiarach liczony jest „szum”: odległość między odtworzeniami wewnątrz serii A (ten sam beam).
Zmiana przypisana ustawieniu beam = odległość A↔B ponad ten szum.
"""
from __future__ import annotations

import argparse
import difflib
import itertools
import json
import re
import statistics
import sys
from pathlib import Path

import pandas as pd

from analiza_testu import czytaj, nazwa_modelu, tabela

# Nazwy z --modele w Test_automatyczny.py -> nazwy modeli w plikach CSV.
NAZWY_MODELI = {"turbo": "large-v3-turbo"}
NAGLOWEK = re.compile(r"^=== (.+), odtworzenie (\d+)\s*$")


def czytaj_teksty(folder: Path) -> dict[str, list[list[str]]]:
    """teksty.txt -> {model: [odtworzenie -> lista tekstów wypowiedzi]}."""
    wynik: dict[str, list[list[str]]] = {}
    biezace: list[str] | None = None
    for linia in (folder / "teksty.txt").read_text(encoding="utf-8").splitlines():
        m = NAGLOWEK.match(linia)
        if m:
            biezace = []
            wynik.setdefault(NAZWY_MODELI.get(m.group(1), m.group(1)), []).append(biezace)
        elif biezace is not None and linia.strip():
            biezace.append(linia.strip())
    return wynik


def slowa(tekst: str, dokladnie: bool = False) -> list[str]:
    """Słowa do porównania: domyślnie bez interpunkcji i wielkości liter."""
    if dokladnie:
        return tekst.split()
    return re.findall(r"\w+", tekst.lower())


def odleglosc(a: list[str], b: list[str]) -> int:
    """Odległość edycyjna Levenshteina na listach słów (wstawienie, usunięcie, zamiana = 1)."""
    poprzedni = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        wiersz = [i]
        for j, y in enumerate(b, 1):
            wiersz.append(min(poprzedni[j] + 1, wiersz[j - 1] + 1, poprzedni[j - 1] + (x != y)))
        poprzedni = wiersz
    return poprzedni[-1]


def mediana(wartosci) -> float | None:
    wartosci = [w for w in wartosci if w is not None and not pd.isna(w)]
    return statistics.median(wartosci) if wartosci else None


def odleglosci(a: list[list[str]], b: list[list[str]] | None, dokladnie: bool) -> float | None:
    """Mediana odległości słów: między seriami (a×b) albo wewnątrz serii (pary z a, gdy b=None)."""
    teksty_a = [slowa(" ".join(o), dokladnie) for o in a]
    if b is None:
        pary = itertools.combinations(teksty_a, 2)
    else:
        pary = itertools.product(teksty_a, [slowa(" ".join(o), dokladnie) for o in b])
    return mediana(odleglosc(x, y) for x, y in pary)


def pary_final(a: dict, b: dict, model: str) -> tuple[list[dict], int, int]:
    """Przebiegi FINAL obu serii złączone po długości bufora (ten sam bufor = ten sam dźwięk).
    Zwraca (wiersze per bufor, niedopasowane przebiegi FINAL w A, w B)."""
    def grupy(seria: dict) -> dict:
        prz = seria["prz"]
        prz = prz[(prz["model"] == model) & prz["final"]]
        return {klucz: g for klucz, g in prz.groupby(prz["bufor_s"].round(2))}

    ga, gb = grupy(a), grupy(b)
    wiersze = []
    for klucz in sorted(set(ga) & set(gb)):
        ta = [slowa(str(t)) for t in ga[klucz]["tekst"].fillna("")]
        tb = [slowa(str(t)) for t in gb[klucz]["tekst"].fillna("")]
        wiersze.append({
            "bufor_s": klucz, "n_a": len(ta), "n_b": len(tb),
            "asr_a": mediana(ga[klucz]["asr_ms"]), "asr_b": mediana(gb[klucz]["asr_ms"]),
            "slowa": mediana(len(t) for t in ta) or 0,
            "szum": mediana(odleglosc(x, y) for x, y in itertools.combinations(ta, 2)),
            "miedzy": mediana(odleglosc(x, y) for x, y in itertools.product(ta, tb)),
            "przyklad": next(((x, y) for x, y in itertools.product(ta, tb) if x != y), None),
        })
    poza_a = sum(len(g) for k, g in ga.items() if k not in gb)
    poza_b = sum(len(g) for k, g in gb.items() if k not in ga)
    return wiersze, poza_a, poza_b


def zmienione_wypowiedzi(a: list[list[str]], b: list[list[str]] | None) -> tuple[int, int, int]:
    """(zmienione wypowiedzi, porównane wypowiedzi, pary odtworzeń z innym podziałem na wypowiedzi).
    b=None: pary odtworzeń wewnątrz serii a (szum)."""
    zmienione = razem = inny_podzial = 0
    for x, y in (itertools.combinations(a, 2) if b is None else itertools.product(a, b)):
        if len(x) != len(y):
            inny_podzial += 1
            continue
        razem += len(x)
        zmienione += sum(slowa(p) != slowa(q) for p, q in zip(x, y))
    return zmienione, razem, inny_podzial


def przyklady(a: list[str], b: list[str], ile: int) -> list[str]:
    """Różnice słów między dwoma odtworzeniami, w kolejności występowania."""
    x, y = slowa(" ".join(a)), slowa(" ".join(b))
    wynik = []
    for znacznik, i1, i2, j1, j2 in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes():
        if znacznik != "equal":
            wynik.append(f"„{' '.join(x[i1:i2]) or '∅'}” → „{' '.join(y[j1:j2]) or '∅'}”")
    return wynik[:ile]


def wczytaj(folder: Path) -> dict:
    lat, prz = czytaj(folder, "latencja.csv"), czytaj(folder, "przebiegi.csv")
    lat["model"] = lat["model"].map(nazwa_modelu)
    prz["model"] = prz["model"].map(nazwa_modelu)
    prz["final"] = prz["final"].astype(str) == "True"
    plan = folder / "plan.json"
    beam = json.loads(plan.read_text(encoding="utf-8")).get("final_beam") if plan.exists() else None
    return {"folder": folder, "lat": lat, "prz": prz, "teksty": czytaj_teksty(folder), "beam_plan": beam}


def beam_serii(seria: dict, model: str) -> str:
    """beam_size przebiegów FINAL faktycznie zapisany w przebiegi.csv."""
    prz = seria["prz"]
    wartosci = sorted(set(prz[(prz["model"] == model) & prz["final"]]["beam_size"].dropna().astype(int)))
    return "/".join(map(str, wartosci)) or "—"


def czasy(seria: dict, model: str) -> dict:
    prz, lat = seria["prz"], seria["lat"]
    prz, lat = prz[prz["model"] == model], lat[lat["model"] == model]
    return {
        "final_n": int(prz["final"].sum()),
        "final_asr": mediana(prz[prz["final"]]["asr_ms"]),
        "final_po_koncu": mediana(lat["final_po_koncu_ms"]),
        "live_asr": mediana(prz[~prz["final"]]["asr_ms"]),
    }


def ms(x) -> str:
    return "—" if x is None else f"{x:.0f}"


def delta(b, a) -> str:
    return "—" if a is None or b is None else f"{b - a:+.0f}"


def porownaj(model: str, a: dict, b: dict, prog_ms: float, prog_proc: float, ile_przykladow: int) -> str:
    """Drukuje porównanie jednego modelu i zwraca werdykt."""
    ta, tb = a["teksty"].get(model, []), b["teksty"].get(model, [])
    ca, cb = czasy(a, model), czasy(b, model)
    print(f"  MODEL {model}:  odtworzeń A={len(ta)}, B={len(tb)}   "
          f"beam FINAL wg przebiegi.csv: A={beam_serii(a, model)}, B={beam_serii(b, model)}")
    if not ta or not tb:
        print("    brak danych w jednej z serii\n")
        return "brak danych"
    if beam_serii(a, model) == beam_serii(b, model):
        print("    ! obie serie mają ten sam beam FINAL — to nie jest porównanie ustawień")

    tabela(["czas [ms], mediana", "A", "B", "B−A"], [
        [f"przebieg FINAL (asr_ms; n={ca['final_n']}/{cb['final_n']})",
         ms(ca["final_asr"]), ms(cb["final_asr"]), delta(cb["final_asr"], ca["final_asr"])],
        ["koniec mowy → FINAL (final_po_koncu_ms)",
         ms(ca["final_po_koncu"]), ms(cb["final_po_koncu"]), delta(cb["final_po_koncu"], ca["final_po_koncu"])],
        ["przebiegi na żywo (kontrola, beam bez zmian)",
         ms(ca["live_asr"]), ms(cb["live_asr"]), delta(cb["live_asr"], ca["live_asr"])],
    ])

    pary, poza_a, poza_b = pary_final(a, b, model)
    print("  Przebiegi FINAL dla tych samych wypowiedzi (ten sam bufor_s) — mediany per wypowiedź")
    tabela(["bufor_s", "n A/B", "asr_ms A", "asr_ms B", "B−A", "słów", "szum A↔A", "A↔B"], [
        [f"{w['bufor_s']:.2f}", f"{w['n_a']}/{w['n_b']}", ms(w["asr_a"]), ms(w["asr_b"]), delta(w["asr_b"], w["asr_a"]),
         ms(w["slowa"]), ms(w["szum"]), ms(w["miedzy"])] for w in pary])
    final_slow = sum(w["slowa"] for w in pary)
    final_miedzy = sum(w["miedzy"] for w in pary)
    # Wypowiedź z jednym przebiegiem w A nie ma szumu — liczona jak szum 0 (każda zmiana idzie na konto beam).
    final_szum = sum(w["szum"] or 0 for w in pary) if pary else None
    final_ponad = sum(max(0.0, w["miedzy"] - (w["szum"] or 0)) for w in pary) if pary else None
    final_asr = mediana(w["asr_b"] - w["asr_a"] for w in pary)
    print(f"  Razem {len(pary)} wypowiedzi, {final_slow:.0f} słów: zmienione słowa A↔B {final_miedzy:.1f}, "
          f"szum A↔A {'—' if final_szum is None else f'{final_szum:.1f}'}, "
          f"ponad szum {'—' if final_ponad is None else f'{final_ponad:.1f}'}; "
          f"mediana różnicy asr_ms per wypowiedź: {'—' if final_asr is None else f'{final_asr:+.0f}'} ms")
    if poza_a or poza_b:
        print(f"  (bez pary: {poza_a} przebiegów FINAL w A, {poza_b} w B — bufor o innej długości)")
    roznice = [f"[{w['bufor_s']:.2f} s] „{' '.join(w['przyklad'][0])}” → „{' '.join(w['przyklad'][1])}”"
               for w in pary if w["przyklad"] and w["miedzy"] > (w["szum"] or 0)]
    for linia in roznice[:ile_przykladow]:
        print("    " + linia[:230])
    print()

    n_slow = mediana(len(slowa(" ".join(o))) for o in ta) or 0
    wiersze, ponad = [], {}
    for nazwa, dokladnie in (("słowa (bez interpunkcji i wielkości liter)", False),
                             ("z interpunkcją i wielkością liter", True)):
        szum_a, szum_b = odleglosci(ta, None, dokladnie), odleglosci(tb, None, dokladnie)
        miedzy = odleglosci(ta, tb, dokladnie)
        ponad[dokladnie] = None if szum_a is None else max(0.0, miedzy - szum_a)
        wiersze.append([nazwa, ms(szum_a), ms(szum_b), ms(miedzy),
                        "—" if ponad[dokladnie] is None else f"{ponad[dokladnie]:.1f}"])
    print(f"  Cały tekst odtworzenia — mediana odległości edycyjnej (ok. {n_slow:.0f} słów; pomiar pomocniczy)")
    tabela(["", "szum A↔A", "szum B↔B", "A↔B", "ponad szum A"], wiersze)
    zm, razem, inny = zmienione_wypowiedzi(ta, tb)
    zm_a, razem_a, _ = zmienione_wypowiedzi(ta, None)
    if razem:
        szum = f" (wewnątrz A: {zm_a / razem_a * 100:.0f}%)" if razem_a else ""
        print(f"  Wypowiedzi z innym tekstem: {zm} z {razem} = {zm / razem * 100:.0f}%{szum}"
              + (f"; {inny} par odtworzeń pominięto — inny podział na wypowiedzi" if inny else ""))
    lista = przyklady(ta[0], tb[0], ile_przykladow)
    if lista:
        print("  Przykłady (1. odtworzenie A → 1. odtworzenie B): " + "; ".join(lista))

    oszcz = None if final_asr is None else -final_asr   # ten sam dźwięk na wejściu, więc różnica to sam beam
    po_koncu = delta(cb["final_po_koncu"], ca["final_po_koncu"])
    proc = None if final_ponad is None or not final_slow else final_ponad / final_slow * 100
    if oszcz is None:
        werdykt = "brak czasu FINAL"
    elif proc is None:
        werdykt = "brak wspólnych wypowiedzi"
    else:
        werdykt = "ZALICZONE" if oszcz >= prog_ms and proc <= prog_proc else "NIE"
        werdykt += (f" (FINAL krótszy o {oszcz:.0f} ms, koniec mowy → FINAL {po_koncu} ms; "
                    f"{final_ponad:.1f} zmienionych słów ponad szum z {final_slow:.0f} = {proc:.1f}%)")
    print(f"  => {werdykt}\n")
    return werdykt


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("odniesienie", type=Path, help="folder serii odniesienia (np. beam 5)")
    ap.add_argument("serie", type=Path, nargs="+", help="foldery serii porównywanych z odniesieniem")
    ap.add_argument("--min-ms", type=float, default=200, help="minimalne skrócenie przebiegu FINAL dla tych samych wypowiedzi, w ms (domyślnie 200)")
    ap.add_argument("--max-zmian-proc", type=float, default=1.0,
                    help="dopuszczalne zmiany słów ponad szum w przebiegach FINAL, w %% ich słów (domyślnie 1)")
    ap.add_argument("--przyklady", type=int, default=8, help="ile różnic słów pokazać")
    a = ap.parse_args()

    baza = wczytaj(a.odniesienie)
    podsumowanie = []
    for folder in a.serie:
        seria = wczytaj(folder)
        print("=" * 100)
        print(f"A = {baza['folder']} (final_beam wg planu: {baza['beam_plan'] or '—'})")
        print(f"B = {seria['folder']} (final_beam wg planu: {seria['beam_plan'] or '—'})")
        print("=" * 100)
        for model in sorted(set(baza["teksty"]) | set(seria["teksty"])):
            werdykt = porownaj(model, baza, seria, a.min_ms, a.max_zmian_proc, a.przyklady)
            podsumowanie.append([f"{baza['folder'].name} → {folder.name}", model, werdykt])
    print("PODSUMOWANIE  (zaliczone: przebieg FINAL tych samych wypowiedzi krótszy o >= "
          f"{a.min_ms:.0f} ms i zmiany słów FINAL ponad szum <= {a.max_zmian_proc:g}%)")
    tabela(["porównanie", "model", "wynik"], podsumowanie)


if __name__ == "__main__":
    main()
