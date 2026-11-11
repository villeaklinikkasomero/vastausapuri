"""Vastaajan selainkäyttöliittymä. Käynnistys: uvicorn vastausapuri.app:app"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import queue
import threading
import os
import secrets
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
                               StreamingResponse)
from pydantic import BaseModel, Field

from .config import get_settings
from .llm import describe_error, error_code, get_llm
from .hanke import load_all
from .pipeline import MODES, Assistant
from . import palaute
from .kayttajat import Registry
from .tutkimus import EuropePMC
from .tyokalut.tuo_vastaukset import default_out, import_data

STATIC = Path(__file__).resolve().parent.parent / "static"
COOKIE = "apuri_session"
SESSION_HOURS = 10
PUBLIC = {"/healthz", "/login", "/logout"}

app = FastAPI(title="Neuvontapalvelun vastausapuri", docs_url=None, redoc_url=None)

# Istuntojen allekirjoitusavain. Ilman APURI_SECRETia avain arvotaan käynnistyksessä,
# jolloin uudelleenkäynnistys kirjaa kaikki ulos (turvallinen oletus).
_SECRET = (os.getenv("APURI_SECRET") or secrets.token_hex(32)).encode()


def _reg() -> Registry:
    s = get_settings()
    return Registry((s.data_dir or s.hankkeet_dir) / "kayttajat.json")


def _sign(user: str, expires: int) -> str:
    msg = f"{user}|{expires}"
    sig = hmac.new(_SECRET, msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}|{sig}"


def _session_user(request: Request) -> str | None:
    raw = request.cookies.get(COOKIE, "")
    try:
        user, expires, sig = raw.rsplit("|", 2)
        if int(expires) < time.time():
            return None
        if not hmac.compare_digest(_sign(user, int(expires)), raw):
            return None
        u = _reg().get(user)
        return user if u is not None and u.active else None
    except ValueError:
        return None


def _security_headers(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC:
        return _security_headers(await call_next(request))
    if not _reg().any_users():
        if get_settings().provider != "mock":
            # Bedrock-tilassa palvelu ei ole koskaan auki ilman kirjautumista
            return PlainTextResponse(
                "Palvelu on suljettu: APURI_USERS-asetus puuttuu Renderistä.", status_code=503
            )
        return _security_headers(await call_next(request))  # paikallinen kehitys
    if _session_user(request) is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Kirjautuminen vanhentui. Lataa sivu uudelleen."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return _security_headers(await call_next(request))


_LOGIN_PAGE = """<!doctype html><html lang="fi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Vastausapuri – kirjaudu</title>
<style>
 :root{{--bg:#f6f5f2;--panel:#fff;--ink:#1d1d1b;--muted:#5f5e5a;--line:#dddbd4;--accent:#1f5fa8;--warn:#9a3412}}
 @media (prefers-color-scheme:dark){{:root{{--bg:#18181a;--panel:#222225;--ink:#ecebe7;--muted:#a3a29c;--line:#3a3a3e;--accent:#7fb0ea;--warn:#f2a37a}}}}
 body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
   font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}}
 form{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:28px;width:100%;max-width:340px}}
 h1{{font-size:18px;margin:0 0 4px}} p{{color:var(--muted);margin:0 0 20px;font-size:13px}}
 label{{display:block;font-size:13px;margin:12px 0 4px}}
 input{{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid var(--line);border-radius:8px;
   background:var(--bg);color:var(--ink);font:inherit}}
 button{{margin-top:20px;width:100%;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;cursor:pointer}}
 .err{{color:var(--warn);font-size:13px;margin-top:12px}}
</style></head><body>
<form method="post" action="/login">
 <h1>Vastausapuri</h1><p>Neuvontapalvelun vastaajille</p>
 <label for="u">Käyttäjätunnus</label><input id="u" name="username" autocomplete="username" required autofocus>
 <label for="p">Salasana</label><input id="p" name="password" type="password" autocomplete="current-password" required>
 <button type="submit">Kirjaudu</button>{error}
</form></body></html>"""


@app.get("/login")
def login_page() -> HTMLResponse:
    return HTMLResponse(_LOGIN_PAGE.format(error=""))


@app.post("/login")
async def login(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    user = (form.get("username") or [""])[0].strip()
    pw = (form.get("password") or [""])[0]
    ok = _reg().verify(user, pw) is not None
    if not ok:
        await asyncio.sleep(1)  # hidastaa arvailua
        msg = '<div class="err">Väärä käyttäjätunnus tai salasana.</div>'
        return HTMLResponse(_LOGIN_PAGE.format(error=msg), status_code=401)
    expires = int(time.time()) + SESSION_HOURS * 3600
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, _sign(user, expires), max_age=SESSION_HOURS * 3600, httponly=True,
                    secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
                    samesite="strict")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@lru_cache
def assistants() -> dict[str, Assistant]:
    s = get_settings()
    llm = get_llm(s)
    research = EuropePMC() if s.research == "europepmc" else None
    return {hid: Assistant(s, h, llm, research) for hid, h in load_all(s.hankkeet_dir, s.data_dir).items()}


def _allowed(request: Request) -> tuple[str | None, list[str]]:
    user = _session_user(request)
    all_ids = list(assistants())
    if user is None:  # kehitystila ilman käyttäjiä
        return None, all_ids
    u = _reg().get(user)
    projects = u.projects if u else frozenset()
    return user, [h for h in all_ids if projects is None or h in projects]


class Ask(BaseModel):
    question: str = Field(min_length=5, max_length=20000)
    hanke: str
    mode: str = "luonnos"
    own_style: bool = False
    thread: str | None = Field(default=None, max_length=40000)  # aiempi keskustelu jatkokysymykselle
    research: bool = True  # hae tutkimustietoa (jos hankkeella käytössä)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status(request: Request) -> dict:
    user, allowed = _allowed(request)
    a = assistants()
    s = get_settings()
    return {"provider": s.provider, "drafter": s.drafter_model, "checker": s.checker_model,
            "user": user, "modes": list(MODES), "research": s.research, "admin": _is_admin(user),
            "hankkeet": [a[h].h.summary() for h in allowed]}


@app.post("/api/suggest")
def suggest(body: Ask, request: Request) -> dict:
    user, allowed = _allowed(request)
    if body.hanke not in allowed:
        raise HTTPException(status_code=403, detail="Ei oikeutta tähän hankkeeseen.")
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail="Tuntematon tila.")
    # Kysymystä tai vastausta ei lokiteta eikä tallenneta.
    try:
        result, ps = assistants()[body.hanke].run(body.question, mode=body.mode,
                                                 vastaaja=user, own_style=body.own_style,
                                                 thread=body.thread, use_research=body.research)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ehdotusta ei saatu: {_explain(e)}") from None
    return result.to_dict(restore=ps)


@app.post("/api/suggest/stream")
def suggest_stream(body: Ask, request: Request):
    """Sama kuin /api/suggest, mutta lähettää vaiheet sitä mukaa kuin ne alkavat (NDJSON).

    Rivit: {"type":"steps","steps":[...]}, {"type":"step","id":...}, lopuksi
    {"type":"result","data":{...}} tai {"type":"error","detail":"..."}.
    """
    user, allowed = _allowed(request)
    if body.hanke not in allowed:
        raise HTTPException(status_code=403, detail="Ei oikeutta tähän hankkeeseen.")
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail="Tuntematon tila.")
    assistant = assistants()[body.hanke]
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            result, ps = assistant.run(body.question, mode=body.mode, vastaaja=user,
                                       own_style=body.own_style, thread=body.thread,
                                       use_research=body.research,
                                       progress=lambda sid: events.put({"type": "step", "id": sid}))
            events.put({"type": "result", "data": result.to_dict(restore=ps)})
        except Exception as e:  # noqa: BLE001
            events.put({"type": "error", "detail": f"Ehdotusta ei saatu: {_explain(e)}"})
        finally:
            events.put(None)

    def stream():
        yield json.dumps({"type": "steps", "steps": assistant.steps(body.mode, body.research)}) + "\n"
        threading.Thread(target=work, daemon=True).start()
        while True:
            try:
                ev = events.get(timeout=10)
            except queue.Empty:
                yield json.dumps({"type": "ping"}) + "\n"  # pitää yhteyden auki pitkissä vaiheissa
                continue
            if ev is None:
                break
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


def _explain(e: Exception) -> str:
    """AWS:n virheviesti näytetään (se ei sisällä kysymyksen tekstiä), muut vain tyyppinä."""
    hint = {
        "AccessDeniedException": "Tarkista IAM-oikeudet (bedrock:InvokeModel) ja mallin käyttöoikeus Bedrockissa.",
        "ValidationException": "Tarkista mallitunniste (APURI_DRAFTER_MODEL / APURI_CHECKER_MODEL) ja AWS_REGION.",
        "ResourceNotFoundException": "Mallia ei löydy tai se on vanhentunut. Tarkista mallitunniste ja AWS_REGION.",
        "ThrottlingException": "Bedrockin käyttöraja täyttyi. Yritä hetken päästä uudelleen.",
    }.get(error_code(e), "")
    s = get_settings()
    return (f"{describe_error(e)} (luonnostelija {s.drafter_model}, tarkastaja {s.checker_model}, "
            f"alue {s.aws_region}) {hint}").strip()


# --- Ylläpito: vanhojen vastausten tuonti -------------------------------------------------

def _is_admin(user: str | None) -> bool:
    reg = _reg()
    if not reg.any_users():  # paikallinen kehitys ilman käyttäjiä
        return get_settings().provider == "mock"
    u = reg.get(user) if user else None
    return bool(u and u.active and u.admin)


@app.get("/yllapito")
def admin_page(request: Request):
    if not _is_admin(_session_user(request)):
        raise HTTPException(status_code=403, detail="Vain ylläpitäjille (APURI_ADMINS).")
    return FileResponse(STATIC / "yllapito.html")


@app.get("/api/yllapito/tila")
def admin_status(request: Request) -> dict:
    if not _is_admin(_session_user(request)):
        raise HTTPException(status_code=403, detail="Vain ylläpitäjille.")
    s = get_settings()
    out = []
    for hid, a in assistants().items():
        path = default_out(hid)
        out.append({
            "id": hid, "nimi": a.h.nimi, "vastauksia": len(a.h.answers.answers),
            "palvelu": a.h.config.get("vastaukset", {}).get("palvelu", ""),
            "paivitetty": int(path.stat().st_mtime) if path.exists() else None,
        })
    return {"data_dir": str(s.data_dir) if s.data_dir else None, "hankkeet": out}


class ImportBody(BaseModel):
    hanke: str
    palvelu: str | None = None
    kysymykset: bool = False
    data: list = Field(max_length=200000)


@app.post("/api/yllapito/tuo")
def admin_import(body: ImportBody, request: Request) -> dict:
    """Pseudonymisoi ja tallentaa vastausindeksin palvelimelle. Alkuperäistä tiedostoa ei tallenneta."""
    if not _is_admin(_session_user(request)):
        raise HTTPException(status_code=403, detail="Vain ylläpitäjille.")
    s = get_settings()
    if s.provider == "bedrock" and not s.data_dir:
        raise HTTPException(status_code=400, detail="APURI_DATA_DIR puuttuu: ota Renderin levy käyttöön ensin.")
    if body.hanke not in assistants():
        raise HTTPException(status_code=404, detail="Tuntematon hanke.")
    try:
        report = import_data(body.hanke, body.data, default_out(body.hanke), body.palvelu, body.kysymykset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    assistants.cache_clear()  # uusi indeksi käyttöön heti
    return {"raportti": report}


# --- Palaute luonnoksista -----------------------------------------------------------------

class FeedbackBody(BaseModel):
    hanke: str
    tila: str = "luonnos"
    arvio: str  # "hyva" | "huono"
    syyt: list[str] = Field(default_factory=list, max_length=10)
    kommentti: str = Field(default="", max_length=2000)
    meta: dict = Field(default_factory=dict)


@app.get("/api/palaute/syyt")
def feedback_reasons() -> dict:
    return {"syyt": [{"id": k, "teksti": v} for k, v in palaute.REASONS.items()]}


@app.post("/api/palaute")
def feedback(body: FeedbackBody, request: Request) -> dict:
    _, allowed = _allowed(request)
    if body.hanke not in allowed:
        raise HTTPException(status_code=403, detail="Ei oikeutta tähän hankkeeseen.")
    s = get_settings()
    try:
        palaute.save(palaute.path(s.data_dir, s.hankkeet_dir), hanke=body.hanke, mode=body.tila,
                     rating=body.arvio, reasons=body.syyt, comment=body.kommentti, meta=body.meta,
                     safe_domains=assistants()[body.hanke].h.safe_domains)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True}


@app.get("/api/yllapito/palaute")
def feedback_summary(request: Request, paivat: int | None = None) -> dict:
    if not _is_admin(_session_user(request)):
        raise HTTPException(status_code=403, detail="Vain ylläpitäjille.")
    s = get_settings()
    return {"hankkeet": palaute.summary(palaute.load(palaute.path(s.data_dir, s.hankkeet_dir)), paivat)}


# --- Käyttäjähallinta ------------------------------------------------------------------

class UserBody(BaseModel):
    nimi: str = Field(max_length=40)
    salasana: str | None = Field(default=None, max_length=200)
    hankkeet: list[str] | None = None  # None = kaikki hankkeet
    admin: bool = False
    aktiivinen: bool = True


def _require_admin(request: Request) -> str:
    user = _session_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Vain ylläpitäjille.")
    return user or ""


def _check_projects(projects: list[str] | None) -> None:
    if projects is not None:
        unknown = set(projects) - set(assistants())
        if unknown:
            raise HTTPException(status_code=400, detail=f"Tuntematon hanke: {', '.join(sorted(unknown))}")


@app.get("/api/yllapito/kayttajat")
def users_list(request: Request) -> dict:
    me = _require_admin(request)
    return {"kayttajat": _reg().list(), "minä": me,
            "hankkeet": [{"id": h, "nimi": a.h.nimi} for h, a in assistants().items()]}


@app.post("/api/yllapito/kayttajat")
def users_create(body: UserBody, request: Request) -> dict:
    _require_admin(request)
    _check_projects(body.hankkeet)
    try:
        _reg().upsert(body.nimi.strip().lower(), password=body.salasana, projects=body.hankkeet,
                      admin=body.admin, active=body.aktiivinen, create=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True}


@app.put("/api/yllapito/kayttajat/{nimi}")
def users_update(nimi: str, body: UserBody, request: Request) -> dict:
    me = _require_admin(request)
    _check_projects(body.hankkeet)
    if nimi == me and (not body.admin or not body.aktiivinen):
        raise HTTPException(status_code=400, detail="Et voi poistaa omia ylläpitäjäoikeuksiasi tai sulkea omaa tunnustasi.")
    try:
        _reg().upsert(nimi, password=body.salasana or None, projects=body.hankkeet,
                      admin=body.admin, active=body.aktiivinen)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True}


@app.delete("/api/yllapito/kayttajat/{nimi}")
def users_delete(nimi: str, request: Request) -> dict:
    me = _require_admin(request)
    if nimi == me:
        raise HTTPException(status_code=400, detail="Et voi poistaa omaa tunnustasi.")
    try:
        _reg().delete(nimi)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True}


class PasswordBody(BaseModel):
    vanha: str = Field(max_length=200)
    uusi: str = Field(max_length=200)


@app.post("/api/salasana")
def change_password(body: PasswordBody, request: Request) -> dict:
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Kirjaudu ensin.")
    try:
        _reg().change_password(user, body.vanha, body.uusi)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True}


@app.get("/salasana")
def password_page() -> FileResponse:
    return FileResponse(STATIC / "salasana.html")
