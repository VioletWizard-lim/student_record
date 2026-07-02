from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import admin, auth, generation
from app.config import settings
from app.exceptions import RedirectException

app = FastAPI(title="생기부 도우미")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=not settings.debug,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.exception_handler(RedirectException)
async def redirect_exception_handler(request: Request, exc: RedirectException):
    return RedirectResponse(exc.url, status_code=303)


app.include_router(auth.router)
app.include_router(generation.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
