"""Kunnan tunnistus ja yhdistäminen hyvinvointialueeseen.

Tunnistus tehdään paikallisesti alkuperäisestä kysymyksestä ennen pseudonymisointia. Mallille ja
käyttöliittymään välitetään vain hyvinvointialue ja sen päihdepalvelusivu, ei kuntaa.

Data: hankkeet/yhteiset/hyvinvointialueet.json (Tilastokeskuksen kunta–hyvinvointialue-luokitus
ja Päihdelinkin lista hyvinvointialueiden päihdepalvelusivuista). Päivitä vuosittain kuntaliitosten
ja luokitusmuutosten mukaan.

Taivutus: kunnan nimestä muodostetaan vartalot (Turku -> Turussa, Lahti -> Lahdessa, Seinäjoki ->
Seinäjoella, Joroinen -> Joroisissa, Varkaus -> Varkaudessa) ja niihin sallitaan vain sijapäätteet.
Monitulkintaiset nimet (esim. Salo, Kolari, Nokia, Koski, Lieto) tunnistetaan vain, jos lähellä on
asuinpaikkaan viittaava sana ("asun", "kunta", "kaupunki", "paikkakunta", "täällä" ...).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "hankkeet" / "yhteiset" / "hyvinvointialueet.json"

# Monitulkintaiset: tavallisia sanoja tai etunimiä
AMBIGUOUS = {
    "aura", "salo", "rusko", "koski", "lieto", "pello", "kolari", "nokia", "hanko", "multia", "virrat",
    "kemi", "juva", "ii", "simo", "salla", "inari", "eura", "sauvo", "vaala", "luoto", "soini", "perho",
    "tervo", "kaavi", "ranua", "posio", "akaa", "laukaa", "kannus", "lemi", "kitee", "geta", "sund",
    "masku", "sievi", "somero", "lapua", "teuva", "kuhmo", "paimio", "karvia", "humppila",
    "loppi", "laihia",
}
CUE = re.compile(
    r"\b(?:asu\w*|kotikun\w*|kun(?:ta|nan|nassa|nasta|naan)\w*|kaupun\w*|paikkakun\w*|muut\w*|kotoisin|"
    r"täällä|täältä|alueella|alueen|lähellä|seudu\w*|hyvinvointialue\w*|katko\w*|a-klinik\w*|terveysasem\w*)\b",
    re.I,
)
# Sijapääte, edeltävä sidosvokaali tai -kse-/-de-aines ja liitepartikkeli
_END = (r"(?:e|i|ee|kse|ks|de|te)?"
        r"(?:n|ssa|ssä|sta|stä|lla|llä|lta|ltä|lle|ksi|na|nä|hin|seen|aan|een|iin|oon|uun|yyn|ään|öön|"
        r"an|en|in|on|un|yn|än|ön|ten|jen|ssakin|ssäkin|llakin|lläkin)?(?:kin|kaan|kään|ni|si)?")
_END_REQ = (r"(?:e|i|ee|kse|ks|de|te)?"
            r"(?:n|ssa|ssä|sta|stä|lla|llä|lta|ltä|lle|ksi|na|nä|hin|seen|aan|een|iin|oon|uun|yyn|ään|öön|"
            r"an|en|in|on|un|yn|än|ön|ssakin|ssäkin|llakin|lläkin)(?:kin|kaan|kään|ni|si)?")
_COMPOUND = [("kaupunki", "kaupungi"), ("joki", "joe"), ("koski", "koske"), ("järvi", "järve"),
             ("lahti", "lahde"), ("niemi", "nieme"), ("salmi", "salme"), ("saari", "saare"),
             ("mäki", "mäe"), ("nummi", "numme"), ("vesi", "vede"), ("lampi", "lamme"),
             ("ranta", "ranna"), ("virta", "virra"), ("kumpu", "kummu")]
_GRADE = [("kk", "k"), ("tt", "t"), ("pp", "p"), ("nk", "ng"), ("nt", "nn"), ("mp", "mm"),
          ("lt", "ll"), ("rt", "rr")]
_SPECIAL = {  # epäsäännölliset nimet: (perusmuodot, taivutusvartalot)
    "Uusikaupunki": (["uusikaupunki", "uusikaupunkiin"], ["uudessakaupungi", "uudenkaupungi", "uudestakaupungi"]),
    "Uusikaarlepyy": (["uusikaarlepyy", "nykarleby"], ["uudessakaarlepyy", "uudenkaarlepyy", "uudestakaarlepyy"]),
    "Pihtipudas": (["pihtipudas"], ["pihtiputaa"]),
    "Punkalaidun": (["punkalaidun"], ["punkalaitume"]),
    "Virrat": (["virrat"], ["virroi", "virtoje"]),
    "Mänttä-Vilppula": (["mänttä-vilppula", "mänttä", "vilppula"], ["mänttä-vilppula", "mänttä", "mänt", "vilppula"]),
    "Pedersören kunta": (["pedersöre"], ["pedersöre"]),
    "Koski Tl": (["koski"], ["koske"]),
    "Maarianhamina": (["maarianhamina", "mariehamn"], []),
    "Ii": (["ii"], []),
    "Kangasala": (["kangasala"], ["kangasa"]),
}
# Maakunnat, joiden alue on yksiselitteinen (Uusimaa jaetaan neljään alueeseen + Helsinki, joten sitä ei käytetä)
_REGIONS = {"05": "varsinais-suom", "06": "satakun", "07": "kanta-häm", "08": "pirkanma", "09": "päijät-häm",
            "10": "kymenlaakso", "11": "etelä-karjala", "12": "etelä-savo", "13": "pohjois-savo",
            "14": "pohjois-karjala", "15": "keski-suom", "16": "etelä-pohjanma", "17": "pohjanma",
            "18": "keski-pohjanma", "19": "pohjois-pohjanma", "20": "kainuu", "21": "lap(?:pi|is|ii|in)"}
# Ruotsinkieliset nimet suurimmista kunnista
_SWEDISH = {"Helsinki": ["helsingfors"], "Espoo": ["esbo"], "Vantaa": ["vanda"], "Turku": ["åbo"],
            "Porvoo": ["borgå"], "Vaasa": ["vasa"], "Pietarsaari": ["jakobstad"], "Raasepori": ["raseborg"],
            "Kirkkonummi": ["kyrkslätt"], "Parainen": ["pargas"], "Mustasaari": ["korsholm"],
            "Kokkola": ["karleby"], "Loviisa": ["lovisa"], "Kauniainen": ["grankulla"],
            "Kristiinankaupunki": ["kristinestad"], "Närpiö": ["närpes"], "Hanko": ["hangö"]}


@dataclass(frozen=True)
class Alue:
    koodi: str
    nimi: str
    url: str
    kunta: str  # tunnistettu kunta (vain vastaajan nähtäväksi, ei mallille)


def stems(name: str) -> tuple[list[str], list[str]]:
    """(perusmuodot, taivutusvartalot) pienillä kirjaimilla."""
    if name in _SPECIAL:
        return list(_SPECIAL[name][0]), list(_SPECIAL[name][1])
    n = name.lower()
    base = [n]
    derived: set[str] = set()
    for full, stem in _COMPOUND:
        if n.endswith(full):
            derived.add(n[: -len(full)] + stem)
    if n.endswith("nen"):  # Joroinen -> Joroise-/Joroisi-
        derived.add(n[:-3] + "s")
    if re.search(r"[aeiouyäö][sk]$", n) and n.endswith(("us", "ys", "os", "es", "as")):  # Varkaus, Nurmes, Muhos
        derived.add(n[:-1])
    m = re.search(r"(kk|tt|pp|nk|nt|mp|lt|rt|[aeiouyäölrh][ktp])([aeiouyäö])$", n)
    if m and not any(n.endswith(f) for f, _ in _COMPOUND):
        grp = m.group(1)
        weak = dict(_GRADE).get(grp)
        if weak is None:  # yksittäinen k/t/p: Turku -> Turu, Lieto -> Liedo
            weak = grp[0] + {"k": "", "t": "d", "p": "v"}[grp[1]]
        derived.add(n[: m.start(1)] + weak + m.group(2))
    if n.endswith("i") and not any(n.endswith(f) for f, _ in _COMPOUND):  # Loppi -> Lope-, Keski-Suomi -> -Suome-
        for d in list(derived) + [n]:
            if d.endswith("i") and len(d) > 3 and not n.endswith(("nki", "ki")):
                derived.add(d[:-1] + "e")
    return base + [s.lower() for s in _SWEDISH.get(name, [])], sorted(derived - set(base))


@lru_cache(maxsize=1)
def _load() -> tuple[dict, list[tuple[re.Pattern[str], str, str, bool]]]:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    rules = []
    for k in data["kunnat"]:
        base, derived = stems(k["nimi"])
        # johdetuissa vartaloissa pääte on pakollinen (esim. "Turu" ei yksin ole kunta)
        alts = [re.escape(b) + _END for b in base] + [re.escape(d) + _END_REQ for d in derived]
        pat = re.compile(r"(?<![\wåäö-])(?:" + "|".join(alts) + r")(?![\wåäö])", re.I)
        rules.append((pat, k["nimi"], k["alue"], k["nimi"].lower().split()[0] in AMBIGUOUS))
    for code, stem in _REGIONS.items():
        pat = re.compile(r"(?<![\wåäö-])" + stem + r"[\wåäö]*", re.I)
        rules.append((pat, "(maakunta)", code, False))
    return data, rules


def _has_cue(text: str, start: int) -> bool:
    window = text[max(0, start - 60): start + 60]
    return bool(CUE.search(window))


def detect(text: str) -> list[Alue]:
    """Palauttaa tekstissä mainittujen kuntien hyvinvointialueet (järjestyksessä, ei toistoja)."""
    data, rules = _load()
    text = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", " ", text)  # osoitteet eivät ole paikkakuntia
    found: list[tuple[int, Alue]] = []
    seen: set[str] = set()
    for pat, kunta, alue, ambiguous in rules:
        for m in pat.finditer(text):
            if ambiguous and not _has_cue(text, m.start()):
                continue
            if alue not in seen:
                a = data["alueet"][alue]
                found.append((m.start(), Alue(alue, a["nimi"], a["paihdepalvelut"], kunta)))
                seen.add(alue)
            break
    return [a for _, a in sorted(found, key=lambda x: x[0])]
