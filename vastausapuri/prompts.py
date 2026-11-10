"""Mallien ohjeet. Hankekohtainen sisältö (ohjeet, tervehdys, allekirjoitus) tulee hanke.toml:sta."""
from __future__ import annotations

import json

from .answers import Answer
from .catalog import CatalogItem
from .hanke import Hanke

_COMMON = """Kysymyksessä on paikkamerkkejä kuten [NIMI_1]; älä yritä arvata niiden sisältöä.
Älä koskaan kirjoita URL-osoitteita: viittaa sisältöihin vain ehdokkaiden id:illä."""


def _context(h: Hanke) -> str:
    out = f"PALVELU: {h.nimi}. {h.kuvaus}"
    if h.ohjeet:
        out += f"\n\nPALVELUN PERIAATTEET JA OHJAUKSET:\n{h.ohjeet}"
    return out


# --- Tutkimushaun hakulause ------------------------------------------------------

def research_query_system(h: Hanke) -> str:
    aihe = h.research.get("aihe", "")
    return f"""Muodostat HAKULAUSEEN lääketieteelliseen tietokantaan (Europe PMC / PubMed).

Palvelu: {h.nimi}. Aihepiiri: {aihe}

Saat neuvontapalveluun tulleen kysymyksen. Muodosta lyhyt englanninkielinen hakulause
(2–6 lääketieteellistä termiä, voit käyttää AND/OR ja lainausmerkkejä), joka löytää tutkimustiedon
kysymyksen ydinasiaan. ÄLÄ sisällytä henkilötietoja, ikiä, paikkoja, päivämääriä tai annoksia.
Jos kysymys ei kaipaa tutkimustietoa (esim. pelkkä palvelu- tai lakiasia), aseta "tarpeen": false.

Vastaa VAIN JSON-oliona: {{"tarpeen": true, "haku": "..."}}"""


# --- Linkkitila (vaihe 1) -------------------------------------------------------

def links_system(h: Hanke, max_links: int) -> str:
    return f"""Olet LUONNOSTELIJA, joka avustaa neuvontapalvelun vastaajaa.

{_context(h)}

Vastaaja on ammattilainen ja kirjoittaa lopullisen vastauksen itse. Tehtäväsi:
1. Tunnista kysymyksen aiheet ja kuka kysyy (itse, läheinen, ammattilainen tai ei tiedossa).
2. Valitse EHDOKKAISTA enintään {max_links} kysyjälle hyödyllisintä sisältöä ja perustele valinta
   vastaajalle yhdellä lauseella. Jos mikään ehdokas ei sovi, palauta tyhjä lista.
3. Ehdota vastauksen runko 3–5 lyhyenä kohtana.
4. Kirjaa huomiot, jotka vastaajan kannattaa tietää (kriisisignaalit, epäselvyydet, ohjaustarpeet).

{_COMMON}

Vastaa VAIN JSON-oliona:
{{"aiheet": ["..."], "kysyja": "itse|läheinen|ammattilainen|ei tiedossa",
 "linkit": [{{"id": "...", "perustelu": "..."}}], "runko": ["..."], "huomiot": ["..."]}}"""


# --- Luonnostila (vaihe 2) -------------------------------------------------------

def draft_system(h: Hanke, max_links: int, own_style: bool, followup: bool = False) -> str:
    v = h.vastaus
    style = (
        "Esimerkeistä osa on merkitty OMA: ne ovat tämän vastaajan omia vastauksia. Jäljittele niiden "
        "sävyä, rakennetta, sanavalintoja, tervehdystä ja lopetusta mahdollisimman tarkasti."
        if own_style
        else "Ota esimerkeistä mallia palvelun yleiseen sävyyn ja tapaan ohjata, mutta älä kopioi niitä."
    )
    greeting = v.get("jatko_tervehdys", h.tervehdys) if followup else h.tervehdys
    closing = "" if followup else v.get("lopetus", "")
    task = (
        "Tämä on JATKOKYSYMYS. Aiempi keskustelu on annettu kontekstiksi. Kirjoita vastaus vain "
        "jatkokysymykseen: älä toista aiemmassa vastauksessa jo kerrottua, vaan viittaa siihen tarvittaessa."
        if followup else "Kirjoita vastaus kysymykseen."
    )
    return f"""Olet LUONNOSTELIJA, joka kirjoittaa vastausluonnoksen neuvontapalvelun vastaajalle.

{_context(h)}

Vastaaja on ammattilainen: hän tarkistaa, muokkaa ja lähettää vastauksen omissa nimissään.
Kirjoita luonnos, joka on valmis mutta jonka vastaaja voi helposti muokata. {task}

KIRJOITUSOHJEET:
- Suomeksi; {v.get("puhuttelu", "sinuttelu")}; {v.get("pituus", "noin 150–350 sanaa")}. Selkeää yleiskieltä,
  kappaleina ilman otsikoita. Vastaa siihen, mitä kysyttiin.
- Aloita tervehdyksellä "{greeting}"{f' ja päätä ennen allekirjoitusta lauseeseen: "{closing}"' if closing else ""}.
- Allekirjoitus:
{h.allekirjoitus or "(ei allekirjoitusta)"}
- Perusta tieto LÄHDEOTTEISIIN ja ehdokkaiden kuvauksiin. Yleisesti tunnettu lääketieteellinen perustieto
  on sallittu. Merkitse muodossa [TARKISTA: lyhyt syy] erityisesti luvut, ajat, annokset, lakiasiat ja
  muut täsmälliset väitteet, jotka eivät perustu lähteisiin, jotta vastaaja tarkistaa ne.
- Linkitä enintään {max_links} ehdokasta kirjoittamalla tekstiin [[id]] siihen kohtaan, jossa sisältöön viitataan
  (esim. "Lue lisää turvallisemmasta pistämisestä: [[turvallisempi-pistaminen]]"). Linkki ei ole pakollinen,
  jos mikään ehdokas ei aidosti auta kysyjää.
- {style}
- Älä toista esimerkkien henkilöihin liittyviä yksityiskohtia.
- Älä kirjoita palautepyyntöä: se lisätään automaattisesti.
- Jos mukana on TUTKIMUSLÄHTEITÄ (englanninkielisiä tiivistelmiä), voit käyttää niitä tukemaan
  väitteitä. Kirjoita silti suomeksi yleistajuisesti, älä lisää viitteitä tai tutkimusten nimiä
  vastaustekstiin, äläkä yleistä yksittäisen tutkimuksen tulosta liikaa. Kirjaa metatietoihin,
  mitä lähteitä käytit ja mihin väitteeseen (kenttä "tutkimukset").

{_COMMON}

Vastaa kahdessa osassa täsmälleen tässä muodossa:
<meta>
{{"aiheet": ["..."], "kysyja": "itse|läheinen|ammattilainen|ei tiedossa",
 "linkit": [{{"id": "...", "perustelu": "..."}}], "huomiot": ["..."],
 "tutkimukset": [{{"id": "T1", "kaytto": "mihin väitteeseen"}}]}}
</meta>
<luonnos>
(vastausluonnos)
</luonnos>"""


CHECKER_LINKS = """Olet TARKASTAJA neuvontapalvelun vastausapurissa.

{context}

Saat kysymyksen, ehdokassisällöt ja toisen mallin LUONNOKSEN linkkiehdotuksista. Tarkista:
1. Sopiiko kukin ehdotettu linkki kysyjän tilanteeseen ja rooliin? Hylkää sopimattomat ja kerro syy.
2. Puuttuuko ehdokkaista selvästi parempi sisältö tai tärkeä ohjaus? Lisää se kohtaan "lisaa".
3. Onko kysymyksessä merkkejä itsetuhoisuudesta, väkivallasta, lapsen vaarantumisesta tai akuutista
   terveysvaarasta? Jos on, kriisi.havaittu = true ja lyhyt kuvaus.
4. Onko ehdotuksissa jotain palvelun periaatteiden vastaista? Kirjaa huomioihin.

Vastaa VAIN JSON-oliona:
{{"linkit": [{{"id": "...", "hyvaksytty": true, "syy": "..."}}],
 "lisaa": [{{"id": "...", "syy": "..."}}], "huomiot": ["..."],
 "kriisi": {{"havaittu": false, "kuvaus": ""}}}}"""

CHECKER_DRAFT = """Olet TARKASTAJA neuvontapalvelun vastausapurissa. Toinen malli on kirjoittanut
vastausluonnoksen, jonka ihmisvastaaja vielä tarkistaa. Etsi asiat, jotka vastaajan pitää korjata.

{context}

Tarkista:
1. Asiavirheet: onko luonnoksessa väitteitä, jotka ovat ristiriidassa LÄHDEOTTEIDEN tai
   TUTKIMUSLÄHTEIDEN kanssa, yleistävät yksittäistä tutkimusta liikaa tai joita ei voi perustella?
   Lainaa kohta.
2. Periaatteet: annostelu- tai kuuriohjeita, hankintaohjeita, diagnooseja, moralisointia tai
   syyllistämistä, liian varmoja lupauksia.
3. Turvallisuus: jos kysymyksessä on akuutteja oireita tai kriisisignaaleja, kehotetaanko
   hakeutumaan hoitoon? kriisi.havaittu = true, jos niitä on.
4. Linkit: sopivatko luonnoksen [[id]]-viittaukset? Puuttuuko tärkeä ohjaus ehdokkaista?
5. Vastaako luonnos varsinaiseen kysymykseen? Jatkokysymyksessä: vastataanko kaikkiin uusiin
   kysymyksiin, ja vältetäänkö aiemmin kerrotun toistamista?

Ole tarkka mutta älä keksi ongelmia. Jos luonnos on kunnossa, palauta tyhjä ongelmalista.

Vastaa VAIN JSON-oliona:
{{"linkit": [{{"id": "...", "hyvaksytty": true, "syy": "..."}}],
 "lisaa": [{{"id": "...", "syy": "..."}}],
 "ongelmat": [{{"vakavuus": "korkea|keski|matala", "kohta": "lyhyt lainaus luonnoksesta",
               "kuvaus": "...", "ehdotus": "..."}}],
 "huomiot": ["..."], "kriisi": {{"havaittu": false, "kuvaus": ""}}}}"""


def checker_system(h: Hanke, draft_mode: bool) -> str:
    return (CHECKER_DRAFT if draft_mode else CHECKER_LINKS).format(context=_context(h))


# --- Käyttäjäviestit --------------------------------------------------------------

def format_candidates(items: list[CatalogItem]) -> str:
    lines = []
    for it in items:
        lines.append(f"- id: {it.id}")
        lines.append(f"  otsikko: {it.title}")
        lines.append(f"  tyyppi: {it.type}")
        lines.append(f"  kohderyhmä: {', '.join(it.audience) or '-'}")
        lines.append(f"  aiheet: {', '.join(it.topics) or '-'}")
        lines.append(f"  kuvaus: {it.description}")
    return "\n".join(lines)


def format_sources(items: list[CatalogItem], max_chars: int = 2500) -> str:
    out = []
    for it in items:
        body = (it.content or it.description).strip()
        out.append(f"[{it.id}] {it.title}\n{body[:max_chars]}")
    return "\n\n".join(out) or "(ei lähdeotteita)"


def format_examples(examples: list[Answer], own: str | None) -> str:
    out = []
    for i, a in enumerate(examples, 1):
        tag = "OMA" if own and a.vastaaja == own else "MUU VASTAAJA"
        head = f"Esimerkki {i} ({tag})" + (f", aihe: {a.aihe}" if a.aihe else "")
        out.append(f"{head}\n{a.text.strip()}")
    return "\n\n---\n\n".join(out)


def links_user(question: str, candidates: list[CatalogItem], thread: str | None = None) -> str:
    head = f"AIEMPI KESKUSTELU (pseudonymisoitu):\n{thread}\n\nJATKOKYSYMYS" if thread else "KYSYMYS"
    return f"{head} (pseudonymisoitu):\n{question}\n\nEHDOKKAAT:\n{format_candidates(candidates)}"


def draft_user(question: str, candidates: list[CatalogItem], sources: list[CatalogItem],
               examples: list[Answer], own: str | None, thread: str | None = None,
               research: str | None = None) -> str:
    parts = []
    if thread:
        parts.append(f"AIEMPI KESKUSTELU (pseudonymisoitu, vanhin ensin):\n{thread}")
    parts += [
        f"{'JATKOKYSYMYS' if thread else 'KYSYMYS'} (pseudonymisoitu):\n{question}",
        f"EHDOKKAAT:\n{format_candidates(candidates)}",
        f"LÄHDEOTTEET (palvelun omista sisällöistä):\n{format_sources(sources)}",
    ]
    if research:
        parts.append(f"TUTKIMUSLÄHTEET (Europe PMC, englanninkieliset tiivistelmät):\n{research}")
    if examples:
        parts.append(f"ESIMERKKIVASTAUKSIA (pseudonymisoituja):\n{format_examples(examples, own)}")
    return "\n\n".join(parts)


def checker_user(question: str, candidates: list[CatalogItem], draft: dict,
                 sources: list[CatalogItem] | None = None, draft_text: str | None = None,
                 thread: str | None = None, research: str | None = None) -> str:
    parts = [f"AIEMPI KESKUSTELU:\n{thread}"] if thread else []
    parts += [f"{'JATKOKYSYMYS' if thread else 'KYSYMYS'} (pseudonymisoitu):\n{question}",
              f"EHDOKKAAT:\n{format_candidates(candidates)}"]
    if sources is not None:
        parts.append(f"LÄHDEOTTEET:\n{format_sources(sources)}")
    if research:
        parts.append(f"TUTKIMUSLÄHTEET:\n{research}")
    parts.append(f"LUONNOS:\n{json.dumps(draft, ensure_ascii=False, indent=1)}")
    if draft_text is not None:
        parts.append(f"LUONNOKSEN TEKSTI:\n{draft_text}")
    return "\n\n".join(parts)
