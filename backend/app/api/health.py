from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    try:
        app_version = version("mira-backend")
    except PackageNotFoundError:
        app_version = "0.1.0"
    return {"ok": True, "version": app_version}

