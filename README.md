# Neuvontapalvelun vastausapuri

Apuri kirjoittaa neuvontapalvelun vastaajalle vastausluonnoksen, jossa on valmiina linkit palvelun
omiin sisältöihin ja oikeisiin jatko-ohjauksiin. Toinen malli tarkistaa luonnoksen, ja vastaaja
muokkaa ja lähettää vastauksen itse. Ensimmäinen hanke on **Dopinglinkki**, ja uusia hankkeita voi
lisätä ilman koodimuutoksia.

## Tilat

| Tila | Mitä vastaaja saa |
| --- | --- |
| **Luonnos** | Kokonainen vastausluonnos, linkit, tarkastajan huomiot (vakavuus, lainaus, korjausehdotus) ja `[TARKISTA: …]`-merkinnät kohtiin, joille ei löytynyt lähdettä |
| **Vain linkit** | 3–5 linkkiä perusteluineen, vastauksen runko ja huomiot |

**Kirjoita minun tyylilläni**: kun vastaajan omia vanhoja vastauksia on tuotu indeksiin (ks. alla),
luonnostelija saa esimerkeiksi ensisijaisesti hänen omiaan ja jäljittelee hänen tapaansa kirjoittaa.

## Miten se toimii

1. **Pseudonymisointi** (`pseudonymize.py`) omassa ympäristössä: nimet, paikkakunnat, iät, yhteystiedot,
   henkilötunnukset, osoitteet ja päivämäärät korvataan paikkamerkeillä (`[NIMI_1]`). Palvelun omat linkit
   säilytetään. Nimet palautetaan vasta vastaajan näkymässä.
2. **Kriisisignaalit** (`safety.py`) tunnistetaan paikallisesti: yhteiset (itsetuhoisuus, väkivalta,
   myrkytys) ja hankekohtaiset (Dopinglinkillä mm. pistoskohdan tulehdus, rintakipu, ylikuumeneminen).
3. **Haku** (`catalog.py`): katalogista ehdokkaat ja niiden tekstiotteet lähteiksi, vastausindeksistä
   samankaltaiset vanhat vastaukset esimerkeiksi.
4. **Luonnostelija** (Claude Opus 4.6) kirjoittaa luonnoksen hankkeen ohjeiden mukaan ja viittaa
   sisältöihin merkinnällä `[[id]]`.
5. **Tarkastaja** (Claude Sonnet 4.6) etsii asiavirheet, periaatteiden vastaiset kohdat (esim.
   annosteluohjeet, diagnoosit, moralisointi), puuttuvat hoitoon ohjaukset ja sopimattomat linkit.
6. **Koodin tarkistukset** (`pipeline.py`): `[[id]]`-viittaukset muutetaan katalogin osoitteiksi. Malli ei voi
   keksiä osoitteita: tuntematon viittaus muuttuu `[TARKISTA]`-merkinnäksi. Kriisipalvelut nostetaan ensimmäisiksi.

## Tutkimustieto

Luonnokselle haetaan tutkimustietoa kahdesta lähteestä:

- **Hankkeen omat tutkimustiivistelmät.** Dopinglinkin suomenkieliset tiivistelmät ovat katalogissa
  tyyppinä `tutkimus`, ja ne päivittyvät sivukartasta (`research_summary-sitemap1.xml`).
- **Europe PMC** (sisältää PubMedin). Malli muodostaa kysymyksestä lyhyen englanninkielisen hakulauseen,
  ja haku tuo 3–5 tiivistelmää. Katsaukset tulevat ensin, ja julkaisut rajataan viimeiseen 12 vuoteen.
  Luonnostelija käyttää niitä tukemaan väitteitä ilman viitteitä vastaustekstissä. Tarkastaja vertaa
  luonnosta niihin, ja vastaaja näkee lähteet linkkeineen ja tiedon siitä, mitä luonnoksessa käytettiin.
  Hakuun lähtee vain yleinen hakulause, josta poistetaan paikkamerkit ja erikoismerkit, ei kysyjän tekstiä.
  Hankekohtaiset asetukset ovat `hanke.toml`-tiedoston `[tutkimus]`-osiossa. Vastaaja voi kytkeä haun
  pois kysymyskohtaisesti.

## Asiantuntijalista

`hanke.toml`-tiedoston `[[asiantuntijat]]`-kohdat ovat työryhmän ylläpitämä lista lääkäreistä ja palveluista,
joihin saa ohjata nimeltä (nimi, erikoisala, alue, milloin, huom, tarkistettu). Luonnostelija mainitsee nimeltä
vain listalla olevia, ja tarkastaja merkitsee vakavaksi, jos luonnoksessa on muu nimi. Lista kannattaa
tarkistaa säännöllisesti, esimerkiksi ottaako lääkäri uusia potilaita.

## Lakitieto

Keskeiset lainkohdat ovat katalogissa tyyppinä `laki`, ja niissä on tarkistettu tiivistelmä sekä linkki
Finlexin ajantasaiseen lakiin. Luonnostelija perustaa lakiasiat niihin, eikä se anna oikeudellista arviota
yksittäistapauksesta. Tarkastaja vertaa lakiväitteitä tiivistelmiin.

| Kohde | Missä | Sisältö |
| --- | --- | --- |
| `laki-dopingrikos` | Dopinglinkki | RL 44:6–8 dopingrikokset, 44:16 dopingaineen määritelmä, käyttö ei rangaistavaa |
| `laki-laakkeiden-tuonti` | Dopinglinkki | Netistä tilaaminen (EU/ETA, resepti, 3 kk) ja matkalta tuonti |
| `laki-potilasasiakirjat` | yhteiset | Potilaslaki 12–13 §: kirjaaminen, salassapito, omien tietojen tarkastus ja korjaus, vakuutukset |
| `laki-vaitiolovelvollisuus` | yhteiset | Terveydenhuollon ammattihenkilöiden vaitiolovelvollisuus |
| `laki-vakuutus` | yhteiset | Vakuutussopimuslaki: tiedonantovelvollisuus vakuutusta haettaessa |

Tiivistelmät on tarkistettu 10.11.2026 (kenttä `tarkistettu`). Ne kannattaa käydä läpi lakiasioihin
perehtyneen kanssa ja päivittää, kun lakeja muutetaan. Uuden lakikohteen voi lisätä hankkeen tai
yhteiseen `katalogi.json`-tiedostoon samalla rakenteella.

## Palaute

Jokaisen luonnoksen alla kysytään "Oliko luonnoksesta apua?". Vastaaja valitsee Kyllä tai Ei kovin,
voi merkitä syitä (asiavirhe, puuttuva ohjaus, turvallisuus, sävy, pituus, ei vastannut, linkit, kieli) ja
kirjoittaa kommentin. Tallennetaan vain arvio, syyt, pseudonymisoitu kommentti ja tekniset tiedot (hanke,
tila, mallit, huomioiden ja linkkien määrä, kesto) tiedostoon `<APURI_DATA_DIR>/palaute.jsonl`. Kysymystä,
luonnosta tai vastaajan nimeä ei tallenneta. Yhteenveto (hyödyllisten osuus, yleisimmät korjattavat ja
uusimmat kommentit) näkyy ylläpitosivulla.

## Hankkeet

```
hankkeet/
  yhteiset/katalogi.json        kaikille yhteiset (kriisipalvelut)
  dopinglinkki/hanke.toml       nimi, sivusto, sivukartat, ohjeet mallille, tervehdys, allekirjoitus, kriisisignaalit
  dopinglinkki/katalogi.json    sisällöt ja ohjauskohteet (Dopinglinkin sivut + SUEK, Tulli, Fimea, terveysneuvontapisteet ...)
```

**Uusi hanke:** kopioi `hankkeet/dopinglinkki` uudeksi kansioksi, muokkaa `hanke.toml` (erityisesti
`[ohjeet]` ja `sivukartat`) ja rakenna katalogi:

```bash
python -m vastausapuri.tyokalut.rakenna_katalogi <hanke> --kirjoita
```

Käy uudet kohteet (`"uusi": true`) läpi ja lisää niille `topics` ja `audience`. Hankkeen näkyvyys
rajataan käyttäjäkohtaisesti (ks. `APURI_USERS`).

### Katalogi

`katalogi.json`-kohteessa on `id`, `title`, `url`, `type` (`artikkeli`, `testi`, `palvelu`, `kriisi`),
`description`, `topics`, `audience` ja `content` (sivun tekstiote, jonka katalogityökalu täyttää).
Renderissä katalogi päivitetään sivustolta jokaisen julkaisun yhteydessä. Käsin kuratoidut tiedot
(id, tyyppi, aiheet, kohderyhmä) säilyvät.

## Vanhat vastaukset (vastaajan ääni ja ohjausmallit)

**Ylläpitosivulla** (`/yllapito`, vain `APURI_ADMINS`-käyttäjille) ladataan neuvontapalvelun säievienti
(JSON). Apuri poimii hankkeen palvelun säikeet (`hanke.toml`: `[vastaukset] palvelu = "Doping"`), siistii
HTML:n, poistaa palautepyynnöt, pseudonymisoi tekstit ja tallentaa indeksin levylle (`APURI_DATA_DIR`).
Alkuperäistä tiedostoa ei tallenneta, ja uusi tuonti korvaa vanhan indeksin. Raportissa näkyvät määrät,
aiheet, vastaajat ja ohjaukset sekä linkit, joita ei vielä ole katalogissa.

- Kysymykset tuodaan vain, jos "Kysymykset on siistitty tunnisteista" on valittu. Silloin haku vertaa uutta
  kysymystä vanhoihin, ja esimerkit annetaan mallille muodossa kysymys + vastaus (+ aiempi keskustelu
  jatkokysymyksissä).
- Vastaaja tunnistetaan allekirjoituksesta (esim. "Tuuli - Varjomaailman aikuinen"), jos sellainen on.

Komentorivillä sama onnistuu näin:

```bash
python -m vastausapuri.tyokalut.tuo_vastaukset dopinglinkki neuvonta-threads.json --kysymykset --raportti raportti.md
```

Myös CSV- ja JSON-taulukot toimivat (`--vastaus-sarake`, `--vastaaja-sarake` ...). **Tulos `vastaukset.jsonl`
ei kuulu Gitiin** (`.gitignore`).

## Käynnistys paikallisesti

```bash
pip install -r requirements.txt
APURI_PROVIDER=mock uvicorn vastausapuri.app:app --port 8000   # ilman mallia
pytest -q
```

## Julkaisu Renderiin

Palvelu on `render.yaml`-Blueprint. Lähde.AI:hin ei kosketa.

1. Renderissä: **New → Blueprint** → tämä repo (tai **Manual sync**, jos Blueprint on jo olemassa).
2. Render kysyy `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` ja `APURI_USERS`.
3. Julkaise: **Manual Deploy → Deploy latest commit** (automaattinen julkaisu on pois päältä).

Huomioita:
- Palvelu on Frankfurtissa. Varmista Renderin tietojenkäsittelysopimus (DPA).
- Pilottia varten apurille kannattaa tehdä oma IAM-käyttäjä, jolla on oikeus vain `bedrock:InvokeModel`
  -kutsuun `eu.`-profiileihin.
- Pyyntölokitus on pois päältä, eikä sovellus tallenna kysymyksiä.

## Asetukset (ympäristömuuttujat)

| Muuttuja | Oletus | Selitys |
| --- | --- | --- |
| `APURI_PROVIDER` | `mock` | `bedrock` tai `mock` |
| `AWS_REGION` | `eu-north-1` | EU-lähdealue Bedrock-kutsuille |
| `APURI_DRAFTER_MODEL` | `eu.anthropic.claude-opus-4-6-v1` | Luonnostelija |
| `APURI_CHECKER_MODEL` | `eu.anthropic.claude-sonnet-4-6` | Tarkastaja |
| `APURI_USERS` | – | `nimi:salasana[:hanke1\|hanke2],...`; ilman hankeosaa kaikki hankkeet. Pakollinen Bedrock-tilassa |
| `APURI_SECRET` | arvottu | Istuntojen allekirjoitusavain (Render arpoo) |
| `APURI_DATA_DIR` | `/var/data` (Render) | Vanhojen vastausten kansio (`<kansio>/<hanke>/vastaukset.jsonl`), Renderin levy |
| `APURI_ADMINS` | – | Ylläpitäjät (pilkuin eroteltuina), pääsy `/yllapito`-sivulle |
| `APURI_TUTKIMUS` | `europepmc` (Bedrock), `pois` (mock) | Tutkimushaku päälle/pois koko palvelussa |
| `APURI_HANKKEET` | `hankkeet` | Hankekansio |
| `APURI_MAX_LINKS` | `5` | Linkkien enimmäismäärä (kriisipalvelut tulevat lisäksi) |
| `APURI_CANDIDATES` | `12` | Hakuehdokkaita mallille |
| `APURI_SOURCES` | `4` | Lähdeotteita luonnokselle |
| `APURI_EXAMPLES` | `3` | Esimerkkivastauksia luonnokselle |
| `APURI_SPACY_MODEL` | – | Valinnainen suomen NER-malli pseudonymisoinnin tueksi |

## Ennen pilottia

- **Dopinglinkin ohjeet** (`hankkeet/dopinglinkki/hanke.toml`): tervehdys, allekirjoitus ja periaatteet
  käydään läpi vastaajien kanssa.
- **Kriisipalvelujen yhteystiedot** (`hankkeet/yhteiset/katalogi.json`) tarkistetaan.
- **Pseudonymisoinnin nimi- ja paikkalistat** laajennetaan, ja niitä testataan oikeilla esimerkeillä.

## Rakenne

```
vastausapuri/
  app.py            rajapinta, kirjautuminen, hankeoikeudet
  pipeline.py       kahden mallin putki (linkit / luonnos)
  prompts.py        mallien ohjeet (hankekohtainen sisältö hanke.toml:sta)
  hanke.py          hankkeiden lataus
  catalog.py        katalogi ja n-grammihaku
  answers.py        vanhojen vastausten indeksi
  tutkimus.py       Europe PMC -tutkimushaku
  pseudonymize.py   paikkamerkit
  safety.py         kriisisignaalit
  llm.py            Bedrock- ja mock-mallit
  tyokalut/         rakenna_katalogi.py, tuo_vastaukset.py
static/index.html   käyttöliittymä
hankkeet/           hankkeiden asetukset ja katalogit
tests/              testit
```
