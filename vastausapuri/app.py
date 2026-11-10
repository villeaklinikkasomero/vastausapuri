"""Vastaajan selainkäyttöliittymä. Käynnistys: uvicorn vastausapuri.app:app"""
from __future__ import annotations

import base64
import os
import secrets
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .catalog import Index, load_catalog
from .config import get_settings
from .llm import get_llm
from .pipeline import Assistant

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Neuvontapalvelun vastausapuri", docs_url=None, redoc_url=None)


def _credentials() -> dict[str, str]:
    """Käyttäjät muodossa APURI_USERS="nimi:salasana,nimi2:salasana2"."""
    out = {}
    for pair in filter(None, (os.getenv("APURI_USERS") or "").split(",")):
        user, _, pw = pair.strip().partition(":")
        if user and pw:
            out[user] = pw
    return out


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)
    users = _credentials()
    if not users:
        # Bedrock-tilassa palvelu ei ole koskaan auki ilman kirjautumista
        if get_settings().provider != "mock":
            return PlainTextResponse("APURI_USERS puuttuu: palvelu on suljettu.", status_code=503)
    else:
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = user in users and secrets.compare_digest(pw, users[user])
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Vastausapuri"'})
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@lru_cache
def assistant() -> Assistant:
    s = get_settings()
    return Assistant(s, Index(load_catalog(s.catalog_path)), get_llm(s))


class Ask(BaseModel):
    question: str = Field(min_length=5, max_length=20000)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status() -> dict:
    a = assistant()
    return {"provider": a.s.provider, "drafter": a.s.drafter_model,
            "checker": a.s.checker_model, "catalog_items": len(a.index.items)}


@app.post("/api/suggest")
def suggest(body: Ask) -> dict:
    # Kysymystä tai vastausta ei lokiteta eikä tallenneta.
    try:
        result, ps = assistant().run(body.question)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ehdotusta ei saatu: {type(e).__name__}") from None
    return result.to_dict(restore=ps)
