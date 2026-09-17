from __future__ import annotations

import queue
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from slop_code.agent_runner.agent import AgentConfigBase
from slop_code.agent_runner.agents.codex.agent import CodexConfig
from slop_code.agent_runner.credentials import CredentialType
from slop_code.agent_runner.credentials import ProviderCredential
from slop_code.agent_runner.models import AgentCostLimits
from slop_code.agent_runner.models import UsageTracker
from slop_code.agent_runner.resume import ResumeInfo
from slop_code.common.llms import APIPricing
from slop_code.common.llms import ModelDefinition
from slop_code.entrypoints.problem_runner import worker
from slop_code.entrypoints.problem_runner.models import RunTaskConfig
from slop_code.evaluation import PassPolicy
from slop_code.evaluation import ProblemConfig
from slop_code.execution import DockerEnvironmentSpec
from slop_code.execution.docker_runtime.models import DockerConfig


@pytest.fixture
def problem_config() -> ProblemConfig:
    tests_root = Path(__file__).resolve().parents[2]
    return ProblemConfig.from_yaml(
        tests_root / "agent_runner/resources/inventory_cli_debug_problem"
    )


@pytest.fixture
def task_config(tmp_path: Path) -> RunTaskConfig:
    return RunTaskConfig(
        problem_base_path=tmp_path,
        run_dir=tmp_path / "run",
        image="test-agent-image",
        env_spec=DockerEnvironmentSpec(
            name="test",
            docker=DockerConfig(image="test-base-image", network="bridge"),
        ),
        agent_config=CodexConfig(
            version="test",
            cost_limits=AgentCostLimits(cost_limit=1, net_cost_limit=2),
        ),
        model_def=ModelDefinition(
            name="test-model",
            internal_name="test-model",
            provider="test",
            pricing=APIPricing(),
        ),
        credential=ProviderCredential(
            provider="test",
            credential_type=CredentialType.ENV_VAR,
            value="test-credential",
            source="TEST_API_KEY",
            destination_key="TEST_API_KEY",
        ),
        prompt_template="{{ spec }}",
        pass_policy=PassPolicy.ANY_CASE,
        seed=0,
        verbosity=0,
    )


@pytest.mark.parametrize("failure_stage", [None, "agent", "runner"])
def test_services_cover_agent_creation_and_execution(
    monkeypatch: pytest.MonkeyPatch,
    task_config: RunTaskConfig,
    problem_config: ProblemConfig,
    failure_stage: str | None,
) -> None:
    events: list[str] = []
    output_path = task_config.run_dir / "problem"
    original_environment = task_config.env_spec
    assert isinstance(original_environment, DockerEnvironmentSpec)
    managed_environment = original_environment.model_copy(
        update={
            "docker": original_environment.docker.model_copy(
                update={"network": "managed-problem-network"}
            )
        }
    )

    @contextmanager
    def problem_environment(
        self: CodexConfig,
        environment: DockerEnvironmentSpec,
        output_path_arg: Path,
    ) -> Iterator[DockerEnvironmentSpec]:
        assert self is task_config.agent_config
        assert environment is original_environment
        assert output_path_arg == output_path
        events.append("services_ready")
        try:
            yield managed_environment
        finally:
            events.append("services_stopped")

    agent = object()
    run_result = {"summary": {"state": "completed"}}

    def create_agent(*args: Any, **kwargs: Any) -> object:
        assert events == ["services_ready"]
        events.append("agent")
        if failure_stage == "agent":
            raise RuntimeError("agent failed")
        return agent

    def run_agent(**kwargs: Any) -> dict[str, Any]:
        assert events == ["services_ready", "agent"]
        assert kwargs["agent"] is agent
        assert kwargs["output_path"] == output_path
        assert kwargs["run_spec"].environment is managed_environment
        assert (
            kwargs["run_spec"].environment.docker.network
            == "managed-problem-network"
        )
        events.append("runner")
        if failure_stage == "runner":
            raise RuntimeError("runner failed")
        return run_result

    monkeypatch.setattr(CodexConfig, "problem_environment", problem_environment)
    monkeypatch.setattr(worker.Agent, "from_config", create_agent)
    monkeypatch.setattr(worker.runner, "run_agent", run_agent)

    if failure_stage:
        with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
            worker.run_agent_on_problem(
                problem_config,
                "problem",
                task_config,
                queue.Queue(),
                output_path,
            )
    else:
        result = worker.run_agent_on_problem(
            problem_config,
            "problem",
            task_config,
            queue.Queue(),
            output_path,
        )
        assert result is run_result

    expected = ["services_ready", "agent"]
    if failure_stage != "agent":
        expected.append("runner")
    assert events == [*expected, "services_stopped"]
    assert task_config.env_spec is original_environment
    assert original_environment.docker.network == "bridge"


def test_completed_resume_does_not_start_services(
    monkeypatch: pytest.MonkeyPatch,
    task_config: RunTaskConfig,
    problem_config: ProblemConfig,
) -> None:
    task_config.resume = True
    resume_info = ResumeInfo(
        resume_from_checkpoint="",
        completed_checkpoints=["checkpoint_1", "checkpoint_2"],
        last_snapshot_dir=None,
        prior_usage=UsageTracker(cost=0.5),
    )
    detect_resume = Mock(return_value=resume_info)
    start_services = Mock()
    create_agent = Mock()
    run_agent = Mock()
    send_progress = Mock()
    monkeypatch.setattr(worker, "detect_resume_point", detect_resume)
    monkeypatch.setattr(CodexConfig, "problem_environment", start_services)
    monkeypatch.setattr(worker.Agent, "from_config", create_agent)
    monkeypatch.setattr(worker.runner, "run_agent", run_agent)
    monkeypatch.setattr(worker, "_send_completed_progress", send_progress)

    result = worker.run_agent_on_problem(
        problem_config,
        "problem",
        task_config,
        queue.Queue(),
        task_config.run_dir / "problem",
    )

    assert result["summary"]["state"] == "skipped"
    assert result["summary"]["usage"]["cost"] == 0.5
    assert detect_resume.call_args.kwargs["environment"] is task_config.env_spec
    send_progress.assert_called_once()
    start_services.assert_not_called()
    create_agent.assert_not_called()
    run_agent.assert_not_called()


def test_codex_without_compose_keeps_execution_environment(
    monkeypatch: pytest.MonkeyPatch,
    task_config: RunTaskConfig,
    problem_config: ProblemConfig,
) -> None:
    create_agent = Mock(return_value=object())
    run_result = {"summary": {"state": "completed"}}
    run_agent = Mock(return_value=run_result)
    monkeypatch.setattr(worker.Agent, "from_config", create_agent)
    monkeypatch.setattr(worker.runner, "run_agent", run_agent)

    result = worker.run_agent_on_problem(
        problem_config,
        "problem",
        task_config,
        queue.Queue(),
        task_config.run_dir / "problem",
    )

    assert result is run_result
    assert (
        run_agent.call_args.kwargs["run_spec"].environment
        is task_config.env_spec
    )
    assert task_config.env_spec.docker.network == "bridge"


def test_base_agent_environment_hook_is_noop(
    task_config: RunTaskConfig,
) -> None:
    agent_config = AgentConfigBase(
        type="test",
        cost_limits=AgentCostLimits(cost_limit=1, net_cost_limit=2),
    )

    with agent_config.problem_environment(
        task_config.env_spec, task_config.run_dir / "problem"
    ) as environment:
        assert environment is task_config.env_spec
