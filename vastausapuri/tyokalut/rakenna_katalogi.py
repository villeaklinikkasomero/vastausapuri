"""Päivittää hankkeen katalogin palvelun omalta sivustolta.

Käyttö:
    python -m vastausapuri.tyokalut.rakenna_katalogi dopinglinkki            # kuivaharjoitus, raportti
    python -m vastausapuri.tyokalut.rakenna_katalogi dopinglinkki --kirjoita # päivittää katalogi.json
    python -m vastausapuri.tyokalut.rakenna_katalogi --kaikki --kirjoita     # kaikki hankkeet

Mitä tekee:
- Lukee hanke.toml:n `sivukartat` ja hakee niistä sivujen osoitteet (`ohita`-listan osat pois).
- Hakee jokaisen sivun ja poimii otsikon, kuvauksen (meta description) ja tekstiotteen (`content`).
- Säilyttää käsin kuratoidut tiedot (id, tyyppi, aiheet, kohderyhmä) olemassa olevista kohteista.
- Uudet sivut lisätään merkinnällä "uusi": true, jotta ne voi käydä läpi ja täydentää aiheet.
- Sivut, joita ei enää löydy, merkitään "puuttuu": true (ei poisteta automaattisesti).
- Muiden sivustojen kohteisiin (esim. Tulli, SUEK) ei kosketa.

Turvaraja: jos alle puolet sivuista saadaan haettua, katalogia ei kirjoiteta.
Ajetaan esim. Renderin build-vaiheessa, jolloin katalogi päivittyy jokaisessa julkaisussa.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[2] / "hankkeet"
UA = "A-klinikkasaatio-vastausapuri/1.0 (katalogin paivitys)"
SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form", "aside", "button"}
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "br", "div", "section", "article", "tr"}
MAX_CONTENT = 4000


def fetch(url: str, timeout: int = 20) -> str:
    url = quote(url, safe=":/?&=%#+,;@!$'()*~")  # ääkköset osoitteessa
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fi"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (omat sivustot)
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, "replace")


def sitemap_urls(url: str, depth: int = 0) -> list[str]:
    xml = fetch(url)
    locs = [unescape_xml(x) for x in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.S)]
    if "<sitemapindex" in xml and depth < 2:
        out: list[str] = []
        for sub in locs:
            out += sitemap_urls(sub, depth + 1)
        return out
    return locs


def unescape_xml(s: str) -> str:
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.description = ""
        self.h1 = ""
        self._in_title = False
        self._in_h1 = False
        self._skip = 0
        self._main_depth = 0  # >0 kun ollaan <main>/<article>-elementin sisällä
        self.main_text: list[str] = []
        self.body_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = (a.get("content") or "").strip()
            if name == "og:title":
                self.og_title = (a.get("content") or "").strip()
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag in ("main", "article"):
            self._main_depth += 1
        if tag in BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in ("main", "article") and self._main_depth:
            self._main_depth -= 1
        if tag in BLOCK_TAGS:
            self._newline()

    def _newline(self):
        for buf in (self.main_text, self.body_text):
            if buf and buf[-1] != "\n":
                buf.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._in_h1:
            self.h1 += data
        if self._skip:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        self.body_text.append(text)
        if self._main_depth:
            self.main_text.append(text)

    def result(self, site_name: str) -> dict:
        raw = "".join(self.main_text) if len("".join(self.main_text)) > 200 else "".join(self.body_text)
        lines = [l.strip() for l in raw.split("\n")]
        content = "\n".join(l for l in lines if len(l) > 2)
        title = (self.h1 or self.og_title or self.title).strip()
        title = re.sub(rf"\s*[-|–]\s*{re.escape(site_name)}\s*$", "", title, flags=re.I)
        return {"title": title, "description": self.description, "content": content[:MAX_CONTENT]}


def slug_id(url: str, taken: set[str]) -> str:
    path = unquote(urlparse(url).path).strip("/").split("/")
    base = re.sub(r"[^a-z0-9åäö]+", "-", (path[-1] if path and path[-1] else "etusivu").lower()).strip("-")[:48]
    base = base.translate(str.maketrans("åäö", "aao"))
    cand, n = base, 2
    while cand in taken:
        cand, n = f"{base}-{n}", n + 1
    return cand


def norm(url: str) -> str:
    return unquote(url).lower().rstrip("/")


def build(hanke_id: str, write: bool, delay: float) -> bool:
    d = ROOT / hanke_id
    cfg = tomllib.loads((d / "hanke.toml").read_text(encoding="utf-8"))
    h = cfg.get("hanke", {})
    site = h.get("sivusto", "")
    site_host = urlparse(site).netloc.lower().removeprefix("www.")
    site_name = h.get("nimi", "")
    skip = h.get("ohita", [])
    cat_path = d / "katalogi.json"
    existing = json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else []
    by_url = {norm(it["url"]): it for it in existing}
    taken = {it["id"] for it in existing}

    urls: list[str] = []
    for sm in h.get("sivukartat", []):
        try:
            urls += sitemap_urls(sm)
        except Exception as e:  # noqa: BLE001
            print(f"[{hanke_id}] sivukartta epäonnistui: {sm}: {e}", file=sys.stderr)
    urls = [u for u in dict.fromkeys(urls) if not any(s in u for s in skip)]
    # Myös katalogin käsin lisätyt saman sivuston sivut haetaan
    for it in existing:
        if urlparse(it["url"]).netloc.lower().removeprefix("www.") == site_host and it["url"] not in urls:
            urls.append(it["url"])
    if not urls:
        print(f"[{hanke_id}] ei sivuja haettavaksi", file=sys.stderr)
        return False

    ok = 0
    seen: set[str] = set()
    stats = {"päivitetty": 0, "uusi": 0, "puuttuu": 0}
    for url in urls:
        key = norm(url)
        seen.add(key)
        try:
            html = fetch(url)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410) and key in by_url:
                by_url[key]["puuttuu"] = True
                stats["puuttuu"] += 1
            print(f"  {e.code} {url}", file=sys.stderr)
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  virhe {url}: {e}", file=sys.stderr)
            continue
        ok += 1
        p = PageParser()
        p.feed(html)
        page = p.result(site_name)
        if key in by_url:
            it = by_url[key]
            it.pop("puuttuu", None)
            if it.get("description_source") == "käsin" and page["description"]:
                it["description"] = page["description"]
                it["description_source"] = "sivusto"
            it["content"] = page["content"]
            stats["päivitetty"] += 1
        else:
            new = {
                "id": slug_id(url, taken),
                "title": page["title"] or url,
                "url": url,
                "type": "artikkeli",
                "description": page["description"],
                "topics": [],
                "audience": [],
                "content": page["content"],
                "uusi": True,
            }
            taken.add(new["id"])
            existing.append(new)
            by_url[key] = new
            stats["uusi"] += 1
        time.sleep(delay)

    print(f"[{hanke_id}] haettu {ok}/{len(urls)} · {stats}")
    if ok < len(urls) / 2:
        print(f"[{hanke_id}] alle puolet sivuista saatiin haettua, katalogia ei kirjoiteta", file=sys.stderr)
        return False
    if write:
        cat_path.write_text(json.dumps(existing, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"[{hanke_id}] kirjoitettu {cat_path}")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hanke", nargs="?", help="hankkeen id (kansion nimi)")
    ap.add_argument("--kaikki", action="store_true", help="kaikki hankkeet, joilla on sivukartat")
    ap.add_argument("--kirjoita", action="store_true", help="kirjoita katalogi.json (muuten vain raportti)")
    ap.add_argument("--viive", type=float, default=0.3, help="viive sivuhakujen välillä (s)")
    args = ap.parse_args(argv)
    if args.kaikki:
        ids = [p.name for p in ROOT.iterdir() if (p / "hanke.toml").exists()]
    elif args.hanke:
        ids = [args.hanke]
    else:
        ap.error("anna hankkeen id tai --kaikki")
    results = [build(i, args.kirjoita, args.viive) for i in ids]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
