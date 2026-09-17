"""Configurable skill workflows for the Codex agent."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from slop_code.execution.compose import WorkflowComposeConfig

_NAME_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_SKILL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WorkflowNode(BaseModel):
    """One skill invocation and its ordered prompt arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    arguments: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _SKILL_RE.fullmatch(value):
            raise ValueError("skill names must be lowercase kebab-case")
        return value

    @field_validator("arguments")
    @classmethod
    def _validate_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not argument.strip() for argument in value):
            raise ValueError("workflow arguments must not be empty")
        return value

    @property
    def needs_change_id(self) -> bool:
        return any("{change_id}" in argument for argument in self.arguments)


class WorkflowConfig(BaseModel):
    """Declarative setup and independently executed skill nodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    version: str
    install_commands: tuple[tuple[str, ...], ...] = ()
    init_commands: tuple[tuple[str, ...], ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    env_from_host: tuple[str, ...] = ()
    changes_dir: Path = Path("openspec/changes")
    compose: WorkflowComposeConfig | None = None
    skills: tuple[WorkflowNode, ...]

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        if not _NAME_RE.fullmatch(value):
            raise ValueError(
                "workflow type must contain lowercase letters, numbers, dots, "
                "or hyphens"
            )
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workflow version must not be empty")
        return value

    @field_validator("changes_dir")
    @classmethod
    def _validate_changes_dir(cls, value: Path) -> Path:
        if value.is_absolute() or value == Path() or ".." in value.parts:
            raise ValueError(
                "workflow changes_dir must be a non-empty relative path "
                "within the workspace"
            )
        return value

    @field_validator("install_commands", "init_commands")
    @classmethod
    def _validate_commands(
        cls,
        value: tuple[tuple[str, ...], ...],
    ) -> tuple[tuple[str, ...], ...]:
        invalid = any(
            not command or any(not argument for argument in command)
            for command in value
        )
        if invalid:
            raise ValueError("commands must contain non-empty arguments")
        return value

    @field_validator("env_from_host")
    @classmethod
    def _validate_env_from_host(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _ENV_NAME_RE.fullmatch(name) for name in value):
            raise ValueError(
                "host environment variable names must be valid identifiers"
            )
        if len(value) != len(set(value)):
            raise ValueError(
                "host environment variable names must be unique"
            )
        return value

    @model_validator(mode="after")
    def _validate_environment_sources(self) -> WorkflowConfig:
        overlap = sorted(set(self.env) & set(self.env_from_host))
        if overlap:
            names = ", ".join(overlap)
            raise ValueError(
                "workflow environment variables cannot be configured in both "
                f"env and env_from_host: {names}"
            )
        return self

    @field_validator("skills")
    @classmethod
    def _validate_skills(
        cls,
        value: tuple[WorkflowNode, ...],
    ) -> tuple[WorkflowNode, ...]:
        if not value:
            raise ValueError("a workflow must configure at least one skill")
        names = [node.name for node in value]
        if len(names) != len(set(names)):
            raise ValueError("workflow skill names must be unique")
        return value

    def build_prompt(
        self,
        node: WorkflowNode,
        task: str,
        change_id: str | None = None,
    ) -> str:
        """Build one independent, unattended Codex node prompt."""
        sections = [f"${node.name}"]
        for argument in node.arguments:
            if "{change_id}" in argument and change_id is None:
                raise ValueError("change_id is required for this workflow node")
            rendered = argument.replace("{task}", task)
            if change_id is not None:
                rendered = rendered.replace("{change_id}", change_id)
            sections.append(rendered)
        sections.append(
            """This is unattended. Complete only this workflow node; do not invoke or
execute later workflow nodes. Never ask the user a question, request
confirmation, or wait for input. If a skill suggests doing so, make the
smallest reasonable decision from the task, repository, and existing workflow
artifacts, then finish this node."""
        )
        return "\n\n".join(sections) + "\n"

    def required_skill_paths(self, workspace: Path) -> tuple[Path, ...]:
        """Return skill files that initialization must make available."""
        return tuple(
            workspace / ".codex" / "skills" / node.name / "SKILL.md"
            for node in self.skills
        )
