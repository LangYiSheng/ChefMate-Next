"""Validated request bodies for settings and conversation endpoints."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from chefmate_workflow import UISnapshot


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str
    section: str
    values: dict


class SessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value):
        if not isinstance(value, str):
            raise ValueError("请输入对话名称。")
        return value.strip()

    @model_validator(mode="after")
    def has_change(self):
        if self.title is None and self.pinned is None:
            raise ValueError("请选择要修改的对话信息。")
        return self


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(default="", max_length=8000)
    images: list[str] = Field(default_factory=list, max_length=12)
    retry: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    ui_snapshot: UISnapshot | None = None

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def content_present(self):
        if not self.message and not self.images:
            raise ValueError("请输入消息或上传图片")
        if len(set(self.images)) != len(self.images):
            raise ValueError("图片不能重复")
        return self
