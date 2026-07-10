from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DecisionRequestContext(BaseModel):
    title: str
    summary: str

    @field_validator("title", "summary")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @field_validator("title")
    @classmethod
    def _validate_title_length(cls, value: str) -> str:
        if len(value) > 80:
            raise ValueError("长度超过 80")
        return value

    @field_validator("summary")
    @classmethod
    def _validate_summary_length(cls, value: str) -> str:
        if len(value) > 240:
            raise ValueError("长度超过 240")
        return value


class DecisionOption(BaseModel):
    label: str
    description: str
    recommended: bool = False

    @field_validator("label", "description")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()


class DecisionGroup(BaseModel):
    id: str
    label: str
    type: Literal["single", "multi"]
    options: list[DecisionOption]
    placeholder: str | None = None

    @field_validator("id", "label", "placeholder")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("options")
    @classmethod
    def _strip_options(cls, value: list[DecisionOption]) -> list[DecisionOption]:
        return value


class DecisionAnswer(BaseModel):
    group_id: str
    selected: list[str] = Field(default_factory=list)
