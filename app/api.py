"""HTTP API over the RAG pipeline. The only module that imports fastapi.

    uvicorn app.api:app --port 8000

Endpoints (contract in CLAUDE.md, "Web frontend"):
    POST /ask          JSON answer from chat.ask()
    POST /ask/stream   Server-Sent Events: `token` events, then one `done` (or `error`)
    GET  /meta         model, last sync time, chunk count, coverage, disclaimer
    GET  /health       liveness check

This module does no retrieval, prompting, or model work; it only translates
between HTTP and app.chat. Request and answer content is never logged.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app import chat, llm, store
from ingest.config import MANIFEST_FILE

load_dotenv()
log = logging.getLogger(__name__)

DEFAULT_FRONTEND_ORIGIN = "http://localhost:3000"


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    history: list[Message] = Field(default_factory=list, max_length=20)


class Source(BaseModel):
    """One retrieved excerpt's metadata (mirrors the stored chunk fields)."""

    chunk_id: str
    citation: str
    title: str
    url: str
    source_updated: str | None
    effective_date: str | None
    doc_type: str
    scope: list[str]
    affected_by: list[str] = []   # court orders or alerts that may suspend or change this policy


class AskResponse(BaseModel):
    text: str
    sources: list[Source]
    model: str
    removed_links: list[str]


class Meta(BaseModel):
    model: str
    last_synced: str | None
    chunk_count: int
    scope: str
    disclaimer: str


def _source(chunk: dict[str, Any]) -> Source:
    return Source(
        chunk_id=chunk["chunk_id"],
        citation=chunk["citation"],
        title=chunk["title"],
        url=chunk["url"],
        source_updated=chunk.get("source_updated") or None,
        effective_date=chunk.get("effective_date") or None,
        doc_type=chunk["doc_type"],
        scope=list(chunk.get("scope", [])),
        affected_by=list(chunk.get("affected_by") or []),
    )


def _response(answer: chat.Answer) -> AskResponse:
    return AskResponse(
        text=answer.text,
        sources=[_source(c) for c in answer.sources],
        model=answer.model,
        removed_links=answer.removed_links,
    )


def _history(req: AskRequest) -> list[dict[str, str]]:
    return [m.model_dump() for m in req.history]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await run_in_threadpool(chat.warm_up)
    yield


app = FastAPI(title="Visa Policy Chat API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGIN)],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/meta", response_model=Meta)
def meta() -> Meta:
    last_synced = None
    if MANIFEST_FILE.exists():
        last_synced = json.loads(MANIFEST_FILE.read_text(encoding="utf-8")).get("last_successful_sync")
    return Meta(
        model=llm.model_name(),
        last_synced=last_synced,
        chunk_count=store.stats()["count"],
        scope=chat.COVERAGE,
        disclaimer=chat.DISCLAIMER,
    )


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    try:
        return _response(chat.ask(req.question, _history(req)))
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ask/stream")
async def ask_stream(req: AskRequest) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            async for kind, value in iterate_in_threadpool(chat.ask_stream(req.question, _history(req))):
                if kind == "token":
                    yield {"event": "token", "data": json.dumps({"text": value})}
                else:
                    yield {"event": "done", "data": _response(value).model_dump_json()}
        except llm.LLMError as exc:
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(events())
