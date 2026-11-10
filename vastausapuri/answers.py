"""Vanhat vastaukset hakuindeksinä: esimerkit sisällöstä, sävystä ja ohjauksista.

Tiedosto hankkeet/<id>/vastaukset.jsonl syntyy tuontityökalulla
(vastausapuri/tyokalut/tuo_vastaukset.py), joka pseudonymisoi tekstit.
Raakoja kysymyksiä indeksiin ei tallenneta.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import TextIndex


@dataclass(frozen=True)
class Answer:
    id: str
    text: str  # pseudonymisoitu vastaus
    vastaaja: str = ""  # vastaajan tunniste (vastaa kirjautumistunnusta, jos oma tyyli halutaan)
    aihe: str = ""  # valinnainen yleistetty aihe/tiivistelmä kysymyksestä
    pvm: str = ""
    links: tuple[str, ...] = field(default_factory=tuple)


def load_answers(path: str | Path) -> list[Answer]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out.append(
            Answer(
                id=str(r["id"]),
                text=r["text"],
                vastaaja=r.get("vastaaja", ""),
                aihe=r.get("aihe", ""),
                pvm=r.get("pvm", ""),
                links=tuple(r.get("links", [])),
            )
        )
    return out


class AnswerIndex(TextIndex[Answer]):
    def __init__(self, answers: list[Answer]):
        super().__init__(answers, [f"{a.aihe} {a.aihe} {a.text}" for a in answers])
        self.answers = self.objects

    def respondents(self) -> set[str]:
        return {a.vastaaja for a in self.answers if a.vastaaja}

    def similar(self, question: str, k: int = 3, vastaaja: str | None = None) -> list[Answer]:
        """Samankaltaiset vastaukset. Jos vastaaja annetaan, ensisijaisesti hänen omansa."""
        if not self.answers:
            return []
        picked: list[Answer] = []
        if vastaaja:
            picked = [a for a, s in self.search(question, k=k, where=lambda a: a.vastaaja == vastaaja) if s > 0]
        if len(picked) < k:
            rest = [a for a, s in self.search(question, k=k * 2) if s > 0 and a not in picked]
            picked += rest[: k - len(picked)]
        return picked
