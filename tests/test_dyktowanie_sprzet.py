"""Rozpoznanie sprzętu i dobór modelu — bez dostępu do prawdziwych kart."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_sprzet as sprzet  # noqa: E402


@pytest.mark.parametrize("name,vendor", [
    ("NVIDIA GeForce GTX 1650 Ti", "nvidia"),
    ("GeForce RTX 4070", "nvidia"),
    ("AMD Radeon(TM) Graphics", "amd"),
    ("Radeon RX 7800 XT", "amd"),
    ("Intel(R) UHD Graphics 630", "intel"),
    ("Intel(R) Iris(R) Xe Graphics", "intel"),
    ("Intel(R) Arc(TM) A770", "intel"),
    ("Apple Silicon", "apple"),
    ("Matrox G200", "other"),
])
def test_vendor_of(name, vendor):
    assert sprzet.vendor_of(name) == vendor


def test_nvidia_with_cuda_gets_fast_model():
    hardware = sprzet.detect(1, ["AMD Radeon(TM) Graphics", "NVIDIA GeForce RTX 3060"], vram_mb=12288)
    assert hardware.vendor == "nvidia" and hardware.accelerated
    assert hardware.card == "NVIDIA GeForce RTX 3060"
    assert hardware.recommended_model == sprzet.MODEL_FAST_GPU
    assert "karta graficzna" in hardware.summary


def test_small_nvidia_card_gets_light_model():
    hardware = sprzet.detect(1, ["NVIDIA GeForce MX150"], vram_mb=2048)
    assert hardware.accelerated and hardware.recommended_model == sprzet.MODEL_CPU


@pytest.mark.parametrize("cards,vendor", [
    (["AMD Radeon RX 6600"], "amd"),
    (["Intel(R) UHD Graphics 630"], "intel"),
    ([], "none"),
])
def test_other_cards_are_recognised_but_use_processor(cards, vendor):
    hardware = sprzet.detect(0, cards, second_engine=False)
    assert hardware.vendor == vendor
    assert not hardware.accelerated
    assert hardware.recommended_model == sprzet.MODEL_CPU
    assert "procesor" in hardware.summary


def test_nvidia_card_without_working_driver_is_explained():
    hardware = sprzet.detect(0, ["NVIDIA GeForce GTX 1050"], second_engine=False)
    assert hardware.vendor == "nvidia" and not hardware.accelerated
    assert "sterownik" in hardware.summary


def test_cuda_device_without_recognised_name_still_counts():
    hardware = sprzet.detect(1, ["Microsoft Hyper-V Video"], vram_mb=8192)
    assert hardware.accelerated and hardware.vendor == "nvidia"
