from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.decision import DecisionAnswer
from app.schemas.runs import RunAttachmentRef


class AuthIn(BaseModel):
    username: str
    password: str


class AppCreateIn(BaseModel):
    name: str | None = None
    description: str | None = None


class AppPatchIn(BaseModel):
    name: str | None = None
    description: str | None = None
    cover: str | None = None
    status: Literal["draft", "published"] | None = None
    visibility: Literal["public", "private"] | None = None
    market_access: Literal["cloneable", "run_only"] | None = None
    graph: dict[str, Any] | None = None


class AppPublishIn(BaseModel):
    visibility: Literal["public", "private"] | None = None
    market_access: Literal["cloneable", "run_only"] | None = None


class VersionCreateIn(BaseModel):
    label: str | None = None


class NlCompileIn(BaseModel):
    app_id: str
    instruction: str
    current_graph: dict[str, Any]
    compile_id: str | None = None
    attachments: list[RunAttachmentRef] = Field(default_factory=list)


class NlCompileResumeIn(BaseModel):
    answers: list[DecisionAnswer]
    text: str | None = None
    attachments: list[RunAttachmentRef] = Field(default_factory=list)


class NlCompileRefineIn(BaseModel):
    feedback: str


class GraphLayoutBeautifyIn(BaseModel):
    app_id: str
    graph: dict[str, Any]
    node_sizes: dict[str, dict[str, float]] = Field(default_factory=dict)


class WorkflowLintIn(BaseModel):
    graph: dict[str, Any] | None = None


class PromptAssistantGenerateIn(BaseModel):
    app_id: str
    generation_id: str | None = None
    graph: dict[str, Any]
    node_id: str
    user_request: str = ""
    model: str | None = None
    reasoning_effort: str | None = None


class PromptAssistantResumeIn(BaseModel):
    answers: list[DecisionAnswer]
    text: str | None = None
    attachments: list[RunAttachmentRef] = Field(default_factory=list)
