"""Tuo neuvontapalvelun vanhat vastaukset hankkeen vastausindeksiksi.

Tuetut muodot:
- Neuvontapalvelun säievienti (JSON): [{"thread_id", "service_name", "questions": [{"question",
  "classifications", "created_on", "answers": [{"answer", "published_on"}]}]}]
- Taulukko-JSON: {"rows": [{"question", "answer", "type"}]} (myös kysymys/vastaus). type = palvelutyyppi
  (Dopinglinkki = 3, ks. hanke.toml [vastaukset] tyyppi).
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
    r"|mitä mieltä olit (saamastasi )?vastauksesta|palautelomakkeen|^\s*-{3,}\s*$)", re.I)
# Allekirjoituksen nimike, jonka yläpuolella on vastaajan nimi (Päihdelinkki: "Janne N. / Neuvontapalvelun vastaaja")
ROLE = re.compile(r"(vastaaja|työntekijä|ylläpito|proviisori|sosionomi|sosiaalityöntekijä|terveydenhoitaja"
                  r"|kokemusasiantuntija|ohjaaja|päihdelinkki|seksuaalineuvoja|sairaanhoitaja)", re.I)
FULLNAME = re.compile(r"^([A-ZÅÄÖ][a-zåäö]+(?:-[A-ZÅÄÖ][a-zåäö]+)?)\s+([A-ZÅÄÖ][a-zåäö]+(?:-[A-ZÅÄÖ][a-zåäö]+)?)$")
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


_ASCII = str.maketrans("åäöé", "aaoe")


def signature_name(text: str) -> str:
    """Vastaajan tunniste allekirjoituksesta.

    - "Tuuli - Varjomaailman aikuinen" -> "tuuli"
    - "Janne Esimerkki / Neuvontapalvelun vastaaja" -> "janne.esimerkki" (sama muoto kuin suositeltu
      kirjautumistunnus, jolloin "Kirjoita minun tyylilläni" löytää vastaajan omat vastaukset)
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    tail = lines[-6:]
    for i, line in enumerate(tail):
        m = FULLNAME.match(line.rstrip(",. "))
        if m and any(ROLE.search(x) for x in tail[i + 1:i + 3]):
            return f"{m.group(1)}.{m.group(2)}".lower().translate(_ASCII)
    for line in reversed(lines[-3:]):
        m = SIGNATURE.match(line)
        if m:
            return m.group(1).lower().translate(_ASCII)
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


def _first(r: dict, *keys: str) -> str:
    for k in keys:
        v = r.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def rows_records(rows: list[dict], tyyppi: str | None, with_questions: bool) -> list[dict]:
    """Taulukkomuoto: [{"question"/"kysymys", "answer"/"vastaus", "type"?, ...}]. HTML tai pelkkä teksti."""
    out = []
    for r in rows:
        if tyyppi and "type" in r and str(r.get("type")) != str(tyyppi):
            continue
        raw_a = _first(r, "answer", "vastaus")
        atext, links = clean_html(raw_a) if "<" in raw_a else (raw_a.strip(), [])
        atext = strip_feedback(atext.replace("\r\n", "\n"))
        raw_q = _first(r, "question", "kysymys")
        qtext = clean_html(raw_q)[0] if "<" in raw_q else raw_q.strip()
        out.append({
            "id": _first(r, "id") or hashlib.sha1(atext.encode()).hexdigest()[:12],
            "palvelu": str(r.get("type", "")),
            "text": atext,
            "kysymys": qtext if with_questions else "",
            "konteksti": "",
            "aihe": topic(r.get("classifications")) if isinstance(r.get("classifications"), list)
                    else _first(r, "aihe", "classification", "category"),
            "pvm": _first(r, "published_on", "created_on", "pvm")[:10],
            "vastaaja": _first(r, "vastaaja", "respondent") or signature_name(atext),
            "links": [l for l in links if l.startswith("http")] + URL.findall(atext),
            "jatko": False,
        })
    return out


def table_rows(data) -> list[dict] | None:
    """Palauttaa rivit, jos data on taulukkomuotoa ({"rows": [...]} tai lista, jossa vastauskenttä)."""
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        rows = data["rows"]  # esim. {"table": ..., "rows": [...]}
        if rows and isinstance(rows[0], dict) and ("answer" in rows[0] or "vastaus" in rows[0]):
            return rows
    elif isinstance(data, list) and data and isinstance(data[0], dict) and "answer" in data[0]:
        return data
    return None


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


def _key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", text.lower()).strip().encode()).hexdigest()


def build_index(hanke: Hanke, records: list[dict], out_path: Path, min_len: int = 80,
                vmap: dict[str, str] | None = None, append: bool = False) -> str:
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
    existing: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    if append and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.append(line)
                seen.add(_key(json.loads(line)["text"]))
    tmp = out_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for line in existing:  # aiemmin tuodut (jo pseudonymisoituja)
            f.write(line + "\n")
        for r in records:
            if len(r["text"]) < min_len:
                skipped += 1
                continue
            k = _key(r["text"])
            if k in seen:
                duplicates += 1
                continue
            seen.add(k)
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
        f"- Tuotu {written} vastausta ({followups} jatkokysymyksiin), ohitettu {skipped} lyhyttä tai tyhjää"
        + (f" ja {duplicates} jo indeksissä olevaa" if duplicates else ""),
        f"- Indeksissä nyt yhteensä {len(existing) + written} vastausta"
        + (" (lisätty aiempaan indeksiin)" if append else " (aiempi indeksi korvattiin)"),
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
                with_questions: bool = False, min_len: int = 80, vmap: dict[str, str] | None = None,
                append: bool = False) -> str:
    """Ylläpitosivun ja komentorivin yhteinen tuonti. Tunnistaa säieviennin ja taulukkomuodon."""
    hanke = load_hanke(ROOT / "hankkeet", hanke_id)
    cfg = hanke.config.get("vastaukset", {})
    if is_thread_export(data):
        palvelu = palvelu or cfg.get("palvelu")
        records = thread_records(data, palvelu, with_questions)
        if not records:
            services = sorted({t.get("service_name", "") for t in data})
            raise ValueError(f"Palvelulle '{palvelu}' ei löytynyt vastauksia. Viennin palvelut: {', '.join(services)}")
    elif (rows := table_rows(data)) is not None:
        tyyppi = palvelu if palvelu and str(palvelu).isdigit() else cfg.get("tyyppi")
        records = rows_records(rows, str(tyyppi) if tyyppi is not None else None, with_questions)
        if not records:
            types = sorted({str(r.get("type")) for r in rows if "type" in r})
            raise ValueError(f"Palvelutyypille '{tyyppi}' ei löytynyt rivejä. Tiedoston tyypit: {', '.join(types)}")
    else:
        raise ValueError("Tuntematon muoto. Tuetut: säievienti (lista, jossa 'questions') tai taulukko "
                         "({'rows': [{'question', 'answer'}]}).")
    return build_index(hanke, records, out_path, min_len, vmap, append=append)


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
    ap.add_argument("--lisaa", action="store_true", help="lisää nykyiseen indeksiin (oletus: korvaa)")
    ap.add_argument("--min-pituus", type=int, default=80, help="lyhyemmät vastaukset ohitetaan (merkkiä)")
    ap.add_argument("--ulos", type=Path, help="oletus: <APURI_DATA_DIR tai hankkeet>/<hanke>/vastaukset.jsonl")
    ap.add_argument("--raportti", type=Path, help="kirjoita yhteenveto Markdown-tiedostoon")
    args = ap.parse_args(argv)

    data = read_file(args.tiedosto)
    out_path = args.ulos or default_out(args.hanke)
    vmap = parse_map(args.vastaajakartta)
    try:
        if is_thread_export(data) or table_rows(data) is not None:
            report = import_data(args.hanke, data, out_path, args.palvelu, args.kysymykset,
                                 args.min_pituus, vmap, append=args.lisaa)
        else:
            rows = data if isinstance(data, list) else data.get("rows", [])
            if rows and args.vastaus_sarake not in rows[0]:
                print(f"Saraketta '{args.vastaus_sarake}' ei löydy. Sarakkeet: {', '.join(rows[0].keys())}",
                      file=sys.stderr)
                return 2
            hanke = load_hanke(ROOT / "hankkeet", args.hanke)
            report = build_index(hanke, table_records(rows, args), out_path, args.min_pituus, vmap,
                                 append=args.lisaa)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.raportti:
        args.raportti.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
