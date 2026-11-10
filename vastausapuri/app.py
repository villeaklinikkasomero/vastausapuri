"""Vastaajan selainkäyttöliittymä. Käynnistys: uvicorn vastausapuri.app:app"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .llm import get_llm
from .hanke import load_all
from .pipeline import MODES, Assistant

STATIC = Path(__file__).resolve().parent.parent / "static"
COOKIE = "apuri_session"
SESSION_HOURS = 10
PUBLIC = {"/healthz", "/login", "/logout"}

app = FastAPI(title="Neuvontapalvelun vastausapuri", docs_url=None, redoc_url=None)

# Istuntojen allekirjoitusavain. Ilman APURI_SECRETia avain arvotaan käynnistyksessä,
# jolloin uudelleenkäynnistys kirjaa kaikki ulos (turvallinen oletus).
_SECRET = (os.getenv("APURI_SECRET") or secrets.token_hex(32)).encode()


def _users() -> dict[str, tuple[str, set[str] | None]]:
    """Käyttäjät: APURI_USERS="nimi:salasana[:hanke1|hanke2],nimi2:salasana2".

    Ilman hankeosaa käyttäjä näkee kaikki hankkeet. Käyttäjänimeä käytetään myös
    vastaajatunnisteena, kun luonnos kirjoitetaan omalla tyylillä.
    """
    out: dict[str, tuple[str, set[str] | None]] = {}
    for entry in filter(None, (os.getenv("APURI_USERS") or "").split(",")):
        parts = [x.strip() for x in entry.strip().split(":")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            projects = {p for p in parts[2].split("|") if p} if len(parts) > 2 and parts[2] not in ("", "*") else None
            out[parts[0]] = (parts[1], projects)
    return out


def _credentials() -> dict[str, str]:
    return {u: pw for u, (pw, _) in _users().items()}


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
        return user if user in _credentials() else None
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
    if not _credentials():
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
    users = _credentials()
    ok = user in users and secrets.compare_digest(pw.encode(), users[user].encode())
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
    return {hid: Assistant(s, h, llm) for hid, h in load_all(s.hankkeet_dir, s.data_dir).items()}


def _allowed(request: Request) -> tuple[str | None, list[str]]:
    user = _session_user(request)
    all_ids = list(assistants())
    if user is None:  # kehitystila ilman käyttäjiä
        return None, all_ids
    _, projects = _users()[user]
    return user, [h for h in all_ids if projects is None or h in projects]


class Ask(BaseModel):
    question: str = Field(min_length=5, max_length=20000)
    hanke: str
    mode: str = "luonnos"
    own_style: bool = False
    thread: str | None = Field(default=None, max_length=40000)  # aiempi keskustelu jatkokysymykselle


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status(request: Request) -> dict:
    user, allowed = _allowed(request)
    a = assistants()
    s = get_settings()
    return {"provider": s.provider, "drafter": s.drafter_model, "checker": s.checker_model,
            "user": user, "modes": list(MODES),
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
                                                 thread=body.thread)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ehdotusta ei saatu: {_explain(e)}") from None
    return result.to_dict(restore=ps)


def _explain(e: Exception) -> str:
    """AWS:n virheviesti näytetään (se ei sisällä kysymyksen tekstiä), muut vain tyyppinä."""
    resp = getattr(e, "response", None)
    if isinstance(resp, dict) and "Error" in resp:
        err = resp["Error"]
        code, msg = err.get("Code", type(e).__name__), err.get("Message", "")
        hint = {
            "AccessDeniedException": "Tarkista IAM-oikeudet (bedrock:InvokeModel) ja mallin käyttöoikeus Bedrockissa.",
            "ValidationException": "Tarkista mallitunniste (APURI_DRAFTER_MODEL / APURI_CHECKER_MODEL) ja AWS_REGION.",
            "ResourceNotFoundException": "Mallia ei löydy tältä alueelta. Tarkista mallitunniste ja AWS_REGION.",
            "ThrottlingException": "Bedrockin käyttöraja täyttyi. Yritä hetken päästä uudelleen.",
        }.get(code, "")
        model = get_settings()
        return f"{code}: {msg[:400]} (luonnostelija {model.drafter_model}, tarkastaja {model.checker_model}, alue {model.aws_region}) {hint}".strip()
    return type(e).__name__
