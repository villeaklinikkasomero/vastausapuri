"""Hankkeet: jokaisella oma katalogi, ohjeet, vastausindeksi ja kriisisignaalit.

Rakenne:
    hankkeet/
      yhteiset/katalogi.json          kaikille yhteiset (esim. kriisipalvelut)
      <hanke>/hanke.toml              asetukset ja ohjeet mallille
      <hanke>/katalogi.json           hankkeen sisällöt ja ohjauskohteet
      <hanke>/vastaukset.jsonl        (valinnainen) pseudonymisoidut vanhat vastaukset

Vastaukset eivät kuulu Git-repoon. Tuotannossa ne luetaan APURI_DATA_DIR-kansiosta
(esim. Renderin levy): <APURI_DATA_DIR>/<hanke>/vastaukset.jsonl.

Uuden hankkeen lisääminen ei vaadi koodimuutoksia.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .answers import AnswerIndex, load_answers
from .catalog import Index, load_catalog, merge_catalogs
from .pseudonymize import _domain
from .safety import compile_patterns


@dataclass
class Hanke:
    id: str
    nimi: str
    kuvaus: str
    sivusto: str
    ohjeet: str
    vastaus: dict
    index: Index
    answers: AnswerIndex
    signal_patterns: dict[str, list[re.Pattern[str]]]
    config: dict = field(default_factory=dict)

    @property
    def safe_domains(self) -> frozenset[str]:
        """Verkkotunnukset, joiden linkit eivät ole henkilötietoa (katalogin ja palvelun omat)."""
        urls = [it.url for it in self.index.items] + [self.sivusto]
        return frozenset(_domain(u) for u in urls if u)

    @property
    def research(self) -> dict:
        return self.config.get("tutkimus", {})

    @property
    def tervehdys(self) -> str:
        return self.vastaus.get("tervehdys", "Hei,")

    @property
    def allekirjoitus(self) -> str:
        return self.vastaus.get("allekirjoitus", "")

    def summary(self) -> dict:
        return {
            "id": self.id,
            "nimi": self.nimi,
            "sisaltoja": len(self.index.items),
            "vastauksia": len(self.answers.answers),
            "vastaajat": sorted(self.answers.respondents()),
            "tutkimus": bool(self.research.get("kaytossa")),
        }


def load_hanke(root: Path, hanke_id: str, data_dir: Path | None = None) -> Hanke:
    d = root / hanke_id
    cfg = tomllib.loads((d / "hanke.toml").read_text(encoding="utf-8"))
    h = cfg.get("hanke", {})
    catalogs = [load_catalog(d / "katalogi.json")]
    for inc in cfg.get("haku", {}).get("sisallyta", []):
        catalogs.append(load_catalog(root / inc / "katalogi.json"))
    return Hanke(
        id=h.get("id", hanke_id),
        nimi=h.get("nimi", hanke_id),
        kuvaus=h.get("kuvaus", ""),
        sivusto=h.get("sivusto", ""),
        ohjeet=cfg.get("ohjeet", {}).get("teksti", "").strip(),
        vastaus=cfg.get("vastaus", {}),
        index=Index(merge_catalogs(*catalogs)),
        answers=AnswerIndex(load_answers((data_dir / hanke_id if data_dir else d) / "vastaukset.jsonl")),
        signal_patterns=compile_patterns(cfg.get("kriisi")),
        config=cfg,
    )


def load_all(root: Path, data_dir: Path | None = None) -> dict[str, Hanke]:
    out = {}
    for d in sorted(p for p in root.iterdir() if (p / "hanke.toml").exists()):
        h = load_hanke(root, d.name, data_dir)
        out[h.id] = h
    if not out:
        raise RuntimeError(f"Hankkeita ei löytynyt kansiosta {root}")
    return out
