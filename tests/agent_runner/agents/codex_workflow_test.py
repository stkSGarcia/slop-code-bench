"""Tests for declarative Codex skill workflows."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from slop_code.agent_runner.agents.codex.workflow import WorkflowConfig


def _workflow(**overrides: object) -> WorkflowConfig:
    values: dict[str, object] = {
        "type": "openspec",
        "version": "1.7.0",
        "install_commands": [
            ["npm", "install", "-g", "@fission-ai/openspec@1.7.0"]
        ],
        "init_commands": [["openspec", "init", ".", "--force"]],
        "env": {"CI": "true"},
        "skills": [
            {
                "name": "openspec-propose",
                "arguments": ["{task}", "Create every proposal artifact."],
            },
            {"name": "openspec-apply-change"},
            {
                "name": "openspec-archive-change",
                "arguments": ["Archive this change:", "{change_id}"],
            },
        ],
    }
    values.update(overrides)
    return WorkflowConfig.model_validate(values)


def test_workflow_setup_is_fully_declarative() -> None:
    workflow = _workflow()

    assert workflow.install_commands == (
        ("npm", "install", "-g", "@fission-ai/openspec@1.7.0"),
    )
    assert workflow.init_commands == (("openspec", "init", ".", "--force"),)
    assert workflow.env == {"CI": "true"}
    assert workflow.env_from_host == ()
    assert workflow.changes_dir == Path("openspec/changes")


def test_workflow_can_configure_its_changes_directory() -> None:
    workflow = _workflow(
        changes_dir="synergyspec-selfevolving/changes",
    )

    assert workflow.changes_dir == Path(
        "synergyspec-selfevolving/changes"
    )


def test_host_environment_variable_names_are_declarative() -> None:
    workflow = _workflow(
        env_from_host=["EMBEDDING_API_KEY", "NEO4J_PASSWORD"]
    )

    assert workflow.env_from_host == (
        "EMBEDDING_API_KEY",
        "NEO4J_PASSWORD",
    )
    assert "EMBEDDING_API_KEY" not in workflow.env


def test_nodes_only_receive_their_configured_argument() -> None:
    workflow = _workflow()
    propose, apply, archive = workflow.skills
    checkpoint = "Checkpoint 2 requires a searchable document index."

    propose_prompt = workflow.build_prompt(propose, checkpoint)
    apply_prompt = workflow.build_prompt(apply, checkpoint)
    archive_prompt = workflow.build_prompt(
        archive,
        checkpoint,
        "change-two",
    )

    assert propose_prompt.startswith("$openspec-propose\n")
    assert checkpoint in propose_prompt
    assert propose_prompt.index(checkpoint) < propose_prompt.index(
        "Create every proposal artifact."
    )
    assert apply_prompt.startswith("$openspec-apply-change\n")
    assert checkpoint not in apply_prompt
    assert archive_prompt.startswith("$openspec-archive-change\n")
    assert "change-two" in archive_prompt
    assert archive_prompt.index("Archive this change:") < archive_prompt.index(
        "change-two"
    )
    assert checkpoint not in archive_prompt
    assert all(
        "Never ask the user a question" in prompt
        for prompt in (propose_prompt, apply_prompt, archive_prompt)
    )
    assert all(
        "Complete only this workflow node" in prompt
        for prompt in (propose_prompt, apply_prompt, archive_prompt)
    )


def test_change_id_argument_is_required() -> None:
    workflow = _workflow()

    with pytest.raises(ValueError, match="change_id is required"):
        workflow.build_prompt(workflow.skills[-1], "task")


def test_required_skill_paths_follow_configuration() -> None:
    paths = _workflow().required_skill_paths(Path("/repo"))

    assert paths == (
        Path("/repo/.codex/skills/openspec-propose/SKILL.md"),
        Path("/repo/.codex/skills/openspec-apply-change/SKILL.md"),
        Path("/repo/.codex/skills/openspec-archive-change/SKILL.md"),
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"skills": []}, "at least one skill"),
        (
            {
                "skills": [
                    {"name": "same-skill"},
                    {"name": "same-skill"},
                ]
            },
            "must be unique",
        ),
        (
            {"init_commands": [[]]},
            "commands must contain non-empty arguments",
        ),
        (
            {"install_commands": [["npm", ""]]},
            "commands must contain non-empty arguments",
        ),
        (
            {"skills": [{"name": "valid-skill", "arguments": [""]}]},
            "workflow arguments must not be empty",
        ),
        (
            {"skills": [{"name": "Bad Name"}]},
            "lowercase kebab-case",
        ),
        (
            {"changes_dir": "/workspace/changes"},
            "changes_dir must be a non-empty relative path",
        ),
        (
            {"changes_dir": "../changes"},
            "changes_dir must be a non-empty relative path",
        ),
        (
            {"env_from_host": ["INVALID-NAME"]},
            "must be valid identifiers",
        ),
        (
            {"env_from_host": ["API_KEY", "API_KEY"]},
            "must be unique",
        ),
        (
            {
                "env": {"API_KEY": "literal"},
                "env_from_host": ["API_KEY"],
            },
            "cannot be configured in both",
        ),
        (
            {
                "resource_pool": {
                    "env_var": "ARTNET_NEO4J_URI",
                    "values": ["bolt://neo4j:7687"],
                },
            },
            "Extra inputs are not permitted",
        ),
    ],
)
def test_invalid_workflow_configuration_is_rejected(
    override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _workflow(**override)
