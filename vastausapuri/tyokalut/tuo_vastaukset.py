"""Tuo neuvontapalvelun vanhat vastaukset hankkeen vastausindeksiksi.

Käyttö (esimerkki):
    python -m vastausapuri.tyokalut.tuo_vastaukset dopinglinkki vienti.csv \\
        --vastaus-sarake vastaus --vastaaja-sarake vastaaja --pvm-sarake pvm \\
        --aihe-sarake kategoria --raportti raportti.md

Periaatteet:
- AJA VAIN SÄÄTIÖN OMASSA YMPÄRISTÖSSÄ. Tiedostot sisältävät henkilötietoja.
- Kysymyksiä ei tuoda lainkaan (niissä on usein kysyjien henkilötietoja).
- Vastaukset pseudonymisoidaan. Palvelun omat ja katalogin linkit säilytetään,
  koska niistä näkee, mihin vastaajat ovat ohjanneet.
- Tulos (vastaukset.jsonl) ei kuulu Git-repoon: se on .gitignore-listalla ja
  tuotannossa se sijoitetaan APURI_DATA_DIR-kansioon (esim. Renderin levy).

Raportti kertoo vastaajakohtaiset määrät ja yleisimmät ohjaukset (linkit), sekä
linkit, joita ei vielä ole katalogissa: ne ovat ehdokkaita katalogiin lisättäviksi.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from ..config import ROOT
from ..hanke import load_hanke
from ..pseudonymize import pseudonymize

URL = re.compile(r"https?://[^\s)<>\"\]]+", re.I)


def read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("rows", [])
    text = path.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(text.splitlines(), dialect=dialect))


def parse_map(spec: str | None) -> dict[str, str]:
    out = {}
    for pair in filter(None, (spec or "").split(",")):
        k, _, v = pair.partition("=")
        if k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hanke")
    ap.add_argument("tiedosto", type=Path, help="CSV-, JSON- tai JSONL-vienti")
    ap.add_argument("--vastaus-sarake", default="vastaus")
    ap.add_argument("--vastaaja-sarake", default="vastaaja", help="vastaajan tunniste (valinnainen)")
    ap.add_argument("--pvm-sarake", default="pvm")
    ap.add_argument("--id-sarake", default="id")
    ap.add_argument("--aihe-sarake", default="aihe", help="esim. kategoria tai aihe (valinnainen)")
    ap.add_argument("--vastaajakartta", help='kannan vastaaja -> kirjautumistunnus, esim. "Matti V.=matti,Liisa K.=liisa"')
    ap.add_argument("--min-pituus", type=int, default=80, help="lyhyemmät vastaukset ohitetaan")
    ap.add_argument("--ulos", type=Path, help="oletus: <APURI_DATA_DIR tai hankkeet>/<hanke>/vastaukset.jsonl")
    ap.add_argument("--raportti", type=Path, help="kirjoita yhteenveto Markdown-tiedostoon")
    args = ap.parse_args(argv)

    hanke = load_hanke(ROOT / "hankkeet", args.hanke)
    safe = hanke.safe_domains
    catalog_urls = {u.url.lower().rstrip("/") for u in hanke.index.items}
    vmap = parse_map(args.vastaajakartta)
    data_dir = Path(os.environ["APURI_DATA_DIR"]) if os.getenv("APURI_DATA_DIR") else ROOT / "hankkeet"
    out_path = args.ulos or data_dir / args.hanke / "vastaukset.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = read_rows(args.tiedosto)
    if rows and args.vastaus_sarake not in rows[0]:
        print(f"Saraketta '{args.vastaus_sarake}' ei löydy. Sarakkeet: {', '.join(rows[0].keys())}", file=sys.stderr)
        return 2

    written, skipped = 0, 0
    per_resp: Counter[str] = Counter()
    links: Counter[str] = Counter()
    ph_counts: Counter[str] = Counter()
    with out_path.open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            text = (r.get(args.vastaus_sarake) or "").strip()
            if len(text) < args.min_pituus:
                skipped += 1
                continue
            raw_resp = (r.get(args.vastaaja_sarake) or "").strip()
            resp = vmap.get(raw_resp, raw_resp.lower())
            ps = pseudonymize(text, safe)
            ph_counts.update(ps.counts)
            urls = [u.rstrip(".,;:!?") for u in URL.findall(ps.text)]
            links.update(u.lower().rstrip("/") for u in urls)
            rid = str(r.get(args.id_sarake) or hashlib.sha1(text.encode()).hexdigest()[:12])
            rec = {
                "id": rid,
                "text": ps.text,
                "vastaaja": resp,
                "aihe": (r.get(args.aihe_sarake) or "").strip(),
                "pvm": (r.get(args.pvm_sarake) or "").strip(),
                "links": urls,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
            per_resp[resp or "(tuntematon)"] += 1

    lines = [
        f"# Vastausten tuonti: {hanke.nimi}",
        "",
        f"- Tuotu: {written} vastausta, ohitettu {skipped} (lyhyet tai tyhjät)",
        f"- Tiedosto: {out_path}",
        f"- Pseudonymisoidut kohdat: {dict(ph_counts) or 'ei yhtään'}",
        "",
        "## Vastaajat",
        "",
        "| Vastaaja | Vastauksia |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in per_resp.most_common()],
        "",
        "## Yleisimmät ohjaukset (linkit vastauksissa)",
        "",
        "| Linkki | Kertaa | Katalogissa |",
        "| --- | --- | --- |",
        *[f"| {u} | {n} | {'kyllä' if u in catalog_urls else '**ei**'} |" for u, n in links.most_common(40)],
        "",
        "Linkit, joita ei ole katalogissa, kannattaa lisätä hankkeen katalogi.json-tiedostoon.",
    ]
    report = "\n".join(lines)
    if args.raportti:
        args.raportti.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
