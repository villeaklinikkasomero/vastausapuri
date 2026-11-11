"""Tuo neuvontapalvelun vanhat vastaukset hankkeen vastausindeksiksi.

Tuetut muodot:
- Neuvontapalvelun säievienti (JSON): [{"thread_id", "service_name", "questions": [{"question",
  "classifications", "created_on", "answers": [{"answer", "published_on"}]}]}]
- CSV / JSON-lista / JSONL, jossa on vastaussarake (ja valinnaisesti vastaaja, pvm, aihe).

Käyttö (esimerkit):
    python -m vastausapuri.tyokalut.tuo_vastaukset dopinglinkki neuvonta-threads.json --kysymykset
    python -m vastausapuri.tyokalut.tuo_vastaukset dopinglinkki vienti.csv --vastaus-sarake vastaus

Periaatteet:
- Kysymyksiä EI tuoda oletuksena. --kysymykset tuo ne mukaan; käytä sitä vain, kun aineiston
  kysymykset on siistitty tunnisteista. Kaikki tekstit pseudonymisoidaan silti varmuuden vuoksi.
- Palautepyyntö poistetaan vastausten lopusta (apuri lisää sen itse).
- Palvelun omat ja katalogin linkit säilytetään, koska niistä näkee, mihin on ohjattu.
- Tulos (vastaukset.jsonl) ei kuulu Git-repoon. Tuotannossa se sijoitetaan APURI_DATA_DIR-kansioon.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html as htmllib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from ..config import ROOT
from ..hanke import Hanke, load_hanke
from ..pseudonymize import pseudonymize

URL = re.compile(r"https?://[^\s)<>\"\]]+", re.I)
FEEDBACK = re.compile(
    r"(palautetta työstämme|palautteesi on täysin anonyymi|anna palautteesi|mitä pidit (saamastasi )?vastauksesta"
    r"|palautelomakkeen)", re.I)
SIGNATURE = re.compile(r"^\s*([A-ZÅÄÖ][a-zåäö]+)\s*[-–]\s*\S.*$")  # esim. "Tuuli - Varjomaailman aikuinen"


# --- Siivous ------------------------------------------------------------------------

def clean_html(h: str | None) -> tuple[str, list[str]]:
    """HTML tekstiksi kappaleineen. Palauttaa myös href-linkit (ne katoaisivat tekstistä)."""
    h = h or ""
    links = re.findall(r'href="([^"]+)"', h)
    # Linkkiteksti + osoite näkyviin, jotta ohjaus säilyy esimerkissä
    h = re.sub(r'<a [^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", h, flags=re.S)
    h = re.sub(r"</p>|<br\s*/?>|</li>", "\n", h)
    h = re.sub(r"<li[^>]*>", "- ", h)
    t = htmllib.unescape(re.sub(r"<[^>]+>", "", h)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r]+", " ", l).strip() for l in t.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text, links


def strip_feedback(text: str) -> str:
    """Poistaa palautepyynnön kappaleet vastauksen lopusta."""
    paras = text.split("\n")
    while paras and (not paras[-1].strip() or FEEDBACK.search(paras[-1])):
        paras.pop()
    return "\n".join(paras).strip()


def signature_name(text: str) -> str:
    """Vastaajan etunimi allekirjoituksesta, jos sellainen on (esim. "Tuuli - Varjomaailman aikuinen")."""
    for line in reversed([l for l in text.split("\n") if l.strip()][-3:]):
        m = SIGNATURE.match(line)
        if m:
            return m.group(1).lower()
    return ""


def topic(classes: list[str] | None) -> str:
    return ", ".join(re.sub(r"^\d+\s*", "", c).strip() for c in (classes or []) if c)


# --- Säievienti -----------------------------------------------------------------------

def thread_records(data: list[dict], palvelu: str | None, with_questions: bool) -> list[dict]:
    """Jokaisesta vastauksesta oma tietue: kysymys (edelliset vastaamattomat yhdistettynä), aiempi
    keskustelu kontekstiksi ja vastaus."""
    out = []
    for t in data:
        if palvelu and (t.get("service_name") or "").lower() != palvelu.lower():
            continue
        transcript: list[str] = []
        pending_q: list[str] = []
        pending_cls: list[str] = []
        for q in t.get("questions") or []:
            qtext, _ = clean_html(q.get("question"))
            pending_q.append(qtext)
            pending_cls += q.get("classifications") or []
            for a in q.get("answers") or []:
                atext, links = clean_html(a.get("answer"))
                atext = strip_feedback(atext)
                if not atext:
                    continue
                kysymys = "\n\n".join(x for x in pending_q if x)
                out.append({
                    "id": f"{t.get('thread_id')}-{q.get('id')}",
                    "palvelu": t.get("service_name", ""),
                    "text": atext,
                    "kysymys": kysymys if with_questions else "",
                    "konteksti": "\n\n".join(transcript) if with_questions else "",
                    "aihe": topic(pending_cls),
                    "pvm": (a.get("published_on") or "")[:10],
                    "vastaaja": signature_name(atext),
                    "links": [l for l in links if l.startswith("http")],
                    "jatko": bool(transcript),
                })
                transcript += [f"Kysyjä: {kysymys}", f"Vastaaja: {atext}"]
                pending_q, pending_cls = [], []
    return out


def table_records(rows: list[dict], args) -> list[dict]:
    out = []
    for r in rows:
        atext = strip_feedback((r.get(args.vastaus_sarake) or "").strip())
        out.append({
            "id": str(r.get(args.id_sarake) or hashlib.sha1(atext.encode()).hexdigest()[:12]),
            "palvelu": "",
            "text": atext,
            "kysymys": (r.get(args.kysymys_sarake) or "").strip() if args.kysymykset else "",
            "konteksti": "",
            "aihe": (r.get(args.aihe_sarake) or "").strip(),
            "pvm": (r.get(args.pvm_sarake) or "").strip(),
            "vastaaja": (r.get(args.vastaaja_sarake) or "").strip(),
            "links": URL.findall(atext),
            "jatko": False,
        })
    return out


def read_file(path: Path) -> list[dict] | dict:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8-sig"))
    text = path.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(text.splitlines(), dialect=dialect))


def is_thread_export(data) -> bool:
    return isinstance(data, list) and bool(data) and isinstance(data[0], dict) and "questions" in data[0]


# --- Tuonti ---------------------------------------------------------------------------

def parse_map(spec: str | None) -> dict[str, str]:
    out = {}
    for pair in filter(None, (spec or "").split(",")):
        k, _, v = pair.partition("=")
        if k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def build_index(hanke: Hanke, records: list[dict], out_path: Path, min_len: int = 80,
                vmap: dict[str, str] | None = None) -> str:
    """Pseudonymisoi tietueet, kirjoittaa vastaukset.jsonl:n ja palauttaa raportin (Markdown)."""
    safe = hanke.safe_domains
    catalog_urls = {u.url.lower().rstrip("/") for u in hanke.index.items}
    vmap = vmap or {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = skipped = followups = with_q = 0
    per_resp: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    links: Counter[str] = Counter()
    ph: Counter[str] = Counter()
    lengths: list[int] = []
    tmp = out_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            if len(r["text"]) < min_len:
                skipped += 1
                continue
            rec = dict(r)
            for key in ("text", "kysymys", "konteksti"):
                if rec.get(key):
                    ps = pseudonymize(rec[key], safe)
                    ph.update(ps.counts)
                    rec[key] = ps.text
            rec["vastaaja"] = vmap.get(r["vastaaja"], r["vastaaja"].lower())
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
            followups += bool(r.get("jatko"))
            with_q += bool(rec.get("kysymys"))
            lengths.append(len(rec["text"].split()))
            per_resp[rec["vastaaja"] or "(ei tunnistetta)"] += 1
            topics.update(t.strip() for t in rec["aihe"].split(",") if t.strip())
            links.update(u.lower().rstrip("/.") for u in rec["links"] if "webropol" not in u.lower())
    tmp.replace(out_path)

    avg = sum(lengths) // len(lengths) if lengths else 0
    lines = [
        f"# Vastausten tuonti: {hanke.nimi}",
        "",
        f"- Tuotu {written} vastausta ({followups} jatkokysymyksiin), ohitettu {skipped} lyhyttä tai tyhjää",
        f"- Kysymykset mukana: {with_q} vastauksessa",
        f"- Vastausten keskipituus: {avg} sanaa",
        f"- Pseudonymisoidut kohdat: {dict(ph) or 'ei yhtään'}",
        f"- Tiedosto: {out_path}",
        "",
        "## Vastaajat (allekirjoituksen tai vastaajasarakkeen mukaan)",
        "",
        "| Vastaaja | Vastauksia |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in per_resp.most_common()],
        "",
        "## Aiheet (luokittelu)",
        "",
        "| Aihe | Vastauksia |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in topics.most_common(25)],
        "",
        "## Ohjaukset (linkit vastauksissa, palautelinkit pois lukien)",
        "",
        "| Linkki | Kertaa | Katalogissa |",
        "| --- | --- | --- |",
        *[f"| {u} | {n} | {'kyllä' if u in catalog_urls else '**ei**'} |" for u, n in links.most_common(40)],
        "",
        "Linkit, joita ei ole katalogissa, kannattaa lisätä hankkeen katalogi.json-tiedostoon.",
    ]
    return "\n".join(lines)


def import_data(hanke_id: str, data, out_path: Path, palvelu: str | None = None,
                with_questions: bool = False, min_len: int = 80, vmap: dict[str, str] | None = None) -> str:
    """Ylläpitosivun ja komentorivin yhteinen tuonti säieviennille."""
    hanke = load_hanke(ROOT / "hankkeet", hanke_id)
    palvelu = palvelu or hanke.config.get("vastaukset", {}).get("palvelu")
    if not is_thread_export(data):
        raise ValueError("Tiedosto ei ole neuvontapalvelun säievienti (odotettiin listaa, jossa 'questions').")
    records = thread_records(data, palvelu, with_questions)
    if not records:
        services = sorted({t.get("service_name", "") for t in data})
        raise ValueError(f"Palvelulle '{palvelu}' ei löytynyt vastauksia. Viennin palvelut: {', '.join(services)}")
    return build_index(hanke, records, out_path, min_len, vmap)


def default_out(hanke_id: str) -> Path:
    base = Path(os.environ["APURI_DATA_DIR"]) if os.getenv("APURI_DATA_DIR") else ROOT / "hankkeet"
    return base / hanke_id / "vastaukset.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hanke")
    ap.add_argument("tiedosto", type=Path, help="säievienti (JSON) tai CSV/JSON/JSONL-taulukko")
    ap.add_argument("--palvelu", help="säieviennin service_name (oletus hanke.toml:n [vastaukset] palvelu)")
    ap.add_argument("--kysymykset", action="store_true",
                    help="tuo myös kysymykset (vain siistitystä aineistosta)")
    ap.add_argument("--vastaus-sarake", default="vastaus")
    ap.add_argument("--kysymys-sarake", default="kysymys")
    ap.add_argument("--vastaaja-sarake", default="vastaaja")
    ap.add_argument("--pvm-sarake", default="pvm")
    ap.add_argument("--id-sarake", default="id")
    ap.add_argument("--aihe-sarake", default="aihe")
    ap.add_argument("--vastaajakartta", help='tunniste -> kirjautumistunnus, esim. "tuuli=tuuli.k,Ville S.=ville"')
    ap.add_argument("--min-pituus", type=int, default=80, help="lyhyemmät vastaukset ohitetaan (merkkiä)")
    ap.add_argument("--ulos", type=Path, help="oletus: <APURI_DATA_DIR tai hankkeet>/<hanke>/vastaukset.jsonl")
    ap.add_argument("--raportti", type=Path, help="kirjoita yhteenveto Markdown-tiedostoon")
    args = ap.parse_args(argv)

    data = read_file(args.tiedosto)
    out_path = args.ulos or default_out(args.hanke)
    vmap = parse_map(args.vastaajakartta)
    try:
        if is_thread_export(data):
            report = import_data(args.hanke, data, out_path, args.palvelu, args.kysymykset,
                                 args.min_pituus, vmap)
        else:
            rows = data if isinstance(data, list) else data.get("rows", [])
            if rows and args.vastaus_sarake not in rows[0]:
                print(f"Saraketta '{args.vastaus_sarake}' ei löydy. Sarakkeet: {', '.join(rows[0].keys())}",
                      file=sys.stderr)
                return 2
            hanke = load_hanke(ROOT / "hankkeet", args.hanke)
            report = build_index(hanke, table_records(rows, args), out_path, args.min_pituus, vmap)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.raportti:
        args.raportti.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
