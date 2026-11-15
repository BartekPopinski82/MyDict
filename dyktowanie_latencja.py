"""Pomiar latencji dyktowania — znaczniki czasu każdej wypowiedzi w dzienniku.

Dla każdej wypowiedzi (#N) zapisuje:

* DŹWIĘK START   — kiedy w mikrofonie realnie pojawiła się mowa (czas próbki
                   z zegara ADC karty dźwiękowej, nie moment przetworzenia),
* VAD START      — kiedy program wykrył, że ktoś mówi,
* 1. PEWNY TEKST — pierwsze zatwierdzone słowa (tryb lokalny),
* DŹWIĘK KONIEC  — ostatnia ramka mowy,
* VAD KONIEC     — kiedy cisza zakończyła wypowiedź,
* FINAL          — gotowy tekst po końcowym przebiegu modelu,
* OKIENKO START  — pierwszy tekst na pływającym pasku (także szary, niepewny),
* OKIENKO PEWNY  — pierwszy pewny (biały) tekst na pasku,
* OKIENKO FINAL  — pasek pokazał tekst domkniętej wypowiedzi,
* WPISYWANIE START / KONIEC — pierwszy i ostatni znak wysłany do okna
                   (moment powrotu z SendInput).

Pasek pokazuje od razu tekst niepewny, a do okna trafia tylko pewny — stąd
różnica „okienko → aplikacja”, którą podsumowanie podaje osobno.

Oprócz tego (żeby wiedzieć, CO zjada czas) zapisuje:

* PRZEBIEG k    — każdy przebieg modelu: długość bufora, czas ASR, odstęp od poprzedniego,
  a w drugiej linii parametry dekodowania, nowe audio, prompt, segmenty, okna dekodera,
  tokeny i tok/s, fazy czasu, CPU wątku; w trzeciej — GPU i CPU w trakcie przebiegu
  (z monitora zasobów). Przebieg dłuższy niż ``ANOMALY_SECONDS`` dostaje znacznik
  ANOMALIA i pełny zrzut segmentów. Każdy przebieg (i FINAL) to też wiersz w przebiegi.csv.
* ZALEGŁOŚĆ AUDIO — silnik przetwarza dźwięk sprzed X ms (nie nadąża za mową),
* UI            — obsługa tekstu w interfejsie (w tym odświeżanie całej transkrypcji)
                  i zablokowanie wątku interfejsu (timer 33 ms spóźniony),
* WPISYWANIE WSTRZYMANE — tekst gotowy, ale czekał na okno / klawisze,
* BILANS START / KONIEC — rozbicie czasu na składniki i wskazanie największego.

Zero odliczania to POCZĄTEK GŁOSU z poziomu mikrofonu (RMS co 10 ms, próg nad szumem
tła, kliknięcia odrzucane) — to samo, co widać po falach na pasku — a nie moment,
w którym detektor mowy (VAD) uzbierał ramki i przekroczył próg. Linia GŁOS START
pokazuje, o ile później VAD zobaczył ten sam głos; OD GŁOSU zbiera czasy do paska,
tekstu i znaków w aplikacji liczone od początku głosu.

Modele strumieniowe (chmura, np. Deepgram) zgłaszają te same zdarzenia, liczone z czasów
słów podanych przez usługę: VAD START = pierwszy wynik z tekstem, PRZEBIEG = każdy wynik
(„ASR” to opóźnienie wyniku względem końca dźwięku, który obejmuje — sieć + model usługi),
VAD KONIEC i FINAL = wynik kończący wypowiedź (cała zwłoka trafia do „pauzy kończącej”).

Na końcu linia PODSUMOWANIE z opóźnieniami w ms. Gdy ``configure()`` dostanie
ścieżkę, podsumowania trafiają też do pliku CSV (jeden wiersz na wypowiedź).

Wszystkie czasy są z ``time.perf_counter()``; do dziennika przeliczane na
godzinę z milisekundami. Błąd pomiaru nigdy nie przerywa dyktowania.
"""

from __future__ import annotations

import csv
import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

log = logging.getLogger("dyktowanie.latencja")

_PERF0 = time.perf_counter()
_WALL0 = time.time()

# Detektor początku głosu z poziomu mikrofonu (niezależny od VAD).
LEVEL_BLOCK = 0.010          # s — rozdzielczość (blok 10 ms)
LEVEL_MIN = 0.004            # RMS ≈ −48 dBFS: poniżej tego nigdy nie uznajemy za głos
LEVEL_RATIO = 3.0            # ile razy ponad szum tła (≈ +9,5 dB)
LEVEL_CONFIRM = 4            # tyle bloków z rzędu (40 ms) odróżnia głos od kliknięcia
LEVEL_GAP = 0.30             # s ciszy kończy odcinek głosu
VOICE_MAX_BEFORE_VAD = 1.0   # s: poziom sprzed dłuższego czasu to raczej inny hałas

ANOMALY_SECONDS = 2.0        # przebieg ASR dłuższy niż to = ANOMALIA (pełny zrzut segmentów)

PASS_CSV_COLUMNS = [
    "nr", "przebieg", "start", "koniec", "start_perf", "asr_ms", "bufor_s", "nowe_audio_s",
    "odstep_ms", "model", "compute_type", "final", "beam_size", "best_of", "temperature",
    "patience", "condition_on_previous_text", "word_timestamps", "max_new_tokens",
    "prompt_znaki", "prompt_tokeny", "hotwords_tokeny", "segmenty", "odrzucone", "okna",
    "tokeny", "tokeny_na_s", "slowa", "przygotowanie_ms", "pierwszy_segment_ms", "reszta_ms",
    "cpu_watku_ms", "logprob_min", "kompresja_max", "no_speech_max", "temperatury_segmentow",
    "seek", "gpu_probki", "gpu_util_min", "gpu_util_sr", "gpu_util_max", "sm_mhz_min",
    "sm_mhz_max", "mem_mhz_min", "mem_mhz_max", "pstate", "moc_max_w", "temp_max_c",
    "vram_max_mb", "ograniczenia_zegara", "cpu_proces_sr", "cpu_proces_max", "cpu_system_max",
    "anomalia", "tekst",
]

CSV_COLUMNS = [
    "nr", "data",
    "dzwiek_start", "vad_start", "pierwszy_tekst", "dzwiek_koniec", "vad_koniec", "final",
    "okienko_start", "okienko_pewny", "okienko_final",
    "wpisywanie_start", "wpisywanie_koniec",
    "vad_wykrycie_ms", "pierwszy_tekst_ms", "okienko_start_ms", "okienko_pewny_ms",
    "wpisywanie_start_ms", "okienko_do_aplikacji_ms", "okienko_final_po_koncu_ms",
    "cisza_do_konca_ms", "final_po_koncu_ms", "wpisywanie_koniec_po_koncu_ms",
    "czas_wpisywania_ms", "dlugosc_mowy_ms",
    "znaki", "przebiegi_asr", "asr_sredni_ms", "asr_final_ms", "uwagi",
    # bilans: od początku mowy do 1. znaku w aplikacji
    "b_start_vad_ms", "b_start_asr_ms", "b_start_silnik_czeka_ms", "b_start_qt_ms",
    "b_start_wstrzymane_ms", "b_start_kolejka_wpisywania_ms", "b_start_najwiecej",
    # bilans: od końca mowy do ostatniego znaku w aplikacji
    "b_koniec_cisza_ms", "b_koniec_silnik_zajety_ms", "b_koniec_asr_final_ms", "b_koniec_qt_ms",
    "b_koniec_wpisywanie_ms", "b_koniec_najwiecej",
    # diagnostyka
    "model", "asr_max_ms", "bufor_max_s", "odstep_max_ms", "przebiegi_wolniejsze_niz_krok",
    "kolejka_audio_max_ms", "vad_max_ms", "ui_obsluga_max_ms", "ui_odswiezanie_max_ms",
    "ui_zablokowany_max_ms", "sendinput_max_ms", "wstrzymane_ms",
    "wpisywanie_lag_max_ms", "wpisywanie_lag_sr_ms", "bufor_final_s",
    # liczone od POCZĄTKU GŁOSU (poziom mikrofonu); gdy go nie wykryto — od VAD
    "glos_start", "glos_przed_vad_ms", "glos_do_wykrycia_ms", "glos_do_paska_ms",
    "glos_do_pierwszego_tekstu_ms", "glos_do_1_znaku_ms", "glos_do_ostatniego_znaku_ms",
]


def now() -> float:
    return time.perf_counter()


def clock(t: float | None) -> str:
    if t is None:
        return "—"
    return datetime.fromtimestamp(_WALL0 + (t - _PERF0)).strftime("%H:%M:%S.%f")[:-3]


def _ms(a: float | None, b: float | None) -> int | None:
    if a is None or b is None:
        return None
    return int(round((b - a) * 1000))


def _s_ms(seconds: float | None) -> int | None:
    return None if seconds is None else int(round(seconds * 1000))


def _round(value, digits: int):
    return None if value is None else round(value, digits)


def _fmt_ms(value: int | None) -> str:
    return "—" if value is None else f"{value:+d} ms"


class _Utterance:
    def __init__(self, number: int):
        self.number = number
        self.audio_start: float | None = None
        self.vad_start: float | None = None
        self.first_text: float | None = None
        self.audio_end: float | None = None
        self.vad_end: float | None = None
        self.final: float | None = None
        self.asr_times: list[float] = []
        self.asr_final: float | None = None
        self.start_char: int | None = None
        self.end_char: int | None = None
        self.ui_final = False
        self.overlay_first: float | None = None
        self.overlay_stable: float | None = None
        self.overlay_final: float | None = None
        self.type_first: float | None = None
        self.type_last: float | None = None
        self.typed_chars = 0
        self.notes: list[str] = []
        # --- składniki opóźnienia
        self.first_from_final = False
        self.asr_before_first = 0.0   # suma czasów ASR przebiegów do 1. pewnego tekstu
        self.asr_max = 0.0
        self.buffer_max = 0.0
        self.interval_max = 0.0
        self.slow_passes = 0          # przebiegi dłuższe niż krok (silnik nie nadąża)
        self.final_buffer: float | None = None
        self.queue_age_max = 0.0      # najstarszy dźwięk czekający na silnik
        self.vad_max = 0.0
        self.ui_cost_max = 0.0
        self.ui_render_max = 0.0
        self.ui_block_max = 0.0
        self.blocked = 0.0            # wstrzymanie wpisywania (okno / klawisze), łącznie
        self.blocked_first = 0.0      # ... w tym przed pierwszym znakiem
        self.inject_max = 0.0
        self.handoffs: list[tuple[int, float]] = []  # (długość pewnego tekstu, kiedy przekazano)
        self.lag_max = 0.0
        self.lag_sum = 0.0
        self.lag_n = 0
        self.model = ""
        self.voice_start: float | None = None   # początek głosu wg poziomu mikrofonu


class LatencyTracker:
    """Zbiera zdarzenia z trzech wątków: silnika, interfejsu i wpisywania."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counter = 0
        self._engine: _Utterance | None = None
        self._ui_queue: deque[_Utterance] = deque()
        self._typing: list[_Utterance] = []
        self._ui_pos = 0
        self._done = 0
        self._csv_path: Path | None = None
        self._pass_csv_path: Path | None = None
        # Monitor zasobów: resource_summary(t0, t1) -> dict | None (ustawia silnik).
        self.resource_summary = None
        self._clock_logged = False
        self.model = ""
        self._ui_last: _Utterance | None = None
        self._tick: float | None = None
        self._last_queue_log = 0.0
        # detektor początku głosu
        self._levels_seen = False                 # czy mikrofon w ogóle dostarczał poziom
        self._floor: float | None = None          # szum tła (RMS)
        self._loud_run = 0
        self._quiet_for = 0.0
        self._prov: float | None = None           # wstępny początek (czeka na potwierdzenie)
        self._open: list[float] | None = None     # bieżący odcinek głosu [początek, koniec]
        self._segments: deque[list[float]] = deque(maxlen=64)

    # -- konfiguracja ---------------------------------------------------------

    def configure(self, csv_path: Path | None):
        self._csv_path = csv_path
        self._pass_csv_path = None if csv_path is None else csv_path.with_name("przebiegi.csv")
        if csv_path is not None:
            log.info("Pomiar latencji włączony; CSV: %s, przebiegi: %s", csv_path, self._pass_csv_path)

    def microphone_clock(self, adc: bool):
        if not self._clock_logged:
            self._clock_logged = True
            log.info("Znaczniki czasu dźwięku: %s",
                     "zegar ADC karty dźwiękowej" if adc else "przybliżone (moment odebrania bloku, ±50 ms)")

    # -- wątek silnika (LiveStreamer.on_timing) --------------------------------

    def stream_event(self, kind: str, **data):
        try:
            with self._lock:
                self._stream_event(kind, data)
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    def _stream_event(self, kind: str, data: dict):
        t = now()
        if kind == "speech_start":
            if self._engine is not None:
                self._engine.notes.append("przerwana")
            self._counter += 1
            utt = _Utterance(self._counter)
            utt.audio_start = data.get("audio_start")
            utt.vad_start = t
            utt.model = self.model
            utt.voice_start = self._voice_for(utt.audio_start)
            if utt.voice_start is not None:
                log.info("[LAT #%d] GŁOS START %s (poziom mikrofonu) | VAD zobaczył ten głos %s później "
                         "(DŹWIĘK START %s) | program wykrył go %s po początku głosu", utt.number,
                         clock(utt.voice_start), _fmt_ms(_ms(utt.voice_start, utt.audio_start)),
                         clock(utt.audio_start), _fmt_ms(_ms(utt.voice_start, t)))
            elif utt.audio_start is not None and self._levels_seen:
                utt.notes.append("początek głosu: poziom mikrofonu nie przekroczył progu — zero = VAD")
                log.info("[LAT #%d] GŁOS START: brak progu poziomu mikrofonu — zero = VAD (DŹWIĘK START %s)",
                         utt.number, clock(utt.audio_start))
            self._engine = utt
            self._ui_queue.append(utt)
            log.info("[LAT #%d] DŹWIĘK START %s | VAD START %s (%s)", utt.number,
                     clock(utt.audio_start), clock(t), _fmt_ms(_ms(utt.audio_start, t)))
            return
        utt = self._engine
        if utt is None:
            return
        if kind == "pass":
            asr = data.get("asr")
            if asr is not None:
                utt.asr_times.append(asr)
                utt.asr_max = max(utt.asr_max, asr)
                if utt.first_text is None:
                    utt.asr_before_first += asr
                if data.get("step") is not None and asr > data["step"]:
                    utt.slow_passes += 1
            buffer, interval = data.get("buffer"), data.get("interval")
            if buffer:
                utt.buffer_max = max(utt.buffer_max, buffer)
            if interval:
                utt.interval_max = max(utt.interval_max, interval)
            log.info("[LAT #%d] PRZEBIEG %d: bufor %s s | ASR %s ms | odstęp od poprzedniego %s ms%s",
                     utt.number, len(utt.asr_times),
                     "—" if not buffer else f"{buffer:.1f}",
                     "—" if asr is None else f"{asr * 1000:.0f}",
                     "—" if interval is None else f"{interval * 1000:.0f}",
                     " | WOLNIEJSZY NIŻ KROK" if (asr is not None and data.get("step") is not None
                                                  and asr > data["step"]) else "")
            self._pass_details(utt, f"PRZEBIEG {len(utt.asr_times)}", str(len(utt.asr_times)), data)
            if data.get("delta") and utt.first_text is None:
                utt.first_text = t
                log.info("[LAT #%d] 1. PEWNY TEKST %s (%s od dźwięku) %r", utt.number, clock(t),
                         _fmt_ms(_ms(utt.audio_start, t)), data["delta"].strip()[:40])
        elif kind == "speech_end":
            utt.audio_end = data.get("audio_end")
            utt.vad_end = t
            log.info("[LAT #%d] DŹWIĘK KONIEC %s | VAD KONIEC %s (%s ciszy)", utt.number,
                     clock(utt.audio_end), clock(t), _fmt_ms(_ms(utt.audio_end, t)))
        elif kind == "final":
            utt.final = t
            utt.asr_final = data.get("asr")
            utt.final_buffer = data.get("buffer")
            if data.get("delta") and utt.first_text is None:
                utt.first_text = t
                utt.first_from_final = True
            log.info("[LAT #%d] FINAL %s (%s od końca dźwięku, model %s)", utt.number, clock(t),
                     _fmt_ms(_ms(utt.audio_end, t)),
                     "—" if utt.asr_final is None else f"{utt.asr_final * 1000:.0f} ms")
            if data.get("asr") is not None:
                self._pass_details(utt, "FINAL", "final", data)
            self._engine = None

    # -- diagnostyka przebiegu ASR ----------------------------------------------

    def _pass_details(self, utt: _Utterance, label: str, number: str, data: dict):
        """Druga i trzecia linia przebiegu (parametry/tokeny oraz GPU/CPU) + wiersz przebiegi.csv."""
        try:
            stats = data.get("stats") or {}
            asr = data.get("asr")
            started, ended = data.get("started"), data.get("ended")
            anomaly = asr is not None and asr >= ANOMALY_SECONDS
            res = None
            if self.resource_summary is not None and started is not None and ended is not None:
                try:
                    res = self.resource_summary(started, ended)
                except Exception:
                    log.debug("Błąd podsumowania zasobów", exc_info=True)
            segs = stats.get("segment_list") or []
            tag = " | ANOMALIA" if anomaly else ""

            def num(v, fmt="{:.0f}", none="—"):
                return none if v is None else fmt.format(v)

            if stats:
                log.info(
                    "[LAT #%d] %s szczegóły: %s %s | beam %s, best_of %s, temp %s, patience %s, "
                    "cond_prev %s, word_ts %s, max_new_tokens %s | audio %.2f s (+%s s nowego) | "
                    "prompt %d zn./%s tok., hotwords %s tok. | segmenty %d (odrzucone %d), okna %d, "
                    "tokeny %s → %s tok/s, słowa %d | fazy: przygotowanie %s ms, 1. segment %s ms, "
                    "reszta %s ms | CPU wątku %s ms%s",
                    utt.number, label, stats.get("model", ""), stats.get("compute_type", ""),
                    stats.get("beam_size"), stats.get("best_of"), stats.get("temperature"),
                    stats.get("patience"), stats.get("condition_on_previous_text"),
                    stats.get("word_timestamps"),
                    "brak (limit modelu)" if stats.get("max_new_tokens") is None else stats.get("max_new_tokens"),
                    stats.get("audio_s", 0.0), num(data.get("new_audio"), "{:.2f}"),
                    stats.get("prompt_chars", 0), num(stats.get("prompt_tokens"), "{}"),
                    num(stats.get("hotwords_tokens"), "{}"),
                    stats.get("segments", 0), stats.get("rejected", 0), stats.get("windows", 0),
                    num(stats.get("tokens"), "{}"), num(stats.get("tokens_per_s")),
                    stats.get("words", 0),
                    num(_s_ms(stats.get("prepare_s"))), num(_s_ms(stats.get("first_segment_s"))),
                    num(_s_ms(stats.get("rest_s"))), num(_s_ms(stats.get("thread_cpu_s"))), tag,
                )
                log.info("[LAT #%d] %s segmenty: %s", utt.number, label, "; ".join(
                    f"seek {s.get('seek')} {num(s.get('start'), '{:.2f}')}–{num(s.get('end'), '{:.2f}')} s, "
                    f"{num(s.get('tokens'), '{}')} tok, logprob {num(s.get('avg_logprob'), '{:.2f}')}, "
                    f"kompr {num(s.get('compression_ratio'), '{:.2f}')}, "
                    f"no_speech {num(s.get('no_speech_prob'), '{:.2f}')}, T {num(s.get('temperature'), '{}')}"
                    f"{', ODRZUCONY' if s.get('rejected') else ''}"
                    for s in segs) or "brak")
                if anomaly:
                    for index, s in enumerate(segs, 1):
                        log.info("[LAT #%d] %s ANOMALIA segment %d: %r", utt.number, label, index, s.get("text", ""))
            if res:
                log.info(
                    "[LAT #%d] %s zasoby (%d próbek): GPU util %s/%s/%s %% (min/śr/max) | SM %s–%s MHz | "
                    "pamięć %s–%s MHz | %s | moc max %s W, temp max %s °C | VRAM %s MB | "
                    "ograniczenia zegara: %s | CPU procesu śr %s %% max %s %% (100 = 1 rdzeń), "
                    "system max %s %%%s",
                    utt.number, label, res.get("samples", 0),
                    num(res.get("util_min")), num(res.get("util_avg")), num(res.get("util_max")),
                    num(res.get("sm_min")), num(res.get("sm_max")),
                    num(res.get("mem_mhz_min")), num(res.get("mem_mhz_max")),
                    res.get("pstate") or "—", num(res.get("power_max")), num(res.get("temp_max")),
                    num(res.get("vram_max")), res.get("reasons") or "brak",
                    num(res.get("cpu_proc_avg")), num(res.get("cpu_proc_max")),
                    num(res.get("cpu_sys_max")), tag,
                )
            res = res or {}
            # Modele strumieniowe (chmura) nie mają statystyk dekodera: model i tekst z samego zdarzenia.
            texts = " | ".join(s.get("text", "").strip() for s in segs) or (data.get("text") or "")
            _append_csv(self._pass_csv_path, PASS_CSV_COLUMNS, {
                "nr": utt.number, "przebieg": number, "start": clock(started), "koniec": clock(ended),
                "start_perf": None if started is None else f"{started:.3f}",
                "asr_ms": _s_ms(asr), "bufor_s": _round(data.get("buffer"), 2),
                "nowe_audio_s": _round(data.get("new_audio"), 2), "odstep_ms": _s_ms(data.get("interval")),
                "model": stats.get("model") or utt.model, "compute_type": stats.get("compute_type"),
                "final": stats.get("final") if stats else number == "final", "beam_size": stats.get("beam_size"),
                "best_of": stats.get("best_of"), "temperature": stats.get("temperature"),
                "patience": stats.get("patience"),
                "condition_on_previous_text": stats.get("condition_on_previous_text"),
                "word_timestamps": stats.get("word_timestamps"), "max_new_tokens": stats.get("max_new_tokens"),
                "prompt_znaki": stats.get("prompt_chars"), "prompt_tokeny": stats.get("prompt_tokens"),
                "hotwords_tokeny": stats.get("hotwords_tokens"), "segmenty": stats.get("segments"),
                "odrzucone": stats.get("rejected"), "okna": stats.get("windows"),
                "tokeny": stats.get("tokens"), "tokeny_na_s": _round(stats.get("tokens_per_s"), 1),
                "slowa": stats.get("words"), "przygotowanie_ms": _s_ms(stats.get("prepare_s")),
                "pierwszy_segment_ms": _s_ms(stats.get("first_segment_s")),
                "reszta_ms": _s_ms(stats.get("rest_s")), "cpu_watku_ms": _s_ms(stats.get("thread_cpu_s")),
                "logprob_min": _round(min((s["avg_logprob"] for s in segs if s.get("avg_logprob") is not None), default=None), 3),
                "kompresja_max": _round(max((s["compression_ratio"] for s in segs if s.get("compression_ratio") is not None), default=None), 2),
                "no_speech_max": _round(max((s["no_speech_prob"] for s in segs if s.get("no_speech_prob") is not None), default=None), 3),
                "temperatury_segmentow": ",".join(str(s.get("temperature")) for s in segs) or None,
                "seek": ",".join(str(s.get("seek")) for s in segs) or None,
                "gpu_probki": res.get("samples"), "gpu_util_min": _round(res.get("util_min"), 0),
                "gpu_util_sr": _round(res.get("util_avg"), 0), "gpu_util_max": _round(res.get("util_max"), 0),
                "sm_mhz_min": res.get("sm_min"), "sm_mhz_max": res.get("sm_max"),
                "mem_mhz_min": res.get("mem_mhz_min"), "mem_mhz_max": res.get("mem_mhz_max"),
                "pstate": res.get("pstate"), "moc_max_w": _round(res.get("power_max"), 0),
                "temp_max_c": res.get("temp_max"), "vram_max_mb": res.get("vram_max"),
                "ograniczenia_zegara": res.get("reasons"),
                "cpu_proces_sr": _round(res.get("cpu_proc_avg"), 0), "cpu_proces_max": _round(res.get("cpu_proc_max"), 0),
                "cpu_system_max": _round(res.get("cpu_sys_max"), 0),
                "anomalia": "tak" if anomaly else "", "tekst": texts[:300] or None,
            })
        except Exception:
            log.debug("Błąd diagnostyki przebiegu", exc_info=True)

    # -- wątek mikrofonu: początek głosu -------------------------------------------

    def mic_levels(self, levels, end_time: float | None):
        """RMS kolejnych bloków 10 ms; ostatni blok kończy się o ``end_time`` (perf_counter).

        Początek głosu = początek pierwszego bloku, który przekroczył próg (3× szum tła,
        nie mniej niż −48 dBFS), o ile głośno było przez co najmniej 40 ms. Pojedyncze
        impulsy (kliknięcia) są odrzucane, a odcinek głosu kończy dopiero 0,3 s ciszy."""
        if end_time is None or not len(levels):
            return
        try:
            with self._lock:
                self._levels_seen = True
                count = len(levels)
                for index, level in enumerate(levels):
                    block_end = end_time - (count - 1 - index) * LEVEL_BLOCK
                    if self._floor is None:
                        self._floor = max(level, 1e-5)
                    threshold = max(LEVEL_MIN, self._floor * LEVEL_RATIO)
                    # Szum tła: opada od razu do poziomu bloku, rośnie powoli (+0,2% na 10 ms),
                    # więc głos go nie podnosi, a nowy, stały szum po kilkunastu sekundach tak.
                    if level < self._floor:
                        self._floor = max(level, 1e-5)
                    else:
                        self._floor = min(self._floor * 1.002, level)
                    if level >= threshold:
                        self._quiet_for = 0.0
                        self._loud_run += 1
                        if self._open is None:
                            if self._prov is None:
                                self._prov = block_end - LEVEL_BLOCK
                            if self._loud_run >= LEVEL_CONFIRM:
                                self._open = [self._prov, block_end]
                                self._segments.append(self._open)
                                self._prov = None
                        else:
                            self._open[1] = block_end
                    else:
                        if self._open is None:
                            self._prov = None  # krótki impuls, nie głos
                        self._loud_run = 0
                        self._quiet_for += LEVEL_BLOCK
                        if self._quiet_for >= LEVEL_GAP:
                            self._open = None
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    def _voice_for(self, audio_start: float | None) -> float | None:
        """Początek głosu dla wypowiedzi, którą VAD zobaczył od ``audio_start``.
        Szukamy odcinka głosu obejmującego ten moment (silnik bywa spóźniony względem
        mikrofonu, więc bierzemy historię, a nie tylko bieżący odcinek)."""
        if audio_start is None:
            return None
        best = None
        for start, end in self._segments:
            if (start - LEVEL_GAP <= audio_start <= end + LEVEL_GAP
                    and audio_start - start <= VOICE_MAX_BEFORE_VAD):
                if best is None or start > best:
                    best = start
        return None if best is None else min(best, audio_start)

    def engine_loop(self, push_seconds: float, step_seconds: float, oldest_age: float):
        """Jedna pętla wątku roboczego: ile trwał VAD (``push``), cały ``step`` i jak dawno
        zarejestrowano najstarszy dźwięk, który dopiero teraz trafił do przetwarzania."""
        try:
            with self._lock:
                utt = self._engine
                if utt is not None:
                    utt.vad_max = max(utt.vad_max, push_seconds)
                    utt.queue_age_max = max(utt.queue_age_max, oldest_age)
                t = now()
                if oldest_age > 1.0 and t - self._last_queue_log > 2.0:
                    self._last_queue_log = t
                    log.info("[LAT%s] ZALEGŁOŚĆ AUDIO: silnik przetwarza dźwięk sprzed %.0f ms "
                             "(VAD %.0f ms, krok razem %.0f ms) — nie nadąża za mową",
                             f" #{utt.number}" if utt else "", oldest_age * 1000,
                             push_seconds * 1000, step_seconds * 1000)
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    # -- wątek interfejsu ----------------------------------------------------

    def ui_cost(self, handler_seconds: float, render_seconds: float, archive: int, chars: int):
        """Obsługa tekstu w interfejsie: ``handler`` = pasek + przekazanie do wpisywania,
        ``render`` = odświeżenie całej transkrypcji w oknie głównym (rośnie z długością sesji)."""
        try:
            with self._lock:
                utt = self._ui_last
                if utt is not None:
                    utt.ui_cost_max = max(utt.ui_cost_max, handler_seconds + render_seconds)
                    utt.ui_render_max = max(utt.ui_render_max, render_seconds)
                if render_seconds > 0.1 or handler_seconds > 0.05:
                    log.info("[LAT%s] UI: obsługa tekstu %.0f ms (pasek+wpisywanie %.0f ms, odświeżanie "
                             "transkrypcji %.0f ms; w archiwum %d wypowiedzi, %d znaków)",
                             f" #{utt.number}" if utt else "", (handler_seconds + render_seconds) * 1000,
                             handler_seconds * 1000, render_seconds * 1000, archive, chars)
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    def ui_tick(self):
        """Wołane z timera interfejsu co 33 ms; duże spóźnienie = wątek UI był zajęty."""
        t = now()
        previous, self._tick = self._tick, t
        if previous is None:
            return
        gap = t - previous
        if not 0.25 < gap < 10.0:
            return
        try:
            with self._lock:
                affected = {id(u): u for u in [self._engine, self._ui_last, *self._typing] if u is not None}
                for utt in affected.values():
                    utt.ui_block_max = max(utt.ui_block_max, gap)
                log.info("[LAT] WĄTEK UI ZABLOKOWANY ok. %.0f ms (timer 33 ms) — tekst z silnika i "
                         "wpisywanie czekają na interfejs", gap * 1000)
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    def reset_session(self):
        """Nowa sesja dyktowania (tekst i wpisywanie liczone od zera)."""
        try:
            with self._lock:
                for utt in list(self._typing) + [u for u in self._ui_queue if u not in self._typing]:
                    utt.notes.append("nie wpisano do końca")
                    self._finish(utt)
                self._typing.clear()
                self._ui_queue.clear()
                self._ui_pos = 0
                self._done = 0
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    def ui_text(self, stable_len: int, final: bool, tentative_len: int = 0, t: float | None = None):
        """Pasek pokazał tekst, a wpisywanie dostało pewny tekst o długości ``stable_len``."""
        t = now() if t is None else t
        try:
            with self._lock:
                if not self._ui_queue:
                    self._ui_pos = stable_len
                    return
                utt = self._ui_queue[0]
                if utt.start_char is None:
                    utt.start_char = self._ui_pos
                    self._typing.append(utt)
                before = utt.end_char if utt.end_char is not None else utt.start_char
                utt.end_char = max(stable_len, utt.start_char)
                if utt.end_char > before:
                    utt.handoffs.append((utt.end_char, t))
                self._ui_last = utt
                new_stable = utt.end_char - utt.start_char
                if utt.overlay_first is None and (new_stable > 0 or tentative_len > 0):
                    utt.overlay_first = t
                    log.info("[LAT #%d] OKIENKO START %s (%s od dźwięku)%s", utt.number, clock(t),
                             _fmt_ms(_ms(utt.audio_start, t)), "" if new_stable else " — tekst niepewny (szary)")
                if utt.overlay_stable is None and new_stable > 0:
                    utt.overlay_stable = t
                    if utt.overlay_first != t:
                        log.info("[LAT #%d] OKIENKO PEWNY %s (%s od dźwięku)", utt.number, clock(t),
                                 _fmt_ms(_ms(utt.audio_start, t)))
                if final:
                    utt.overlay_final = t
                    log.info("[LAT #%d] OKIENKO FINAL %s (%s od końca dźwięku)", utt.number, clock(t),
                             _fmt_ms(_ms(utt.audio_end, t)))
                    utt.ui_final = True
                    self._ui_queue.popleft()
                    self._ui_pos = stable_len
                    self._check(utt)
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    # -- wątek wpisywania ------------------------------------------------------

    def typed(self, start: int, end: int, t: float | None = None, inject: float | None = None):
        """Znaki [start, end) właśnie zostały wysłane do okna
        (``inject`` — ile trwało samo SendInput, w sekundach)."""
        t = now() if t is None else t
        try:
            with self._lock:
                self._done = end
                for utt in list(self._typing):
                    if utt.start_char is None or utt.end_char is None:
                        continue
                    overlap = min(end, utt.end_char) - max(start, utt.start_char)
                    if overlap > 0:
                        if utt.type_first is None:
                            utt.type_first = t
                            log.info("[LAT #%d] WPISYWANIE START %s (%s od dźwięku)", utt.number,
                                     clock(t), _fmt_ms(_ms(utt.audio_start, t)))
                        utt.type_last = t
                        utt.typed_chars += overlap
                        if inject is not None:
                            utt.inject_max = max(utt.inject_max, inject)
                        upto = min(end, utt.end_char)
                        for char_end, handed in utt.handoffs:
                            if char_end >= upto:
                                lag = t - handed
                                utt.lag_max = max(utt.lag_max, lag)
                                utt.lag_sum += lag
                                utt.lag_n += 1
                                break
                    self._check(utt)
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    def blocked(self, reason: str, seconds: float):
        """Wpisywanie czekało ``seconds`` s mimo gotowego tekstu (``reason`` — dlaczego)."""
        try:
            with self._lock:
                for utt in self._typing:
                    utt.blocked += seconds
                    if utt.type_first is None:
                        utt.blocked_first += seconds
                    note = f"wpisywanie wstrzymane: {reason}"
                    if note not in utt.notes:
                        utt.notes.append(note)
                log.info("[LAT] WPISYWANIE WSTRZYMANE %.0f ms: %s", seconds * 1000, reason)
        except Exception:
            log.debug("Błąd pomiaru latencji", exc_info=True)

    # -- podsumowanie -----------------------------------------------------------

    @staticmethod
    def _budget(utt: _Utterance):
        """Rozbija czas na składniki. Zwraca (start, koniec); każde to
        (całość_ms | None, [(kolumna_csv, nazwa, ms), ...])."""
        def sec(value):
            return None if value is None else int(round(value * 1000))

        start_parts: list[tuple[str, str, int]] = []
        zero = utt.voice_start if utt.voice_start is not None else utt.audio_start
        total_start = _ms(zero, utt.type_first)
        vad = _ms(zero, utt.vad_start)
        engine = _ms(utt.vad_start, utt.first_text)
        to_ui = _ms(utt.first_text, utt.overlay_stable)
        to_type = _ms(utt.overlay_stable, utt.type_first)
        if None not in (total_start, vad, engine, to_ui, to_type):
            asr_first = sec(utt.asr_final) if utt.first_from_final else sec(utt.asr_before_first)
            asr_first = min(asr_first or 0, max(0, engine))
            blocked = min(sec(utt.blocked_first) or 0, max(0, to_type))
            start_parts = [
                ("b_start_vad_ms", "VAD (wykrycie mowy)", max(0, vad)),
                ("b_start_asr_ms", "ASR do 1. pewnego tekstu", asr_first),
                ("b_start_silnik_czeka_ms", "silnik czeka (krok / zgoda przebiegów / zajęty)",
                 max(0, engine - asr_first)),
                ("b_start_qt_ms", "silnik→UI (Qt)", max(0, to_ui)),
                ("b_start_wstrzymane_ms", "wpisywanie wstrzymane (okno/klawisze)", blocked),
                ("b_start_kolejka_wpisywania_ms", "kolejka wpisywania + SendInput", max(0, to_type - blocked)),
            ]
        end_parts: list[tuple[str, str, int]] = []
        total_end = _ms(utt.audio_end, utt.type_last)
        silence = _ms(utt.audio_end, utt.vad_end)
        to_final = _ms(utt.vad_end, utt.final)
        qt_final = _ms(utt.final, utt.overlay_final)
        tail = _ms(utt.overlay_final, utt.type_last)
        if None not in (total_end, silence, to_final, qt_final, tail):
            asr_final = min(sec(utt.asr_final) or 0, max(0, to_final))
            end_parts = [
                ("b_koniec_cisza_ms", "pauza kończąca wypowiedź", max(0, silence)),
                ("b_koniec_silnik_zajety_ms", "silnik zajęty przed final", max(0, to_final - asr_final)),
                ("b_koniec_asr_final_ms", "ASR final", asr_final),
                ("b_koniec_qt_ms", "silnik→UI (Qt)", max(0, qt_final)),
                ("b_koniec_wpisywanie_ms", "wpisywanie reszty tekstu", max(0, tail)),
            ]
        return (total_start, start_parts), (total_end, end_parts)

    @staticmethod
    def _describe(title: str, total: int | None, parts: list) -> tuple[str, str | None]:
        if not parts:
            return "", None
        top = max(parts, key=lambda part: part[2])
        share = int(round(100 * top[2] / total)) if total else 0
        text = " | ".join(f"{name} {value}" for _, name, value in parts)
        return (f"{title} (razem {total} ms): {text}  ⇒ NAJWIĘCEJ: {top[1]} — {top[2]} ms ({share}%)", top[1])

    def _check(self, utt: _Utterance):
        if utt.ui_final and utt.end_char is not None and self._done >= utt.end_char:
            if utt.end_char <= (utt.start_char or 0):
                utt.notes.append("brak tekstu")
            self._finish(utt)

    def _finish(self, utt: _Utterance):
        if utt in self._typing:
            self._typing.remove(utt)
        if utt.type_last is not None:
            log.info("[LAT #%d] WPISYWANIE KONIEC %s (%s od końca dźwięku)", utt.number,
                     clock(utt.type_last), _fmt_ms(_ms(utt.audio_end, utt.type_last)))
        asr_avg = (sum(utt.asr_times) / len(utt.asr_times) * 1000) if utt.asr_times else None
        row = {
            "nr": utt.number,
            "data": datetime.now().strftime("%Y-%m-%d"),
            "dzwiek_start": clock(utt.audio_start),
            "vad_start": clock(utt.vad_start),
            "pierwszy_tekst": clock(utt.first_text),
            "dzwiek_koniec": clock(utt.audio_end),
            "vad_koniec": clock(utt.vad_end),
            "final": clock(utt.final),
            "okienko_start": clock(utt.overlay_first),
            "okienko_pewny": clock(utt.overlay_stable),
            "okienko_final": clock(utt.overlay_final),
            "wpisywanie_start": clock(utt.type_first),
            "wpisywanie_koniec": clock(utt.type_last),
            "vad_wykrycie_ms": _ms(utt.audio_start, utt.vad_start),
            "pierwszy_tekst_ms": _ms(utt.audio_start, utt.first_text),
            "okienko_start_ms": _ms(utt.audio_start, utt.overlay_first),
            "okienko_pewny_ms": _ms(utt.audio_start, utt.overlay_stable),
            "wpisywanie_start_ms": _ms(utt.audio_start, utt.type_first),
            "okienko_do_aplikacji_ms": _ms(utt.overlay_first, utt.type_first),
            "okienko_final_po_koncu_ms": _ms(utt.audio_end, utt.overlay_final),
            "cisza_do_konca_ms": _ms(utt.audio_end, utt.vad_end),
            "final_po_koncu_ms": _ms(utt.audio_end, utt.final),
            "wpisywanie_koniec_po_koncu_ms": _ms(utt.audio_end, utt.type_last),
            "czas_wpisywania_ms": _ms(utt.type_first, utt.type_last),
            "dlugosc_mowy_ms": _ms(utt.audio_start, utt.audio_end),
            "znaki": utt.typed_chars,
            "przebiegi_asr": len(utt.asr_times),
            "asr_sredni_ms": None if asr_avg is None else int(round(asr_avg)),
            "asr_final_ms": None if utt.asr_final is None else int(round(utt.asr_final * 1000)),
            "uwagi": ", ".join(utt.notes),
            "model": utt.model,
            "asr_max_ms": int(round(utt.asr_max * 1000)) if utt.asr_times else None,
            "bufor_max_s": round(utt.buffer_max, 1) if utt.buffer_max else None,
            "odstep_max_ms": int(round(utt.interval_max * 1000)) if utt.interval_max else None,
            "przebiegi_wolniejsze_niz_krok": utt.slow_passes,
            "kolejka_audio_max_ms": int(round(utt.queue_age_max * 1000)),
            "vad_max_ms": int(round(utt.vad_max * 1000)),
            "ui_obsluga_max_ms": int(round(utt.ui_cost_max * 1000)),
            "ui_odswiezanie_max_ms": int(round(utt.ui_render_max * 1000)),
            "ui_zablokowany_max_ms": int(round(utt.ui_block_max * 1000)),
            "sendinput_max_ms": round(utt.inject_max * 1000, 1) if utt.inject_max else None,
            "wstrzymane_ms": int(round(utt.blocked * 1000)),
            "wpisywanie_lag_max_ms": int(round(utt.lag_max * 1000)) if utt.lag_n else None,
            "wpisywanie_lag_sr_ms": int(round(utt.lag_sum / utt.lag_n * 1000)) if utt.lag_n else None,
            "bufor_final_s": None if utt.final_buffer is None else round(utt.final_buffer, 1),
        }
        zero = utt.voice_start if utt.voice_start is not None else utt.audio_start
        row.update({
            "glos_start": clock(utt.voice_start),
            "glos_przed_vad_ms": _ms(utt.voice_start, utt.audio_start) if utt.voice_start is not None else None,
            "glos_do_wykrycia_ms": _ms(zero, utt.vad_start),
            "glos_do_paska_ms": _ms(zero, utt.overlay_first),
            "glos_do_pierwszego_tekstu_ms": _ms(zero, utt.first_text),
            "glos_do_1_znaku_ms": _ms(zero, utt.type_first),
            "glos_do_ostatniego_znaku_ms": _ms(zero, utt.type_last),
        })
        log.info("[LAT #%d] OD GŁOSU (%s): wykrycie mowy %s ms | pasek %s ms | 1. pewny tekst %s ms | "
                 "1. znak w aplikacji %s ms | ostatni znak %s ms", utt.number,
                 "poziom mikrofonu" if utt.voice_start is not None else "VAD — brak progu poziomu",
                 row["glos_do_wykrycia_ms"], row["glos_do_paska_ms"], row["glos_do_pierwszego_tekstu_ms"],
                 row["glos_do_1_znaku_ms"], row["glos_do_ostatniego_znaku_ms"])
        (total_start, start_parts), (total_end, end_parts) = self._budget(utt)
        for key, _name, value in start_parts + end_parts:
            row[key] = value
        row["b_start_najwiecej"] = max(start_parts, key=lambda part: part[2])[1] if start_parts else None
        row["b_koniec_najwiecej"] = max(end_parts, key=lambda part: part[2])[1] if end_parts else None
        for title, total, parts in (("BILANS START początek głosu→1. znak w aplikacji", total_start, start_parts),
                                    ("BILANS KONIEC koniec dźwięku→ostatni znak", total_end, end_parts)):
            text, _top = self._describe(title, total, parts)
            if text:
                log.info("[LAT #%d] %s", utt.number, text)
        log.info(
            "[LAT #%d] DIAGNOSTYKA [%s]: ASR max %s ms (wolniejsze niż krok: %d z %d) | bufor max %s s | "
            "odstęp przebiegów max %s ms | audio w kolejce max %d ms | VAD max %d ms | UI obsługa max %d ms "
            "(odświeżanie transkrypcji %d ms, zablokowany %d ms) | SendInput max %s ms | wstrzymane %d ms | "
            "pasek→znak w oknie: śr. %s ms, max %s ms",
            utt.number, utt.model or "?", row["asr_max_ms"], utt.slow_passes, len(utt.asr_times),
            row["bufor_max_s"], row["odstep_max_ms"], row["kolejka_audio_max_ms"], row["vad_max_ms"],
            row["ui_obsluga_max_ms"], row["ui_odswiezanie_max_ms"], row["ui_zablokowany_max_ms"],
            row["sendinput_max_ms"], row["wstrzymane_ms"], row["wpisywanie_lag_sr_ms"],
            row["wpisywanie_lag_max_ms"],
        )
        log.info(
            "[LAT #%d] PODSUMOWANIE: mowa %s–%s (%s ms) | okienko %s | wpisywanie %s–%s | "
            "dźwięk→okienko %s ms | dźwięk→1. znak w aplikacji %s ms | okienko→aplikacja %s ms | "
            "koniec dźwięku→ostatni znak %s ms | 1. pewny tekst %s ms | "
            "final po końcu %s ms | %d znaków | ASR %d× śr. %s ms, final %s ms%s",
            utt.number, row["dzwiek_start"], row["dzwiek_koniec"], row["dlugosc_mowy_ms"],
            row["okienko_start"], row["wpisywanie_start"], row["wpisywanie_koniec"],
            row["okienko_start_ms"], row["wpisywanie_start_ms"], row["okienko_do_aplikacji_ms"],
            row["wpisywanie_koniec_po_koncu_ms"], row["pierwszy_tekst_ms"],
            row["final_po_koncu_ms"], utt.typed_chars, len(utt.asr_times), row["asr_sredni_ms"],
            row["asr_final_ms"], f" | {row['uwagi']}" if row["uwagi"] else "",
        )
        self._write_csv(row)

    def _write_csv(self, row: dict):
        _append_csv(self._csv_path, CSV_COLUMNS, row)


_CHECKED_CSV: set[Path] = set()   # pliki, których nagłówek już sprawdzono w tej sesji


def _append_csv(path: Path | None, columns: list[str], row: dict):
    if path is None:
        return
    try:
        new = not path.exists()
        if new:
            _CHECKED_CSV.discard(path)
        if not new and path not in _CHECKED_CSV:
            with open(path, encoding="utf-8-sig") as handle:
                header = handle.readline().strip()
            if header != ";".join(columns):
                # Stary układ kolumn — odkładamy plik, żeby wiersze się nie rozjechały.
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path.rename(path.with_name(f"{path.stem}_stary_{stamp}{path.suffix}"))
                new = True
        _CHECKED_CSV.add(path)
        with open(path, "a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, delimiter=";")
            if new:
                writer.writeheader()
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    except OSError as exc:
        log.warning("Nie zapisano %s: %s", path.name, exc)


tracker = LatencyTracker()
