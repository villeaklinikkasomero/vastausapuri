"""Paikallinen kriisisignaalien tunnistus.

Tämä toimii mallista riippumatta: vaikka malli ei huomaisi signaalia, vastaaja
saa merkinnän. Lista on tarkoituksella laaja; väärä hälytys on halvempi kuin ohitettu.
Sisältö tulee tarkistaa neuvontapalvelun asiantuntijoiden kanssa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    kind: str  # esim. "itsetuhoisuus", "väkivalta", "akuutti_myrkytys" tai hankkeen oma laji
    match: str


_PATTERNS: dict[str, list[str]] = {
    "itsetuhoisuus": [
        r"itsemurh\w*",
        r"tappa\w* itse\w*",
        r"\ben(?:kä|nä)? (?:enää |edes )?(?:halua|jaksa) elää",
        r"\bei (?:enää )?(?:halua|jaksa) elää",
        r"haluan kuolla",
        r"kuolla pois",
        r"lopettaa (?:kaiken|elämä\w*)",
        r"viiltel\w*",
        r"satuttaa itse\w*",
        r"päättää päivä\w*",
        r"ei ole mitään syytä elää",
    ],
    "väkivalta": [
        r"\blyö\w*",
        r"\blöi\b",
        r"potki\w*",
        r"kuristi\w*",
        r"uhkaa\w* (?:tappaa|satuttaa)",
        r"pelkään (?:henkeni|hänen|miehe\w*|vaimo\w*|puoliso\w*)",
        r"lähisuhdeväkival\w*",
        r"väkival\w*",
    ],
    "lapsen_turvallisuus": [
        r"lapsi (?:on )?vaara\w*",
        r"lapset (?:ovat )?vaara\w*",
        r"humala\w* lapse\w*",
        r"lapsi\w* (?:yksin|heitteill\w*)",
    ],
    "akuutti_myrkytys": [
        r"yliannostu\w*",
        r"otti\w* (?:liikaa|kaikki) (?:pillere\w*|lääkke\w*|tablet\w*)",
        r"ei herää",
        r"menetti tajun\w*",
        r"tajuton\w*",
    ],
}

_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in _PATTERNS.items()}


def compile_patterns(extra: dict[str, list[str]] | None) -> dict[str, list[re.Pattern[str]]]:
    """Hankekohtaiset lisäsignaalit yhdistettynä yhteisiin."""
    merged = {k: list(v) for k, v in _COMPILED.items()}
    for kind, pats in (extra or {}).items():
        merged.setdefault(kind, []).extend(re.compile(p, re.I) for p in pats)
    return merged


def detect_signals(text: str, patterns: dict[str, list[re.Pattern[str]]] | None = None) -> list[Signal]:
    found: list[Signal] = []
    for kind, pats in (patterns or _COMPILED).items():
        for p in pats:
            m = p.search(text)
            if m:
                found.append(Signal(kind=kind, match=m.group(0)))
                break  # yksi osuma per tyyppi riittää
    return found
