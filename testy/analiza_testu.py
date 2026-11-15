"""Analiza testu opóźnień dyktowania z dzienników latencja.csv i przebiegi.csv.

Przykłady:
  python analiza_testu.py --od 06:32 --do 07:26 --data 2026-10-03 --folder baseline_2026-10-03
  python analiza_testu.py --od 09:10 --do 10:05 --baseline baseline_2026-10-03

Zakres baseline bierze się z --baseline-od/--baseline-do/--baseline-data, a gdy ich
nie ma — z pliku zakres.json w folderze baseline, a gdy i tego brak — taki sam jak --od/--do.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

MODELE = ["small", "medium", "large-v3", "large-v3-turbo"]
METRYKI = [
    "glos_do_pierwszego_tekstu_ms",
    "final_po_koncu_ms",
    "cisza_do_konca_ms",
    "asr_final_ms",
    "asr_sredni_ms",
    "kolejka_audio_max_ms",
]
BUFOR_PROGI = [0, 1, 3, 6, 10, 30]
BUFOR_ETYKIETY = ["0-1 s", "1-3 s", "3-6 s", "6-10 s", "10-30 s"]
PETLA_TOKENY = 200
# Kawałki nagrania testowego: (nazwa, min ms, max ms) dla dlugosc_mowy_ms.
KAWALKI = [
    ("K2 ~1 s", 800, 1150),
    ("K4 ~1,9 s", 1600, 2200),
    ("K1 ~8,5 s", 8000, 9000),
    ("K6 ~15,4 s", 14900, 15550),
    ("K3 ~15,7 s", 15550, 16200),
    ("K5 34-37 s", 33000, 38500),
]
KAWALKI_KOLEJNOSC = ["K1 ~8,5 s", "K2 ~1 s", "K3 ~15,7 s", "K4 ~1,9 s", "K5 34-37 s", "K6 ~15,4 s", "inne"]
KAWALEK_METRYKI = ["glos_do_pierwszego_tekstu_ms", "final_po_koncu_ms", "cisza_do_konca_ms", "asr_final_ms"]


def czytaj(folder: Path, nazwa: str) -> pd.DataFrame:
    return pd.read_csv(folder / nazwa, sep=";", encoding="utf-8-sig", na_values=["—"], low_memory=False)


def sekundy(kolumna: pd.Series) -> pd.Series:
    return pd.to_timedelta(kolumna, errors="coerce").dt.total_seconds()


def hhmm(tekst: str) -> float:
    g, m = tekst.split(":")[:2]
    return int(g) * 3600 + int(m) * 60


def nazwa_modelu(etykieta) -> str:
    """Etykieta z dziennika -> nazwa modelu wspólna dla obu plików CSV:
    "GPU (CUDA) · small · int8_float16" -> "small", "Chmura (API) · Deepgram · Nova-3" -> "Deepgram · Nova-3"."""
    czesci = str(etykieta).split(" · ")
    if czesci[0] == "Chmura (API)" and len(czesci) > 1:
        return " · ".join(czesci[1:])
    if czesci[0] in ("GPU (CUDA)", "CPU") and len(czesci) >= 3:
        return czesci[1]
    return str(etykieta)


def modele_w_danych(*zakresy) -> list[str]:
    """Modele lokalne w stałej kolejności, potem pozostałe z danych (chmura, strumień)."""
    inne = set()
    for z in zakresy:
        if z is not None:
            inne |= set(z["lat"]["model"].dropna()) | set(z["prz"]["model"].dropna())
    return MODELE + sorted(inne - set(MODELE) - {"nan"})


def daty_przebiegow(prz: pd.DataFrame, lat: pd.DataFrame) -> pd.Series:
    """przebiegi.csv nie ma daty: doba = blok między cofnięciami godziny, a datę bloku
    daje wiersz final (nr + koniec) dopasowany do latencja.csv (nr + final).
    Godzina z kolumny „koniec” — wiersze są zapisywane po kolei w chwili końca przebiegu
    („start” wyniku strumieniowego to koniec objętego dźwięku i bywa wcześniejszy niż poprzedni)."""
    blok = (sekundy(prz["koniec"]).diff() < 0).cumsum()
    finaly = prz[prz["final"].astype(str) == "True"].assign(blok=blok)
    para = finaly.merge(lat[["nr", "final", "data"]], left_on=["nr", "koniec"], right_on=["nr", "final"], how="inner")
    mapa = para.groupby("blok")["data"].agg(lambda s: s.mode().iloc[0]).to_dict()
    brak = sorted(set(blok.unique()) - set(mapa))
    daty = list(pd.unique(lat["data"].dropna()))
    if brak and len(daty) == blok.nunique():  # zapas: kolejność dni taka jak w latencja.csv
        mapa.update({b: daty[b] for b in brak})
    return blok.map(mapa)


def wczytaj(folder: Path, od: str, do: str, data: str | None):
    lat = czytaj(folder, "latencja.csv")
    prz = czytaj(folder, "przebiegi.csv")
    if data is None:
        data = str(lat["data"].dropna().iloc[-1])
    prz["data"] = daty_przebiegow(prz, lat)
    bez_daty = int(prz["data"].isna().sum())
    t0, t1 = hhmm(od), hhmm(do)
    tl, tp = sekundy(lat["dzwiek_start"]), sekundy(prz["start"])
    lat = lat[(lat["data"] == data) & (tl >= t0) & (tl < t1)].copy()
    prz = prz[(prz["data"] == data) & (tp >= t0) & (tp < t1)].copy()
    lat["model"] = lat["model"].map(nazwa_modelu)
    prz["model"] = prz["model"].map(nazwa_modelu)
    lat["kawalek"] = "inne"
    for nazwa, lo, hi in KAWALKI:
        lat.loc[(lat["dlugosc_mowy_ms"] >= lo) & (lat["dlugosc_mowy_ms"] < hi), "kawalek"] = nazwa
    prz["przedzial"] = pd.cut(prz["bufor_s"], BUFOR_PROGI, labels=BUFOR_ETYKIETY, right=False)
    return {"lat": lat, "prz": prz, "data": data, "od": od, "do": do, "folder": folder, "bez_daty": bez_daty}


def liczba(x, miejsca=0) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x:.{miejsca}f}"


def roznica(teraz, baza) -> str:
    if teraz is None or baza is None or pd.isna(teraz) or pd.isna(baza) or baza == 0:
        return "—"
    return f"{(teraz - baza) / baza * 100:+.0f}%"


def tabela(naglowek: list[str], wiersze: list[list[str]]) -> None:
    szer = [max(len(str(w[i])) for w in [naglowek] + wiersze) for i in range(len(naglowek))]
    def linia(w):
        return "  " + "  ".join(str(k).ljust(s) if i == 0 else str(k).rjust(s) for i, (k, s) in enumerate(zip(w, szer)))
    print(linia(naglowek))
    print("  " + "  ".join("-" * s for s in szer))
    for w in wiersze:
        print(linia(w))
    print()


def komorki(teraz, baza, jest_baza: bool, miejsca=0) -> list[str]:
    """Jedna wartość -> [teraz] albo [teraz, baseline, różnica %]."""
    if not jest_baza:
        return [liczba(teraz, miejsca)]
    return [liczba(teraz, miejsca), liczba(baza, miejsca), roznica(teraz, baza)]


def naglowki(nazwy: list[str], jest_baza: bool) -> list[str]:
    if not jest_baza:
        return nazwy
    return [x for n in nazwy for x in (n, "baza", "Δ%")]


def statystyki(s: pd.Series) -> tuple:
    s = s.dropna()
    if s.empty:
        return (None, None, None)
    return (s.median(), s.quantile(0.9), s.max())


def petle(prz: pd.DataFrame) -> tuple:
    p = prz[prz["tokeny"] >= PETLA_TOKENY]
    razem = prz["asr_ms"].sum()
    czas = p["asr_ms"].sum()
    return (len(p), czas / 1000, czas / razem * 100 if razem else None)


def raport_modelu(model: str, t: dict, b: dict | None) -> None:
    jb = b is not None
    lat, prz = t["lat"][t["lat"]["model"] == model], t["prz"][t["prz"]["model"] == model]
    blat = b["lat"][b["lat"]["model"] == model] if jb else lat.iloc[0:0]
    bprz = b["prz"][b["prz"]["model"] == model] if jb else prz.iloc[0:0]

    print("=" * 100)
    print(f"MODEL: {model}")
    print("=" * 100)
    if lat.empty and prz.empty and blat.empty and bprz.empty:
        print("  brak danych w zakresie\n")
        return
    print("  wypowiedzi: " + " | ".join(komorki(len(lat), len(blat), jb))
          + "    przebiegi ASR: " + " | ".join(komorki(len(prz), len(bprz), jb))
          + ("    (teraz | baza | Δ%)" if jb else "") + "\n")

    print("  Opóźnienia wypowiedzi [ms]")
    wiersze = []
    for m in METRYKI:
        st, sb = statystyki(lat[m]), statystyki(blat[m])
        w = [m, str(int(lat[m].notna().sum()))]
        for i in range(3):
            w += komorki(st[i], sb[i], jb)
        wiersze.append(w)
    tabela(["metryka", "n"] + naglowki(["mediana", "p90", "maks"], jb), wiersze)

    print("  Przebiegi ASR wg długości bufora (wszystkie przebiegi, także final)")
    wiersze = []
    for e in BUFOR_ETYKIETY:
        x, y = prz[prz["przedzial"] == e], bprz[bprz["przedzial"] == e]
        w = [e] + komorki(len(x), len(y), jb)
        w += komorki(x["asr_ms"].median() if len(x) else None, y["asr_ms"].median() if len(y) else None, jb)
        w += komorki(x["tokeny"].median() if len(x) else None, y["tokeny"].median() if len(y) else None, jb)
        wiersze.append(w)
    tabela(["bufor"] + naglowki(["przebiegi", "mediana asr_ms", "mediana tokenów"], jb), wiersze)
    poza = int(prz["przedzial"].isna().sum())
    if poza:
        print(f"  (poza przedziałami, bufor >= {BUFOR_PROGI[-1]} s: {poza} przebiegów)\n")

    print(f"  Pętle (przebiegi z tokeny >= {PETLA_TOKENY})")
    pt, pb = petle(prz), petle(bprz) if jb else (None, None, None)
    tabela(["", *naglowki(["wartość"], jb)], [
        ["liczba pętli"] + komorki(pt[0], pb[0], jb),
        ["łączny czas pętli [s]"] + komorki(pt[1], pb[1], jb, 1),
        ["cały czas ASR [s]"] + komorki(prz["asr_ms"].sum() / 1000, bprz["asr_ms"].sum() / 1000 if jb else None, jb, 1),
        ["udział pętli w czasie ASR [%]"] + komorki(pt[2], pb[2], jb, 1),
    ])

    print("  Per kawałek nagrania — mediany [ms] (klasyfikacja po dlugosc_mowy_ms)")
    wiersze = []
    for k in KAWALKI_KOLEJNOSC:
        x, y = lat[lat["kawalek"] == k], blat[blat["kawalek"] == k]
        if x.empty and y.empty:
            continue
        w = [k] + komorki(len(x), len(y), jb)
        w += komorki(x["dlugosc_mowy_ms"].median(), y["dlugosc_mowy_ms"].median(), False)
        for m in KAWALEK_METRYKI:
            w += komorki(x[m].median(), y[m].median(), jb)
        wiersze.append(w)
    krotkie = ["glos→1.tekst", "final po końcu", "cisza do końca", "asr_final"]
    tabela(["kawałek"] + naglowki(["n"], jb) + ["mowa ms"] + naglowki(krotkie, jb), wiersze)


def opis(z: dict, nazwa: str) -> None:
    lat, prz = z["lat"], z["prz"]
    print(f"{nazwa}: {z['folder']}  |  {z['data']}  {z['od']}-{z['do']}  |  "
          f"wypowiedzi: {len(lat)}, przebiegi ASR: {len(prz)}")
    uwagi = []
    if z["bez_daty"]:
        uwagi.append(f"{z['bez_daty']} przebiegów bez ustalonej daty (pominięte)")
    for m in METRYKI + ["dlugosc_mowy_ms"]:
        n = int(lat[m].isna().sum())
        if n:
            uwagi.append(f"{m}: brak wartości w {n} wypowiedziach")
    n = int(prz["tokeny"].isna().sum())
    if n:
        uwagi.append(f"tokeny: brak wartości w {n} przebiegach")
    n = int((lat["kawalek"] == "inne").sum())
    if n:
        uwagi.append(f"{n} z {len(lat)} wypowiedzi nie pasuje długością do żadnego kawałka (inny podział VAD) -> 'inne'")
    for u in uwagi:
        print(f"  ! {u}")
    print()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    domyslny = Path(os.environ.get("LOCALAPPDATA", ".")) / "BAPO-dyktowanie"
    ap = argparse.ArgumentParser(description="Analiza testu opóźnień dyktowania.")
    ap.add_argument("--od", required=True, help="początek zakresu HH:MM")
    ap.add_argument("--do", required=True, help="koniec zakresu HH:MM (bez tej minuty)")
    ap.add_argument("--data", help="RRRR-MM-DD; domyślnie ostatnia data w latencja.csv")
    ap.add_argument("--folder", type=Path, default=domyslny, help=f"folder z dziennikami (domyślnie {domyslny})")
    ap.add_argument("--baseline", type=Path, help="folder baseline do porównania")
    ap.add_argument("--baseline-od", help="zakres w baseline; domyślnie zakres.json z folderu baseline albo --od")
    ap.add_argument("--baseline-do")
    ap.add_argument("--baseline-data")
    a = ap.parse_args()

    teraz = wczytaj(a.folder, a.od, a.do, a.data)
    baza = None
    if a.baseline:
        z = {}
        plik = a.baseline / "zakres.json"
        if plik.exists():
            z = json.loads(plik.read_text(encoding="utf-8"))
        baza = wczytaj(a.baseline, a.baseline_od or z.get("od", a.od), a.baseline_do or z.get("do", a.do),
                       a.baseline_data or z.get("data"))

    opis(teraz, "TERAZ   " if baza else "DANE")
    if baza:
        opis(baza, "BASELINE")
    for model in modele_w_danych(teraz, baza):
        raport_modelu(model, teraz, baza)


if __name__ == "__main__":
    main()
