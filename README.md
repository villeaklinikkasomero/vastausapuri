# Neuvontapalvelun vastausapuri – MVP (linkkiehdottaja)

Apuri ehdottaa neuvontapalvelun vastaajalle 3–5 sopivaa sisältöä säätiön omasta
sisältökatalogista, perustelee valinnat, ehdottaa vastauksen rungon ja merkitsee
mahdolliset kriisisignaalit. Vastauksen kirjoittaa ja lähettää aina ihminen.

## Miten se toimii

1. **Pseudonymisointi** (`pseudonymize.py`) omassa ympäristössä: nimet, paikkakunnat, iät,
   yhteystiedot, henkilötunnukset, osoitteet ja päivämäärät korvataan paikkamerkeillä
   (`[NIMI_1]`). Vastaavuustaulu ei lähde mallille.
2. **Kriisisignaalit** (`safety.py`) tunnistetaan paikallisesti alkuperäisestä tekstistä,
   mallista riippumatta.
3. **Haku** (`catalog.py`) poimii katalogista ehdokkaat merkki-n-grammi-TF-IDF:llä, joka kestää
   suomen taivutusta. Kriisipalvelut ovat aina ehdokkaina.
4. **Luonnostelija** (Claude Opus 5.5) valitsee ehdokkaista linkit ja tekee rungon.
5. **Tarkastaja** (Claude Sonnet 5.5) hyväksyy tai hylkää linkit, voi lisätä paremman ja arvioi
   kriisisignaalit.
6. **Koodin tarkistukset** (`pipeline.py`): vain katalogin id:t kelpaavat, joten malli ei voi
   keksiä osoitteita. Kriisisignaalin yhteydessä kriisipalvelut nostetaan ensimmäisiksi.

## Käynnistys

```bash
pip install -r requirements.txt

# Kehitys ilman mallia (ei verkkoyhteyttä, ei dataa minnekään)
APURI_PROVIDER=mock uvicorn vastausapuri.app:app --port 8000

# Bedrock (AWS-tunnukset tavalliseen tapaan, esim. AWS_PROFILE)
APURI_PROVIDER=bedrock AWS_REGION=eu-north-1 uvicorn vastausapuri.app:app --port 8000
```

Avaa http://localhost:8000. Testit: `pytest -q`.

## Julkaisu Renderiin (Lähde.AI:n työtilaan, omana palvelunaan)

Apuri on oma Render-palvelunsa. Lähde.AI:n koodiin tai palveluun ei kosketa, vain AWS-tunnukset
jaetaan saman Environment Groupin kautta. Kenenkään koneelle ei asenneta mitään.

1. Vie tämä kansio omaan Git-repoon (GitHub/GitLab), jonka Render näkee.
2. Tarkista `render.yaml`:
   - `fromGroup`: Lähde.AI:n Environment Groupin nimi, jossa ovat `AWS_ACCESS_KEY_ID`
     ja `AWS_SECRET_ACCESS_KEY`.
   - `AWS_REGION`: sama alue kuin Lähde.AI:ssa.
   - Mallitunnisteet: samat `eu.`-profiilit, joihin Lähde.AI:n AWS-tilillä on käyttöoikeus.
3. Renderissä: **New → Blueprint** → valitse repo → Apply.
4. Aseta palvelun Environment-välilehdellä `APURI_USERS` muodossa
   `nimi:salasana,nimi2:salasana2`. Ilman sitä palvelu vastaa Bedrock-tilassa 503:lla eikä ole
   auki kenellekään.
5. Avaa palvelun osoite selaimella ja kirjaudu.

Huomioita:
- Palvelu on Frankfurtissa (EU). Varmista, että säätiöllä on Renderin kanssa tietojenkäsittelysopimus
  (DPA), koska kysymykset kulkevat Renderin palvelimen kautta.
- Pääsynhallinta on MVP:ssä HTTP Basic -kirjautuminen. Pilotin jälkeen se kannattaa korvata säätiön
  kertakirjautumisella (esim. Microsoft Entra ID) tai Lähde.AI:n kirjautumisella.
- Pyyntölokitus on pois päältä (`--no-access-log`), eikä sovellus tallenna kysymyksiä.
- Jos IAM-käyttäjä on Lähde.AI:n kanssa yhteinen, sen oikeudet koskevat myös apuria. Pilotissa oma
  IAM-käyttäjä, jolla on oikeus vain `bedrock:InvokeModel`-kutsuun `eu.`-profiileihin, on siistimpi.

## Asetukset (ympäristömuuttujat)

| Muuttuja | Oletus | Selitys |
| --- | --- | --- |
| `APURI_PROVIDER` | `mock` | `bedrock` tai `mock` |
| `AWS_REGION` | `eu-north-1` | EU-lähdealue Bedrock-kutsuille |
| `APURI_DRAFTER_MODEL` | `eu.anthropic.claude-opus-5-5` | Luonnostelija |
| `APURI_CHECKER_MODEL` | `eu.anthropic.claude-sonnet-5-5` | Tarkastaja |
| `APURI_CATALOG` | `data/katalogi.json` | Sisältökatalogin polku |
| `APURI_MAX_LINKS` | `5` | Linkkien enimmäismäärä (kriisipalvelut tulevat lisäksi) |
| `APURI_SPACY_MODEL` | – | Valinnainen suomen NER-malli pseudonymisoinnin tueksi |
| `APURI_USERS` | – | Kirjautumistunnukset `nimi:salasana,...` (pakollinen Bedrock-tilassa) |

Bedrock-tilassa sovellus kieltäytyy käynnistymästä, jos malli ei ole `eu.`-profiili.

## Ennen pilottia

- **Katalogi:** `data/katalogi.json` on keksitty esimerkki, ja sen osoitteet ovat
  `example.org`-osoitteita. Korvaa se säätiön oikeilla sisällöillä. Kriisipalvelujen yhteystiedot
  pitää tarkistaa.
- **Pseudonymisointi:** nimi- ja paikkalistat ovat suppeita. Laajenna niitä, harkitse suomen
  NER-mallia ja testaa oikeilla (sisäisesti käsitellyillä) esimerkeillä.
- **Kriisisanasto:** käy `safety.py`:n lista läpi neuvontapalvelun asiantuntijoiden kanssa.
- **AWS:** salli IAM-politiikassa vain `eu.`-inferenssiprofiilit. Pidä mallikutsujen lokitus
  pois päältä tai ohjaa se omaan salattuun säilöön.
- **Lokitus:** sovellus ei tallenna kysymyksiä. Älä lisää pyyntölokitusta, joka tallentaisi
  pyyntöjen sisällön.

## Rakenne

```
vastausapuri/
  config.py        asetukset ja EU-profiilin pakotus
  pseudonymize.py  paikkamerkit ja palautus
  safety.py        paikallinen kriisisignaalien tunnistus
  catalog.py       katalogi ja haku
  llm.py           Bedrock- ja mock-mallit
  prompts.py       luonnostelijan ja tarkastajan ohjeet
  pipeline.py      kahden mallin putki ja tarkistukset
  app.py           FastAPI-rajapinta
static/index.html  vastaajan käyttöliittymä
data/katalogi.json esimerkkikatalogi
tests/             yksikkötestit (mock-malli)
```
