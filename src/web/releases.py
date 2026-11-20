"""Admin-only release information and manual refresh action."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from web.auth import require_login_page, verify_csrf

router = APIRouter()


@router.get("/config/releases")
async def release_status(request: Request, _username: str = Depends(require_login_page)):
    settings = await request.app.state.settings_store.get()
    return request.app.state.templates.TemplateResponse(request, "releases.html", {
        "release": request.app.state.release_check,
        "using_default_credentials": settings.is_using_default_credentials(),
    })


@router.post("/config/releases")
async def check_release(request: Request, _username: str = Depends(require_login_page)):
    form = await request.form()
    await verify_csrf(request, str(form.get("csrf_token", "")))
    request.app.state.release_check.start_check()
    return RedirectResponse("/config/releases", status_code=303)
