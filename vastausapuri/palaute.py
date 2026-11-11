"""Vastaajien palaute luonnoksista.

Tallennetaan vain arvio, valitut syyt, lyhyt kommentti (pseudonymisoituna) ja tekniset tiedot
(hanke, tila, mallit, huomioiden määrä). Kysymystä, luonnosta eikä vastaajan nimeä tallenneta.
Tiedosto: <APURI_DATA_DIR tai hankkeet>/palaute.jsonl
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path

from .pseudonymize import pseudonymize

REASONS = {
    "asiavirhe": "Asiavirhe",
    "puuttuva_ohjaus": "Puuttuva tai väärä ohjaus",
    "turvallisuus": "Turvallisuus (esim. hoitoon ohjaus puuttui)",
    "savy": "Sävy ei sopinut",
    "pituus": "Liian pitkä tai sekava",
    "ei_vastannut": "Ei vastannut kysymykseen",
    "linkit": "Linkit eivät sopineet",
    "kieli": "Kieli kuulosti koneelliselta",
}
_lock = threading.Lock()


def path(data_dir: Path | None, hankkeet_dir: Path) -> Path:
    return (data_dir or hankkeet_dir) / "palaute.jsonl"


def save(p: Path, *, hanke: str, mode: str, rating: str, reasons: list[str], comment: str,
         meta: dict, safe_domains=frozenset()) -> dict:
    if rating not in ("hyva", "huono"):
        raise ValueError("Tuntematon arvio")
    rec = {
        "ts": int(time.time()),
        "hanke": hanke,
        "tila": mode,
        "arvio": rating,
        "syyt": [r for r in reasons if r in REASONS],
        "kommentti": pseudonymize(comment.strip()[:1000], safe_domains).text if comment.strip() else "",
        # Tekniset tiedot laadun seurantaan (ei sisältöä)
        "jatko": bool(meta.get("followup")),
        "mallit": meta.get("models", {}),
        "ongelmia": int(meta.get("issues", 0)),
        "tarkista": int(meta.get("checks", 0)),
        "linkkeja": int(meta.get("links", 0)),
        "tutkimuksia": int(meta.get("research", 0)),
        "signaalit": [str(s) for s in meta.get("signals", [])][:5],
        "kesto_s": round(float(meta.get("seconds", 0)), 1),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock, p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def summary(records: list[dict], days: int | None = None) -> dict:
    if days:
        cutoff = time.time() - days * 86400
        records = [r for r in records if r["ts"] >= cutoff]
    out: dict = {}
    for hanke in sorted({r["hanke"] for r in records}):
        rs = [r for r in records if r["hanke"] == hanke]
        good = sum(r["arvio"] == "hyva" for r in rs)
        reasons = Counter(s for r in rs for s in r["syyt"])
        out[hanke] = {
            "yhteensa": len(rs),
            "hyodyllisia": good,
            "osuus": round(100 * good / len(rs)) if rs else 0,
            "syyt": [{"syy": REASONS.get(k, k), "maara": v} for k, v in reasons.most_common()],
            "kommentit": [{"ts": r["ts"], "arvio": r["arvio"], "tila": r["tila"], "teksti": r["kommentti"],
                           "syyt": [REASONS.get(s, s) for s in r["syyt"]]}
                          for r in sorted(rs, key=lambda r: -r["ts"]) if r["kommentti"]][:30],
            "keskikesto_s": round(sum(r["kesto_s"] for r in rs) / len(rs), 1) if rs else 0,
        }
    return out
