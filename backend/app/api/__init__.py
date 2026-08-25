from fastapi import APIRouter

from . import apps, auth, graph_layout, health, nlcompile, prompt_assistant, runs, settings, uploads, wiki

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(apps.router)
router.include_router(settings.router)
router.include_router(uploads.router)
router.include_router(graph_layout.router)
router.include_router(nlcompile.router)
router.include_router(prompt_assistant.router)
router.include_router(runs.router)
router.include_router(wiki.router)
