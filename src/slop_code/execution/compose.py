"""Host-managed Docker Compose dependencies for a problem workflow."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from slop_code.execution.docker_runtime.models import DockerEnvironmentSpec
from slop_code.execution.models import EnvironmentSpec
from slop_code.logging import get_logger

logger = get_logger(__name__)


class WorkflowComposeConfig(BaseModel):
    """Compose image builds and optional services for a problem workflow.

    Services must use the project's default network. Named volumes are kept
    after shutdown so resuming with the same lifecycle metadata reuses data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: Path = Field(description="Host path to the Docker Compose file.")
    wait_timeout: int = Field(
        default=180,
        gt=0,
        description="Seconds to wait for Compose services to become healthy.",
    )
    build_services: tuple[str, ...] = Field(
        default=(),
        description="Compose service images to build before the agent image.",
    )
    start_services: bool = Field(
        default=True,
        description="Start Compose runtime services for each problem.",
    )

    def build_images(self, environment: EnvironmentSpec) -> None:
        """Build selected workflow images without starting runtime services."""
        if not self.build_services:
            return
        if not isinstance(environment, DockerEnvironmentSpec):
            raise ValueError(
                "Workflow Docker Compose requires a Docker environment."
            )
        compose_file = self.file.resolve()
        if not compose_file.is_file():
            raise FileNotFoundError(
                f"Workflow Docker Compose file does not exist: {compose_file}"
            )
        _run_compose(
            [
                environment.docker.binary,
                "compose",
                "--file",
                str(compose_file),
                "--project-name",
                "scb-workflow-images",
                "build",
                *self.build_services,
            ],
            timeout=900,
        )

    @contextmanager
    def problem_environment(
        self,
        environment: EnvironmentSpec,
        output_path: Path,
    ) -> Iterator[DockerEnvironmentSpec]:
        """Start dependencies and attach this problem to their Docker network."""
        if not isinstance(environment, DockerEnvironmentSpec):
            raise ValueError(
                "Workflow Docker Compose requires a Docker environment."
            )
        if not self.start_services:
            yield environment
            return
        compose_file = self.file.resolve()
        if not compose_file.is_file():
            raise FileNotFoundError(
                f"Workflow Docker Compose file does not exist: {compose_file}"
            )

        output_path = output_path.resolve()
        metadata_path = output_path / "workflow-compose.json"
        project = _load_project(metadata_path)
        network = f"{project}_default"
        command = [
            environment.docker.binary,
            "compose",
            "--file",
            str(compose_file),
            "--project-name",
            project,
        ]
        down_command = [*command, "down", "--remove-orphans"]
        output_path.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {
                    "project": project,
                    "file": str(compose_file),
                    "network": network,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        problem_environment = environment.model_copy(
            update={
                "docker": environment.docker.model_copy(
                    update={"network": network}
                )
            }
        )
        failed = True
        try:
            logger.info(
                "Starting workflow Docker Compose services",
                project=project,
                file=str(compose_file),
            )
            _run_compose(
                [
                    *command,
                    "up",
                    "--detach",
                    "--wait",
                    "--wait-timeout",
                    str(self.wait_timeout),
                ],
                timeout=self.wait_timeout + 300,
            )
            yield problem_environment
            failed = False
        finally:
            try:
                _run_compose(down_command, timeout=60)
            except BaseException:
                logger.error(
                    "Failed to stop workflow Docker Compose services; "
                    "retry the cleanup command manually",
                    project=project,
                    file=str(compose_file),
                    cleanup_command=shlex.join(down_command),
                    exc_info=True,
                )
                if not failed:
                    raise


def _load_project(metadata_path: Path) -> str:
    """Keep project identity on resume and refresh it for overwritten output."""
    if not metadata_path.exists():
        return f"scb-workflow-{uuid.uuid4().hex[:16]}"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid workflow Docker Compose metadata: {metadata_path}"
        ) from error
    project = metadata.get("project") if isinstance(metadata, dict) else None
    if not isinstance(project, str) or not re.fullmatch(
        r"scb-workflow-[0-9a-f]{16}", project
    ):
        raise ValueError(
            f"Invalid workflow Docker Compose project in {metadata_path}"
        )
    return project


def _run_compose(command: list[str], *, timeout: int) -> None:
    """Run the host CLI with bounded execution and actionable error output."""
    try:
        subprocess.run(  # noqa: S603 - Arguments are passed without a shell.
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ) as error:
        stderr = getattr(error, "stderr", None)
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        details = f"{error}\n{stderr}" if stderr else str(error)
        raise RuntimeError(
            f"Workflow Docker Compose failed: {shlex.join(command)}\n{details}"
        ) from error
