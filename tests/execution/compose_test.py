from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from slop_code.execution.compose import WorkflowComposeConfig
from slop_code.execution.docker_runtime.models import DockerConfig
from slop_code.execution.docker_runtime.models import DockerEnvironmentSpec
from slop_code.execution.local_streaming import LocalEnvironmentSpec


@pytest.fixture
def compose_config(tmp_path: Path) -> WorkflowComposeConfig:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    return WorkflowComposeConfig(file=compose_file, wait_timeout=42)


@pytest.fixture
def docker_environment() -> DockerEnvironmentSpec:
    return DockerEnvironmentSpec(
        name="compose-test",
        docker=DockerConfig(
            image="python:3.12",
            binary="custom-docker",
            network="original-network",
        ),
    )


def test_build_images_without_services_is_a_noop(tmp_path: Path) -> None:
    config = WorkflowComposeConfig(file=tmp_path / "missing.yaml")
    with patch("slop_code.execution.compose.subprocess.run") as run:
        config.build_images(LocalEnvironmentSpec(name="local-test"))
    run.assert_not_called()


def test_build_images_builds_only_selected_services(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
) -> None:
    config = WorkflowComposeConfig(
        file=compose_config.file,
        build_services=("artifactnet-cli", "workflow-helper"),
    )
    with patch("slop_code.execution.compose.subprocess.run") as run:
        config.build_images(docker_environment)

    run.assert_called_once_with(
        [
            "custom-docker",
            "compose",
            "--file",
            str(compose_config.file.resolve()),
            "--project-name",
            "scb-workflow-images",
            "build",
            "artifactnet-cli",
            "workflow-helper",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert not (tmp_path / "workflow-compose.json").exists()
    assert docker_environment.docker.network == "original-network"


def test_build_only_workflow_does_not_start_problem_services(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
) -> None:
    config = WorkflowComposeConfig(
        file=compose_config.file,
        build_services=("matrixspec-cli",),
        start_services=False,
    )
    output = tmp_path / "problem-output"
    with patch("slop_code.execution.compose.subprocess.run") as run:
        config.build_images(docker_environment)
        run.assert_called_once()
        assert run.call_args.args[0][-2:] == ["build", "matrixspec-cli"]

        for _ in range(2):
            with config.problem_environment(
                docker_environment, output
            ) as environment:
                assert environment is docker_environment
        run.assert_called_once()

    assert not output.exists()
    assert docker_environment.docker.network == "original-network"


def test_build_images_propagates_build_failure(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
) -> None:
    config = WorkflowComposeConfig(
        file=compose_config.file, build_services=("artifactnet-cli",)
    )
    error = subprocess.CalledProcessError(
        1, ["docker", "compose", "build"], stderr="npm install failed"
    )
    with (
        patch(
            "slop_code.execution.compose.subprocess.run", side_effect=error
        ) as run,
        pytest.raises(RuntimeError, match="npm install failed") as caught,
    ):
        config.build_images(docker_environment)

    assert caught.value.__cause__ is error
    run.assert_called_once()


def test_build_images_rejects_local_environment(
    compose_config: WorkflowComposeConfig,
) -> None:
    config = WorkflowComposeConfig(
        file=compose_config.file, build_services=("artifactnet-cli",)
    )
    with (
        patch("slop_code.execution.compose.subprocess.run") as run,
        pytest.raises(ValueError, match="requires a Docker environment"),
    ):
        config.build_images(LocalEnvironmentSpec(name="local-test"))
    run.assert_not_called()


@pytest.mark.parametrize("existing_directory", [False, True])
def test_build_images_rejects_invalid_file(
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
    *,
    existing_directory: bool,
) -> None:
    compose_file = tmp_path / "compose.yaml"
    if existing_directory:
        compose_file.mkdir()
    config = WorkflowComposeConfig(
        file=compose_file, build_services=("artifactnet-cli",)
    )
    with (
        patch("slop_code.execution.compose.subprocess.run") as run,
        pytest.raises(FileNotFoundError, match="file does not exist"),
    ):
        config.build_images(docker_environment)
    run.assert_not_called()


def test_waits_for_services_and_preserves_shared_environment(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
) -> None:
    output = tmp_path / "problem-output"
    with (
        patch("slop_code.execution.compose.subprocess.run") as run,
        compose_config.problem_environment(
            docker_environment, output
        ) as environment,
    ):
        run.assert_called_once()
        command = run.call_args.args[0]
        project = command[5]
        assert command == [
            "custom-docker",
            "compose",
            "--file",
            str(compose_config.file.resolve()),
            "--project-name",
            project,
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "42",
        ]
        assert run.call_args.kwargs == {
            "check": True,
            "capture_output": True,
            "text": True,
            "timeout": 342,
        }
        assert environment is not docker_environment
        assert environment.docker is not docker_environment.docker
        assert environment.docker.network == f"{project}_default"
        assert docker_environment.docker.network == "original-network"
        assert environment.docker.image == docker_environment.docker.image
        assert json.loads(
            (output / "workflow-compose.json").read_text(encoding="utf-8")
        ) == {
            "project": project,
            "file": str(compose_config.file.resolve()),
            "network": environment.docker.network,
        }

    assert run.call_count == 2
    assert run.call_args.args[0] == [
        *command[:6],
        "down",
        "--remove-orphans",
    ]
    assert "--volumes" not in run.call_args.args[0]
    assert run.call_args.kwargs["timeout"] == 60


def test_project_isolation_and_resume(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
) -> None:
    outputs = [
        tmp_path / "run-one" / "problem-one",
        tmp_path / "run-one" / "problem-two",
        tmp_path / "run-two" / "problem-one",
        tmp_path / "run-one" / "other" / ".." / "problem-one",
    ]
    networks = []
    with patch("slop_code.execution.compose.subprocess.run"):
        for output in outputs:
            with compose_config.problem_environment(
                docker_environment, output
            ) as environment:
                networks.append(environment.docker.network)

    assert len(set(networks[:3])) == 3
    assert networks[0] == networks[3]


def test_removed_metadata_starts_a_fresh_project(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
) -> None:
    with patch("slop_code.execution.compose.subprocess.run"):
        with compose_config.problem_environment(
            docker_environment, tmp_path
        ) as first:
            original_network = first.docker.network
        (tmp_path / "workflow-compose.json").unlink()
        with compose_config.problem_environment(
            docker_environment, tmp_path
        ) as second:
            assert second.docker.network != original_network


@pytest.mark.parametrize(
    "metadata",
    ["{invalid-json", "[]", "{}", '{"project": "another-project"}'],
)
def test_invalid_metadata_is_rejected_before_startup(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
    metadata: str,
) -> None:
    (tmp_path / "workflow-compose.json").write_text(metadata, encoding="utf-8")
    with (
        patch("slop_code.execution.compose.subprocess.run") as run,
        pytest.raises(ValueError, match="Invalid workflow Docker Compose"),
        compose_config.problem_environment(docker_environment, tmp_path),
    ):
        pytest.fail("Invalid metadata must not start an unrelated project")
    run.assert_not_called()


@pytest.mark.parametrize(
    "startup_error",
    [
        subprocess.CalledProcessError(
            1, ["docker", "compose"], stderr="neo4j is unhealthy"
        ),
        subprocess.TimeoutExpired(
            ["docker", "compose"], 342, stderr=b"neo4j is unhealthy"
        ),
        FileNotFoundError("neo4j is unhealthy: docker CLI missing"),
    ],
    ids=["unhealthy", "timeout", "missing-cli"],
)
def test_startup_failure_cleans_up_before_propagating(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
    startup_error: Exception,
) -> None:
    with (
        patch(
            "slop_code.execution.compose.subprocess.run",
            side_effect=[startup_error, MagicMock()],
        ) as run,
        pytest.raises(RuntimeError, match="neo4j is unhealthy") as caught,
        compose_config.problem_environment(docker_environment, tmp_path),
    ):
        pytest.fail("The environment must not be yielded before readiness")

    assert caught.value.__cause__ is startup_error
    assert run.call_count == 2
    assert run.call_args.args[0][-2:] == ["down", "--remove-orphans"]


@pytest.mark.parametrize(
    "error", [RuntimeError("agent failed"), KeyboardInterrupt()]
)
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_body_failure_or_interrupt_is_preserved_during_cleanup(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
    error: BaseException,
    *,
    cleanup_fails: bool,
) -> None:
    cleanup_result = (
        subprocess.TimeoutExpired(["docker", "compose", "down"], 60)
        if cleanup_fails
        else MagicMock()
    )
    with (
        patch(
            "slop_code.execution.compose.subprocess.run",
            side_effect=[MagicMock(), cleanup_result],
        ) as run,
        patch("slop_code.execution.compose.logger") as logger,
        pytest.raises(type(error)) as caught,
        compose_config.problem_environment(docker_environment, tmp_path),
    ):
        raise error

    assert caught.value is error
    assert run.call_count == 2
    assert run.call_args.args[0][-2:] == ["down", "--remove-orphans"]
    if cleanup_fails:
        logger.error.assert_called_once()
        assert (
            "down --remove-orphans"
            in (logger.error.call_args.kwargs["cleanup_command"])
        )


def test_interrupt_during_startup_still_cleans_up(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
) -> None:
    interrupt = KeyboardInterrupt()
    with (
        patch(
            "slop_code.execution.compose.subprocess.run",
            side_effect=[interrupt, MagicMock()],
        ) as run,
        pytest.raises(KeyboardInterrupt) as caught,
        compose_config.problem_environment(docker_environment, tmp_path),
    ):
        pytest.fail("Interrupted startup must not yield an environment")

    assert caught.value is interrupt
    assert run.call_count == 2
    assert run.call_args.args[0][-2:] == ["down", "--remove-orphans"]


def test_cleanup_failure_after_success_is_reported(
    compose_config: WorkflowComposeConfig,
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
) -> None:
    cleanup_error = subprocess.CalledProcessError(
        1, ["docker", "compose", "down"], stderr="network is still in use"
    )
    with (
        patch(
            "slop_code.execution.compose.subprocess.run",
            side_effect=[MagicMock(), cleanup_error],
        ),
        pytest.raises(RuntimeError, match="network is still in use"),
        compose_config.problem_environment(docker_environment, tmp_path),
    ):
        pass


def test_local_environment_is_rejected_before_startup(
    compose_config: WorkflowComposeConfig,
    tmp_path: Path,
) -> None:
    environment = LocalEnvironmentSpec(name="local-test")
    with (
        patch("slop_code.execution.compose.subprocess.run") as run,
        pytest.raises(ValueError, match="requires a Docker environment"),
        compose_config.problem_environment(environment, tmp_path),
    ):
        pytest.fail("Local environments cannot join the Compose network")
    run.assert_not_called()


@pytest.mark.parametrize("existing_directory", [False, True])
def test_invalid_file_is_rejected_before_startup(
    docker_environment: DockerEnvironmentSpec,
    tmp_path: Path,
    *,
    existing_directory: bool,
) -> None:
    compose_file = tmp_path / "compose.yaml"
    if existing_directory:
        compose_file.mkdir()
    config = WorkflowComposeConfig(file=compose_file)
    with (
        patch("slop_code.execution.compose.subprocess.run") as run,
        pytest.raises(FileNotFoundError, match="file does not exist"),
        config.problem_environment(docker_environment, tmp_path),
    ):
        pytest.fail("An invalid Compose file cannot start dependencies")
    run.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1])
def test_wait_timeout_must_be_positive(timeout: int) -> None:
    with pytest.raises(ValidationError):
        WorkflowComposeConfig(file=Path("compose.yaml"), wait_timeout=timeout)
