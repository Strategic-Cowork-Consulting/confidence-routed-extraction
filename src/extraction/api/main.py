from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extraction import db
from extraction.api.routers import extraction as extraction_router
from extraction.core.pipeline import (
    AllExtractionsFailed,
    ClaudeRateLimitError,
    ClaudeTimeoutError,
    FileTooLargeError,
    NoDocumentsError,
    UnsupportedFormatError,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    yield


app = FastAPI(title="Confidence-Routed Extraction", lifespan=lifespan)
app.include_router(extraction_router.router)


@app.exception_handler(UnsupportedFormatError)
async def _unsupported_format(request: Request, exc: UnsupportedFormatError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "unsupported_format", "detail": "Supported: pdf, png, jpg, txt"},
    )


@app.exception_handler(FileTooLargeError)
async def _file_too_large(request: Request, exc: FileTooLargeError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "file_too_large", "detail": "Max 10MB per document"},
    )


@app.exception_handler(NoDocumentsError)
async def _no_documents(request: Request, exc: NoDocumentsError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "no_documents", "detail": "At least one document is required"},
    )


@app.exception_handler(AllExtractionsFailed)
async def _all_failed(request: Request, exc: AllExtractionsFailed) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "all_extractions_failed", "detail": str(exc)},
    )


@app.exception_handler(ClaudeRateLimitError)
async def _rate_limited(request: Request, exc: ClaudeRateLimitError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "rate_limited"},
        headers={"Retry-After": "60"},
    )


@app.exception_handler(ClaudeTimeoutError)
async def _timeout(request: Request, exc: ClaudeTimeoutError) -> JSONResponse:
    return JSONResponse(
        status_code=504,
        content={"error": "extraction_timeout"},
    )
