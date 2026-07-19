"""FastAPI application factory, middleware, and auxiliary routes."""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.api import profiles, search, threads, users
from app.config import (
    IFRAMELY_BACKEND,
    LOG_REQUESTS,
    ROOT_DIR,
    TURNSTILE_SECRET_KEY,
    TURNSTILE_SITE_KEY,
)
from app.security.ratelimit import SEARCH_PATHS, client_ip, get_rate_limiter
from app.security.turnstile import verify_turnstile

logger = logging.getLogger("archive.api")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        if LOG_REQUESTS and request.url.path.startswith(("/api/", "/iframely")):
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s %s %s %.1fms ip=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                client_ip(request),
            )
        return response


class BotProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        # Honeypot: hidden query params that real UI never sends
        honeypot = request.query_params.get("website") or request.query_params.get("hp_field")
        limiter = get_rate_limiter()
        ip = client_ip(request)
        if honeypot:
            limiter.mark_honeypot(ip)
            return JSONResponse(
                {"error": "forbidden"},
                status_code=403,
                headers={"Retry-After": "300"},
            )

        decision = limiter.check(ip, path)
        if not decision.allowed:
            body = {"error": decision.reason or "rate_limit"}
            headers = {}
            if decision.retry_after:
                headers["Retry-After"] = str(decision.retry_after)
            if decision.require_turnstile and TURNSTILE_SECRET_KEY:
                body["error"] = "turnstile_required"
                body["turnstile"] = True
                if TURNSTILE_SITE_KEY:
                    body["site_key"] = TURNSTILE_SITE_KEY
            return JSONResponse(body, status_code=429, headers=headers)

        if (
            decision.require_turnstile
            and TURNSTILE_SECRET_KEY
            and path in SEARCH_PATHS
        ):
            token = request.headers.get("cf-turnstile-response") or request.query_params.get(
                "cf_turnstile_response"
            )
            ok = await verify_turnstile(token, ip)
            if not ok:
                body = {
                    "error": "turnstile_required",
                    "turnstile": True,
                }
                if TURNSTILE_SITE_KEY:
                    body["site_key"] = TURNSTILE_SITE_KEY
                return JSONResponse(body, status_code=429)

        response = await call_next(request)
        if response.status_code >= 400 and path.startswith("/api/"):
            limiter.record_failure(ip)
        return response


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/"):
            if path in {"/api/usergroups", "/api/awards", "/api/smilies", "/api/titles"}:
                response.headers.setdefault(
                    "Cache-Control", "public, max-age=300, stale-while-revalidate=600"
                )
            elif path.startswith(("/api/user", "/api/thread")):
                response.headers.setdefault(
                    "Cache-Control", "public, max-age=60, stale-while-revalidate=120"
                )
            elif path.startswith("/api/search_"):
                response.headers.setdefault(
                    "Cache-Control", "public, max-age=30, stale-while-revalidate=60"
                )
            else:
                response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith(("/static/", "/stylesheets/")):
            if path.endswith((".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff2")):
                response.headers.setdefault(
                    "Cache-Control", "public, max-age=86400, immutable"
                )
        return response


def create_app() -> FastAPI:
    application = FastAPI(
        title="V3rmillion Archive Explorer API",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    application.add_middleware(GZipMiddleware, minimum_size=500)
    application.add_middleware(CacheControlMiddleware)
    application.add_middleware(BotProtectionMiddleware)
    application.add_middleware(RequestLogMiddleware)

    @application.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            body = detail
        else:
            body = {"error": str(detail)}
        return JSONResponse(body, status_code=exc.status_code)

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse({"error": str(exc)}, status_code=500)

    application.include_router(search.router, prefix="/api")
    application.include_router(threads.router, prefix="/api")
    application.include_router(users.router, prefix="/api")
    application.include_router(profiles.router, prefix="/api")

    @application.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @application.get("/api/config")
    def public_config() -> dict:
        return {
            "turnstile_enabled": bool(TURNSTILE_SECRET_KEY and TURNSTILE_SITE_KEY),
            "turnstile_site_key": TURNSTILE_SITE_KEY or None,
        }

    @application.get("/member.php")
    def legacy_member(uid: str = "", action: str = "") -> Response:
        if uid and action == "profile":
            return RedirectResponse(url=f"/static/search.html?uid={uid}", status_code=302)
        raise HTTPException(status_code=404, detail="not found")

    @application.get("/showthread.php")
    def legacy_thread(thread_id: str = "") -> Response:
        if thread_id:
            return RedirectResponse(
                url=f"/static/search.html?id={thread_id}", status_code=302
            )
        raise HTTPException(status_code=404, detail="not found")

    @application.api_route("/iframely", methods=["GET"])
    @application.api_route("/iframely/{path:path}", methods=["GET"])
    async def iframely_proxy(request: Request, path: str = "") -> Response:
        suffix = request.url.path
        target = f"{IFRAMELY_BACKEND}{suffix}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                upstream = await client.get(
                    target,
                    headers={"User-Agent": "Mozilla/5.0 Archive-Proxy"},
                )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
                headers={"Cache-Control": "public, max-age=300"},
            )
        except Exception as exc:
            logger.error("Iframely proxy error: %s", exc)
            return JSONResponse({"error": f"Proxy error: {exc}"}, status_code=500)

    static_dir = ROOT_DIR / "static"
    styles_dir = ROOT_DIR / "stylesheets"
    if static_dir.is_dir():
        application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    if styles_dir.is_dir():
        application.mount(
            "/stylesheets", StaticFiles(directory=str(styles_dir)), name="stylesheets"
        )

    robots_path = ROOT_DIR / "static" / "robots.txt"

    @application.get("/robots.txt")
    def robots_txt() -> Response:
        if robots_path.exists():
            return Response(
                content=robots_path.read_text(encoding="utf-8"),
                media_type="text/plain; charset=utf-8",
            )
        body = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "Disallow: /iframely\n"
        )
        return Response(content=body, media_type="text/plain; charset=utf-8")

    return application


app = create_app()
