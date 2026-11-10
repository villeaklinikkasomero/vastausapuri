"""Tutkimustiedon haku Europe PMC:stä (sisältää PubMedin aineiston).

Tietosuoja: hakupalveluun lähtee vain mallin muodostama yleinen englanninkielinen
hakulause (esim. "isotretinoin anabolic steroids acne"), ei kysyjän tekstiä. Hakulauseesta
poistetaan lisäksi paikkamerkit ja kaikki muut kuin sallitut merkit.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Protocol

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UA = "A-klinikkasaatio-vastausapuri/1.0"


@dataclass(frozen=True)
class Paper:
    id: str  # T1, T2 ... (annetaan haun jälkeen)
    title: str
    authors: str
    journal: str
    year: str
    url: str
    abstract: str
    review: bool

    def to_dict(self) -> dict:
        return asdict(self)


class ResearchClient(Protocol):
    def search(self, query: str, n: int, years: int) -> list[Paper]: ...


def sanitize_query(q: str) -> str:
    """Vain yleinen hakulause: paikkamerkit ja erikoismerkit pois, pituus rajattu."""
    q = re.sub(r"\[[A-ZÅÄÖ]+_\d+\]", " ", q)
    q = re.sub(r"[^A-Za-z0-9\-\s\"()]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:200]


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _short_authors(s: str) -> str:
    names = [a.strip() for a in (s or "").rstrip(".").split(",") if a.strip()]
    if not names:
        return ""
    return names[0] + (" et al." if len(names) > 1 else "")


class EuropePMC:
    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout

    def _query(self, q: str) -> list[dict]:
        params = urllib.parse.urlencode(
            {"query": q, "format": "json", "resultType": "core", "pageSize": 10}
        )
        req = urllib.request.Request(f"{API}?{params}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 (kiinteä osoite)
            data = json.loads(r.read().decode("utf-8"))
        return data.get("resultList", {}).get("result", [])

    def search(self, query: str, n: int = 4, years: int = 12) -> list[Paper]:
        return list(_cached_search(self, sanitize_query(query), n, years))


@lru_cache(maxsize=256)
def _cached_search(client: EuropePMC, query: str, n: int, years: int) -> tuple[Paper, ...]:
    if not query:
        return ()
    y = time.gmtime().tm_year
    base = f"({query}) AND PUB_YEAR:[{y - years} TO {y}] AND HAS_ABSTRACT:y"
    reviews = '(PUB_TYPE:"review" OR PUB_TYPE:"systematic-review" OR PUB_TYPE:"meta-analysis")'
    rows: list[dict] = []
    seen: set[str] = set()
    # Ensin katsaukset, sitten muut tutkimukset
    for q in (f"{base} AND {reviews}", base):
        for r in client._query(q):
            key = r.get("doi") or r.get("id")
            if key and key not in seen:
                seen.add(key)
                rows.append(r)
        if len(rows) >= n:
            break
    papers = []
    for i, r in enumerate(rows[:n], 1):
        types = [t.lower() for t in (r.get("pubTypeList") or {}).get("pubType", [])]
        if r.get("pmid"):
            url = f"https://europepmc.org/article/MED/{r['pmid']}"
        elif r.get("doi"):
            url = f"https://doi.org/{r['doi']}"
        else:
            url = f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id', '')}"
        papers.append(
            Paper(
                id=f"T{i}",
                title=_clean(r.get("title", "")),
                authors=_short_authors(r.get("authorString", "")),
                journal=_clean(((r.get("journalInfo") or {}).get("journal") or {}).get("title", "")),
                year=str(r.get("pubYear", "")),
                url=url,
                abstract=_clean(r.get("abstractText", ""))[:2000],
                review=any("review" in t or "meta-analysis" in t for t in types),
            )
        )
    return tuple(papers)


def format_papers(papers: list[Paper], max_chars: int = 1500) -> str:
    out = []
    for p in papers:
        kind = "katsaus" if p.review else "tutkimus"
        out.append(f"[{p.id}] {p.title} ({p.authors}, {p.journal} {p.year}; {kind})\n{p.abstract[:max_chars]}")
    return "\n\n".join(out)
