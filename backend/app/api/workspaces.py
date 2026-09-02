from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas import (
    WorkspaceCreateIn,
    WorkspaceEventOut,
    WorkspaceFilePreviewOut,
    WorkspaceFilesOut,
    WorkspaceGitConfigIn,
    WorkspaceGitConfigOut,
    WorkspaceOut,
    WorkspacePatchIn,
    WorkspaceSessionCreateIn,
    WorkspaceSessionOut,
    WorkspaceSessionPatchIn,
    WorkspaceSessionActionIn,
    WorkspaceGoalIn,
    WorkspaceTurnCreateIn,
    WorkspaceTurnOut,
    WorkspaceTurnResumeIn,
    WorkspaceWorkflowProposalCreateIn,
    WorkspaceWorkflowProposalOut,
    WorkspaceWorkflowRunOut,
)
from app.services.workspaces import (
    confirm_workflow_proposal,
    create_workflow_proposal,
    create_workspace,
    create_workspace_session,
    create_workspace_turn,
    delete_workspace,
    delete_workspace_session,
    get_owned_workspace_or_404,
    get_owned_workspace_session_or_404,
    get_owned_workspace_turn_or_404,
    get_workspace,
    get_workflow_proposal_or_404,
    list_workflow_proposals,
    list_workspace_events,
    list_workspace_files,
    list_workspace_sessions,
    list_workspaces,
    patch_workspace,
    patch_workspace_session,
    prepare_workspace_wiki_copy,
    publish_workspace_wiki_copy,
    preview_workspace_file,
    reject_workflow_proposal,
    retry_workspace_wiki_merge,
    resolve_workspace_download,
    save_workspace_git_config,
    upload_workspace_files,
    workspace_git_config_out,
    workspace_turn_out,
    build_workspace_decision_result,
)
from app.services.workspace_runtime import (
    cancel_workspace_turn,
    resume_workspace_turn,
    run_workspace_git_operation,
    schedule_workspace_turn,
    start_workspace_runtime,
    run_workspace_session_action,
    rename_workspace_thread,
    get_workspace_goal,
    set_workspace_goal,
    clear_workspace_goal,
    list_workspace_processes,
    stop_workspace_process,
    clean_workspace_processes,
    get_workspace_runtime_status,
    delete_workspace_thread,
)
from app.services.workspace_workflows import list_workspace_workflow_runs


router = APIRouter(tags=["workspaces"])


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def get_workspaces(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await list_workspaces(db, user.id)


@router.post("/workspaces", response_model=WorkspaceOut)
async def post_workspace(
    payload: WorkspaceCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    created = await create_workspace(
        db,
        user.id,
        name=payload.name,
        description=payload.description,
        source=payload.source.model_dump(mode="json"),
    )
    workspace = await get_owned_workspace_or_404(db, created.id, user.id)
    if payload.source.kind == "git":
        cloned = await run_workspace_git_operation(db, workspace, operation="clone")
        if cloned.status != "success":
            await delete_workspace(db, workspace.id, user.id)
            raise HTTPException(status_code=400, detail=cloned.error or "Git clone 失败")
    try:
        await start_workspace_runtime(db, workspace)
    except Exception:
        pass
    return await get_workspace(db, workspace.id, user.id)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace_endpoint(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await get_workspace(db, workspace_id, user.id)


@router.get("/workspaces/{workspace_id}/runtime")
async def get_workspace_runtime_endpoint(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    workspace = await get_owned_workspace_or_404(db, workspace_id, user.id)
    return await get_workspace_runtime_status(workspace)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def patch_workspace_endpoint(
    workspace_id: str,
    payload: WorkspacePatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await patch_workspace(
        db, workspace_id, user.id, name=payload.name, description=payload.description
    )


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace_endpoint(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await delete_workspace(db, workspace_id, user.id)


@router.get("/workspaces/{workspace_id}/sessions", response_model=list[WorkspaceSessionOut])
async def get_workspace_sessions(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await list_workspace_sessions(db, workspace_id, user.id)


@router.post("/workspaces/{workspace_id}/sessions", response_model=WorkspaceSessionOut)
async def post_workspace_session(
    workspace_id: str,
    payload: WorkspaceSessionCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_workspace_session(db, workspace_id, user.id, payload.title)


@router.patch("/workspace-sessions/{session_id}", response_model=WorkspaceSessionOut)
async def patch_workspace_session_endpoint(
    session_id: str,
    payload: WorkspaceSessionPatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    await rename_workspace_thread(db, workspace, session, payload.title)
    return await patch_workspace_session(db, session_id, user.id, payload.title)


@router.delete("/workspace-sessions/{session_id}", status_code=204)
async def delete_workspace_session_endpoint(
    session_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    await delete_workspace_thread(db, workspace, session)
    await delete_workspace_session(db, session_id, user.id)


@router.get("/workspace-sessions/{session_id}/events", response_model=list[WorkspaceEventOut])
async def get_workspace_session_events(
    session_id: str,
    after_id: int | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_workspace_events(db, session_id, user.id, after_id=after_id)


@router.post("/workspace-sessions/{session_id}/turns", response_model=WorkspaceTurnOut)
async def post_workspace_turn(
    session_id: str,
    payload: WorkspaceTurnCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    turn = await create_workspace_turn(
        db,
        workspace_id=workspace.id,
        session_id=session.id,
        prompt=payload.text,
        model=payload.model,
        reasoning_effort=payload.reasoning_effort,
        attachments=[item.model_dump(mode="json") for item in payload.attachments],
    )
    await db.commit()
    schedule_workspace_turn(turn.id)
    return workspace_turn_out(turn)


@router.post("/workspace-turns/{turn_id}/interrupt", status_code=204)
async def post_workspace_turn_interrupt(
    turn_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    _workspace, _session, turn = await get_owned_workspace_turn_or_404(db, turn_id, user.id)
    if turn.status not in {"pending", "running", "waiting"}:
        raise HTTPException(status_code=409, detail="Workspace turn 已结束")
    if not await cancel_workspace_turn(turn.id):
        raise HTTPException(status_code=409, detail="Workspace turn 当前不在执行")


@router.post("/workspace-turns/{turn_id}/resume", status_code=204)
async def post_workspace_turn_resume(
    turn_id: str,
    payload: WorkspaceTurnResumeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    _workspace, _session, turn = await get_owned_workspace_turn_or_404(db, turn_id, user.id)
    result = await build_workspace_decision_result(
        db,
        turn=turn,
        owner_id=user.id,
        request_id=payload.request_id,
        answers=payload.answers,
        text=payload.text,
        attachments=payload.attachments,
    )
    if not await resume_workspace_turn(turn.id, payload.request_id, result):
        raise HTTPException(status_code=409, detail="提问会话已中断")


@router.post("/workspace-sessions/{session_id}/actions")
async def post_workspace_session_action(
    session_id: str,
    payload: WorkspaceSessionActionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    return await run_workspace_session_action(db, workspace, session, payload.action, payload.instructions)


@router.get("/workspace-sessions/{session_id}/goal")
async def get_workspace_session_goal(session_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    return await get_workspace_goal(db, workspace, session)


@router.put("/workspace-sessions/{session_id}/goal")
async def put_workspace_session_goal(session_id: str, payload: WorkspaceGoalIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    return await set_workspace_goal(db, workspace, session, objective=payload.objective, status=payload.status, token_budget=payload.token_budget)


@router.delete("/workspace-sessions/{session_id}/goal", status_code=204)
async def delete_workspace_session_goal(session_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    await clear_workspace_goal(db, workspace, session)


@router.get("/workspace-sessions/{session_id}/processes")
async def get_workspace_session_processes(session_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    return await list_workspace_processes(db, workspace, session)


@router.post("/workspace-sessions/{session_id}/processes/{process_id}/stop", status_code=204)
async def post_workspace_session_process_stop(session_id: str, process_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    await stop_workspace_process(db, workspace, session, process_id)


@router.post("/workspace-sessions/{session_id}/processes/clean", status_code=204)
async def post_workspace_session_process_clean(session_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    workspace, session = await get_owned_workspace_session_or_404(db, session_id, user.id)
    await clean_workspace_processes(db, workspace, session)


@router.get("/workspaces/{workspace_id}/files", response_model=WorkspaceFilesOut)
async def get_workspace_files(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await list_workspace_files(db, workspace_id, user.id)


@router.get("/workspaces/{workspace_id}/files/preview", response_model=WorkspaceFilePreviewOut)
async def get_workspace_file_preview(
    workspace_id: str,
    path: str = Query(min_length=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await preview_workspace_file(db, workspace_id, user.id, path)


@router.get("/workspaces/{workspace_id}/files/download")
async def get_workspace_file_download(
    workspace_id: str,
    path: str = Query(min_length=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await resolve_workspace_download(db, workspace_id, user.id, path)
    return FileResponse(target, filename=target.name)


@router.post("/workspaces/{workspace_id}/files", response_model=WorkspaceFilesOut)
async def post_workspace_files(
    workspace_id: str,
    files: list[UploadFile] = File(...),
    target_path: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await upload_workspace_files(db, workspace_id, user.id, files, target_path)


@router.get("/workspaces/{workspace_id}/git-config", response_model=WorkspaceGitConfigOut)
async def get_workspace_git_config(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await workspace_git_config_out(db, workspace_id, user.id)


@router.put("/workspaces/{workspace_id}/git-config", response_model=WorkspaceGitConfigOut)
async def put_workspace_git_config(
    workspace_id: str,
    payload: WorkspaceGitConfigIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workspace = await get_owned_workspace_or_404(db, workspace_id, user.id)
    return await save_workspace_git_config(
        db,
        workspace,
        repository_url=payload.repository_url,
        default_branch=payload.default_branch,
        access_token=payload.access_token,
    )


@router.post("/workspaces/{workspace_id}/git/pull")
async def post_workspace_git_pull(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    workspace = await get_owned_workspace_or_404(db, workspace_id, user.id)
    result = await run_workspace_git_operation(db, workspace, operation="pull")
    if result.status != "success":
        raise HTTPException(status_code=409, detail=result.error or "Git pull 失败")
    return {"status": result.status, "duration_ms": result.duration_ms}


@router.post("/workspaces/{workspace_id}/git/push")
async def post_workspace_git_push(
    workspace_id: str,
    confirmed: bool = Body(default=False, embed=True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not confirmed:
        raise HTTPException(status_code=400, detail="Git push 必须由用户明确确认")
    workspace = await get_owned_workspace_or_404(db, workspace_id, user.id)
    result = await run_workspace_git_operation(db, workspace, operation="push", confirmed=True)
    if result.status != "success":
        raise HTTPException(status_code=409, detail=result.error or "Git push 失败")
    return {"status": result.status, "duration_ms": result.duration_ms}


@router.post("/workspaces/{workspace_id}/wiki/sync")
async def post_workspace_wiki_sync(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    workspace = await get_owned_workspace_or_404(db, workspace_id, user.id)
    return await prepare_workspace_wiki_copy(db, workspace)


@router.post("/workspaces/{workspace_id}/wiki/retry")
async def post_workspace_wiki_retry(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    workspace = await get_owned_workspace_or_404(db, workspace_id, user.id)
    return await retry_workspace_wiki_merge(db, workspace)


@router.get(
    "/workspaces/{workspace_id}/workflow-proposals",
    response_model=list[WorkspaceWorkflowProposalOut],
)
async def get_workspace_workflow_proposals(
    workspace_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await list_workflow_proposals(db, workspace_id, user.id)


@router.post(
    "/workspaces/{workspace_id}/workflow-proposals",
    response_model=WorkspaceWorkflowProposalOut,
)
async def post_workspace_workflow_proposal(
    workspace_id: str,
    payload: WorkspaceWorkflowProposalCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_workflow_proposal(
        db,
        workspace_id,
        user.id,
        session_id=payload.session_id,
        kind=payload.kind,
        app_id=payload.app_id,
        name=payload.name,
        description=payload.description,
        base_graph_sha256=payload.base_graph_sha256,
        graph=payload.graph,
    )


@router.get(
    "/workspaces/{workspace_id}/workflow-proposals/{proposal_id}",
    response_model=WorkspaceWorkflowProposalOut,
)
async def get_workspace_workflow_proposal(
    workspace_id: str,
    proposal_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.workspaces import _proposal_out

    return _proposal_out(await get_workflow_proposal_or_404(db, workspace_id, proposal_id, user.id))


@router.post(
    "/workspaces/{workspace_id}/workflow-proposals/{proposal_id}/confirm",
    response_model=WorkspaceWorkflowProposalOut,
)
async def post_confirm_workspace_workflow_proposal(
    workspace_id: str,
    proposal_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await confirm_workflow_proposal(db, workspace_id, proposal_id, user.id)


@router.post(
    "/workspaces/{workspace_id}/workflow-proposals/{proposal_id}/reject",
    response_model=WorkspaceWorkflowProposalOut,
)
async def post_reject_workspace_workflow_proposal(
    workspace_id: str,
    proposal_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await reject_workflow_proposal(db, workspace_id, proposal_id, user.id)


@router.get("/workspaces/{workspace_id}/workflow-runs", response_model=list[WorkspaceWorkflowRunOut])
async def get_workspace_workflow_runs(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_workspace_or_404(db, workspace_id, user.id)
    return await list_workspace_workflow_runs(db, workspace_id, user.id)
