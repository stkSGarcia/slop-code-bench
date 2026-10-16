"""Unit tests for the Codex agent."""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from slop_code.agent_runner.agents.codex import CodexAgent
from slop_code.agent_runner.agents.codex import CodexConfig
from slop_code.agent_runner.agents.codex.agent import CodexModelProvider
from slop_code.agent_runner.agents.utils import HOME_PATH
from slop_code.agent_runner.credentials import ProviderCredential
from slop_code.agent_runner.models import AgentCostLimits
from slop_code.agent_runner.models import AgentError
from slop_code.agent_runner.registry import build_agent_config
from slop_code.common.llms import APIPricing
from slop_code.common.llms import ModelDefinition
from slop_code.common.llms import TokenUsage
from slop_code.entrypoints.config.loader import load_agent_config
from slop_code.execution import DockerConfig
from slop_code.execution import DockerEnvironmentSpec
from slop_code.execution.compose import WorkflowComposeConfig
from slop_code.execution.runtime import RuntimeEvent
from slop_code.execution.runtime import RuntimeResult
from slop_code.execution.session import Session


class FakeRuntime:
    """Minimal runtime stub for testing."""

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []
        self.cleaned = False
        self.last_stream_args: tuple[tuple, dict] | None = None

    def stream(
        self,
        command: str,
        env: dict,
        timeout: float | None,
        stdin: str | list[str] | None = None,
    ) -> Iterable[RuntimeEvent]:
        self.last_stream_args = ((command, env, stdin, timeout), {})
        yield from self.events

    def cleanup(self) -> None:
        self.cleaned = True


class FakeLogger:
    """Capture debug logs for assertions."""

    def __init__(self) -> None:
        self.debug_calls: list[tuple[str, dict]] = []

    def debug(self, event: str, **kwargs: object) -> None:
        self.debug_calls.append((event, kwargs))


@dataclass
class FakeDockerSpec:
    """Fake docker spec for testing."""

    workdir: str = "/workspace"
    image: str = "test-image"


@dataclass
class FakeSession:
    """Fake session for testing."""

    runtime: FakeRuntime
    working_dir: Path
    spec: DockerEnvironmentSpec | None = None
    last_spawn_env_vars: dict[str, str] | None = None
    last_spawn_mounts: dict[str, dict[str, str] | str] | None = None

    def spawn(self, **_: object) -> FakeRuntime:
        env_vars = cast("dict[str, str] | None", _.get("env_vars"))
        mounts = cast(
            "dict[str, dict[str, str] | str] | None",
            _.get("mounts"),
        )
        self.last_spawn_env_vars = dict(env_vars or {})
        self.last_spawn_mounts = dict(mounts or {})
        return self.runtime


@pytest.fixture
def mock_pricing():
    """Standard pricing for tests."""
    return APIPricing(
        input=0.5,
        output=2.0,
        cache_read=0.1,
    )


@pytest.fixture
def mock_cost_limits():
    """Standard cost limits for tests."""
    return AgentCostLimits(
        step_limit=10,
        cost_limit=100.0,
        net_cost_limit=200.0,
    )


@pytest.fixture
def mock_model_def(mock_pricing):
    """Standard ModelDefinition for tests."""
    return ModelDefinition(
        internal_name="gpt-4-test",
        provider="openai",
        pricing=mock_pricing,
        provider_slugs={"openai": "gpt-4-test"},
    )


@pytest.fixture
def mock_credential():
    """Standard credential for tests."""
    from slop_code.agent_runner.credentials import CredentialType

    return ProviderCredential(
        provider="openai",
        value="test-api-key",
        source="OPENAI_API_KEY",
        destination_key="OPENAI_API_KEY",
        credential_type=CredentialType.ENV_VAR,
    )


class TestCodexConfig:
    """Tests for CodexConfig."""

    def test_version_is_required(self, mock_cost_limits):
        """Version field is required for docker template."""
        with pytest.raises(Exception):  # Pydantic validation error
            CodexConfig.model_validate(
                {
                    "type": "codex",
                    "cost_limits": mock_cost_limits,
                    # Missing version
                }
            )

    def test_config_with_version(self, mock_cost_limits):
        """Config can be created with version."""
        config = CodexConfig(
            type="codex",
            version="1.0.0",
            cost_limits=mock_cost_limits,
        )
        assert config.version == "1.0.0"
        assert config.binary == "codex"

    def test_get_docker_file_renders_version(self, mock_cost_limits):
        """get_docker_file renders version into template."""
        config = CodexConfig(
            type="codex",
            version="2.5.0",
            cost_limits=mock_cost_limits,
        )
        dockerfile = config.get_docker_file("base-image:latest")
        assert dockerfile is not None
        assert "base-image:latest" in dockerfile
        assert "@openai/codex@2.5.0" in dockerfile

    def test_repository_openspec_config_loads(self):
        config_path = (
            Path(__file__).parents[3]
            / "configs"
            / "agents"
            / "codex-openspec.yaml"
        )
        _, data = load_agent_config(config_path)

        config = build_agent_config(data)

        assert isinstance(config, CodexConfig)
        assert config.workflow is not None
        assert config.workflow.init_commands[0] == (
            "openspec",
            "config",
            "set",
            "delivery",
            "both",
        )
        assert [node.name for node in config.workflow.skills] == [
            "openspec-propose",
            "openspec-apply-change",
            "openspec-sync-specs",
            "openspec-archive-change",
        ]
        assert config.workflow.skills[0].arguments == (
            "Please make the changes for the following task. Do not implement "
            "code for this stage.",
            "{task}",
        )
        assert config.workflow.skills[1].arguments == ("{change_id}",)
        assert config.cost_limits.max_retries == 0

    def test_repository_plain_codex_config_keeps_original_version(self):
        config_path = (
            Path(__file__).parents[3] / "configs" / "agents" / "codex.yaml"
        )
        _, data = load_agent_config(config_path)

        config = build_agent_config(data)

        assert isinstance(config, CodexConfig)
        assert config.version == "0.74.0"
        assert config.workflow is None

    def test_repository_synergyspec_config_loads(self):
        config_path = (
            Path(__file__).parents[3]
            / "configs"
            / "agents"
            / "codex-synergyspec.yaml"
        )
        _, data = load_agent_config(config_path)

        config = build_agent_config(data)

        assert isinstance(config, CodexConfig)
        assert config.workflow is not None
        assert config.workflow.changes_dir == Path(
            "synergyspec-selfevolving/changes"
        )
        assert [node.name for node in config.workflow.skills] == [
            "synergyspec-selfevolving-propose",
            "synergyspec-selfevolving-apply-change",
            "synergyspec-selfevolving-sync-specs",
            "synergyspec-selfevolving-archive-change",
        ]
        archive = config.workflow.skills[-1]
        assert archive.arguments[0] == "{change_id}"
        assert "consists only of propose, apply, sync, and archive" in (
            archive.arguments[1]
        )
        assert "Override the archive skill's evidence gates" in (
            archive.arguments[1]
        )
        assert "delta" not in archive.arguments[1].lower()

    def test_repository_artnet_config_manages_neo4j_with_compose(self):
        repository = Path(__file__).parents[3]
        config_path = repository / "configs" / "agents" / "codex-artnet.yaml"
        _, data = load_agent_config(config_path)

        config = build_agent_config(data)

        assert isinstance(config, CodexConfig)
        assert config.workflow is not None
        assert config.workflow.compose is not None
        assert (repository / config.workflow.compose.file).is_file()
        assert config.workflow.compose.build_services == ("artifactnet-cli",)
        assert config.workflow.env["ARTNET_NEO4J_URI"] == "bolt://neo4j:7687"
        assert config.workflow.init_commands[-2] == (
            "artnet",
            "init",
            "--adapter",
            "openspec",
        )
        assert "${ARTNET_NEO4J_URI}" in config.workflow.init_commands[-1][2]

        dockerfile = config.get_docker_file("base-image:latest")

        assert dockerfile is not None
        assert "COPY --from=slop-artnet:1.4.3 /app /opt/artnet" in dockerfile

    @pytest.mark.parametrize("workflow_mode", ["none", "openspec", "compose"])
    def test_prepare_docker_build_only_builds_configured_compose(
        self,
        mock_cost_limits: AgentCostLimits,
        monkeypatch: pytest.MonkeyPatch,
        workflow_mode: str,
    ) -> None:
        workflow = None
        if workflow_mode != "none":
            workflow = {
                "type": "openspec",
                "version": "1.7.0",
                "skills": [{"name": "openspec-propose"}],
            }
            if workflow_mode == "compose":
                workflow["compose"] = {"file": "compose.yaml"}
        config = CodexConfig.model_validate(
            {
                "version": "0.146.1",
                "cost_limits": mock_cost_limits,
                "workflow": workflow,
            }
        )
        environment = DockerEnvironmentSpec(
            name="test",
            docker=DockerConfig(image="test-image"),
        )
        build_images = MagicMock()
        monkeypatch.setattr(WorkflowComposeConfig, "build_images", build_images)

        config.prepare_docker_build(environment)

        if workflow_mode == "compose":
            build_images.assert_called_once_with(environment)
        else:
            build_images.assert_not_called()

    def test_openspec_workflow_uses_exact_versions_and_distinct_image(
        self, mock_cost_limits
    ):
        config = CodexConfig.model_validate(
            {
                "type": "codex",
                "version": "0.146.1",
                "workflow": {
                    "type": "openspec",
                    "version": "1.7.0",
                    "install_commands": [
                        [
                            "npm",
                            "install",
                            "-g",
                            "@fission-ai/openspec@1.7.0",
                        ]
                    ],
                    "skills": [{"name": "openspec-propose"}],
                },
                "cost_limits": mock_cost_limits,
            }
        )

        dockerfile = config.get_docker_file("base-image:latest")

        assert dockerfile is not None
        assert "@openai/codex@0.146.1" in dockerfile
        assert "@fission-ai/openspec@1.7.0" in dockerfile
        assert 'export PATH="$NPM_CONFIG_PREFIX/bin:$PATH"' in dockerfile
        assert config.get_image("python") == (
            "codex-0.146.1-openspec-1.7.0-python"
        )

    def test_repository_script_uses_checkpoint_only_prompt(self):
        repository = Path(__file__).parents[3]
        prompt_path = (
            repository
            / "configs"
            / "prompts"
            / "checkpoint-only.jinja"
        )
        script_path = repository / "run-benchmark.sh"
        script = script_path.read_text()

        assert prompt_path.read_text() == "{{ spec.strip() }}\n"
        assert "--prompt checkpoint-only" in script
        assert "agent_config=configs/agents/codex-artnet.yaml" in script
        assert "environment_config=docker-python3.12-uv-artnet" in script
        assert '--agent "$agent_config"' in script
        assert '--environment "$environment_config"' in script
        assert "--num-workers 4" in script

    @pytest.mark.parametrize(
        "extra_args",
        [
            ["--enable", "default_mode_request_user_input"],
            ["--enable=default_mode_request_user_input"],
            ["--config", "features.default_mode_request_user_input=true"],
            ["--last"],
        ],
    )
    def test_workflow_rejects_reenabling_user_input(
        self, mock_cost_limits, extra_args
    ):
        with pytest.raises(Exception, match="cannot (?:enable|select)"):
            CodexConfig.model_validate(
                {
                    "type": "codex",
                    "version": "0.146.1",
                    "workflow": {
                        "type": "openspec",
                        "version": "1.7.0",
                        "skills": [{"name": "openspec-propose"}],
                    },
                    "extra_args": extra_args,
                    "cost_limits": mock_cost_limits,
                }
            )


class TestCodexAgent:
    """Tests for CodexAgent."""

    @pytest.mark.parametrize(
        "condition", ["openspec", "artnet", "synergyspec", "matrixspec"]
    )
    def test_repository_workflows_keep_runtime_uid_default(
        self,
        condition: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mock_model_def: ModelDefinition,
        mock_credential: ProviderCredential,
    ) -> None:
        """Fork workflows retain setup and the upstream runtime uid policy."""
        config_path = (
            Path(__file__).parents[3]
            / "configs"
            / "agents"
            / f"codex-{condition}.yaml"
        )
        _, data = load_agent_config(config_path)
        config = build_agent_config(data)
        assert isinstance(config, CodexConfig)
        assert config.workflow is not None
        assert config.workflow.type == condition

        dockerfile = config.get_docker_file("base-image:latest")
        assert dockerfile is not None
        permission_step = dockerfile.index('RUN chmod -R a+rwX "$HOME"')
        for command in config.workflow.install_commands:
            assert dockerfile.index(f"RUN {shlex.join(command)}") < permission_step

        for name in config.workflow.env_from_host:
            monkeypatch.setenv(name, "test-host-value")
        session = MagicMock(spec=Session)
        session.spec = DockerEnvironmentSpec(
            name="test", docker=DockerConfig(image="test-image")
        )
        session.working_dir = tmp_path
        agent = CodexAgent._from_config(
            config=config,
            model=mock_model_def,
            credential=mock_credential,
            problem_name="test-problem",
            verbose=False,
            image="test-image",
        )
        assert isinstance(agent, CodexAgent)
        assert agent.workflow is config.workflow
        assert agent.model_provider is None

        try:
            agent.setup(session)

            spawn_args = session.spawn.call_args.kwargs
            assert "user" not in spawn_args
            assert spawn_args["env_vars"]["HOME"] == HOME_PATH
            for key, value in config.workflow.env.items():
                assert spawn_args["env_vars"][key] == value
            for key in config.workflow.env_from_host:
                assert spawn_args["env_vars"][key] == "test-host-value"
        finally:
            agent.cleanup()

    def test_from_config_creates_agent(
        self, mock_cost_limits, mock_model_def, mock_credential
    ):
        """_from_config creates agent from config."""
        config = CodexConfig(
            type="codex",
            version="1.0.0",
            cost_limits=mock_cost_limits,
        )

        agent = CodexAgent._from_config(
            config=config,
            model=mock_model_def,
            credential=mock_credential,
            problem_name="test-problem",
            verbose=False,
            image="test-image",
        )

        assert isinstance(agent, CodexAgent)
        assert agent.binary == "codex"

    def test_from_config_requires_image(
        self, mock_cost_limits, mock_model_def, mock_credential
    ):
        """_from_config requires image."""
        config = CodexConfig(
            type="codex",
            version="1.0.0",
            cost_limits=mock_cost_limits,
        )

        with pytest.raises(ValueError, match="requires an image"):
            CodexAgent._from_config(
                config=config,
                model=mock_model_def,
                credential=mock_credential,
                problem_name="test-problem",
                verbose=False,
                image=None,
            )

    def test_setup_and_cleanup(self, tmp_path, mock_cost_limits, mock_pricing):
        """setup() and cleanup() manage session lifecycle."""
        runtime = FakeRuntime()
        session = FakeSession(runtime=runtime, working_dir=tmp_path)

        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model="gpt-4",
            timeout=60,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        # Before setup, session access should raise
        with pytest.raises(Exception):
            _ = agent.session

        agent.setup(cast("Session", session))

        # After setup, session should be accessible
        assert agent.session == session

        agent.cleanup()

        # After cleanup, session is None
        assert agent._session is None

    def test_reset_clears_state(self, tmp_path, mock_cost_limits, mock_pricing):
        """reset() clears internal state."""
        runtime = FakeRuntime()
        session = FakeSession(runtime=runtime, working_dir=tmp_path)

        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        agent.setup(cast("Session", session))

        # Set some state
        agent._last_prompt = "some prompt"
        agent._last_command = MagicMock()

        agent.reset()

        assert agent._last_prompt == ""
        assert agent._last_command is None

    def test_build_command_basic(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """_build_command creates correct base command."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        command = agent._build_command("do something")

        assert command[0] == "codex"
        assert command[1] == "exec"
        assert "'do something'" in command  # shlex.quote wraps prompt
        assert "--skip-git-repo-check" in command
        assert "--json" in command
        assert "--dangerously-bypass-approvals-and-sandbox" in command
        assert "default_mode_request_user_input" not in command

    def test_build_command_with_model(self, mock_cost_limits, mock_pricing):
        """_build_command includes model when specified."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model="gpt-4",
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        command = agent._build_command("do something")

        assert "--model" in command
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "gpt-4"

    def test_build_command_for_retry_resumes_last_exec_session(
        self, mock_cost_limits, mock_pricing
    ):
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        command = agent._build_command("continue", resume=True)

        assert command[:4] == ["codex", "exec", "resume", "--last"]

    def test_build_command_with_thinking(self, mock_cost_limits, mock_pricing):
        """_build_command includes thinking when specified."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking="high",
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        command = agent._build_command("do something")

        assert "--config" in command
        config_idx = command.index("--config")
        assert 'model_reasoning_effort="high"' in command[config_idx + 1]

    def test_build_command_with_extra_args(
        self, mock_cost_limits, mock_pricing
    ):
        """_build_command appends extra_args."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=["--custom-flag", "value"],
            env={},
            model_provider=None,
        )

        command = agent._build_command("do something")

        assert "--custom-flag" in command
        assert "value" in command

    def test_save_artifacts_writes_files(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """save_artifacts writes prompt and trajectory files."""
        runtime = FakeRuntime()
        session = FakeSession(runtime=runtime, working_dir=tmp_path)

        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        agent.setup(cast("Session", session))
        agent._last_prompt = "test prompt"

        output_dir = tmp_path / "artifacts"
        agent.save_artifacts(output_dir)

        prompt_file = output_dir / "prompt.txt"
        assert prompt_file.exists()
        assert prompt_file.read_text() == "test prompt"

    def test_parse_line_reads_codex_token_count_reported_cost(
        self, mock_pricing
    ):
        """Codex token_count events should use CLI-reported total cost."""
        payload = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_cost": 1.25,
                    "total_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cached_input_tokens": 25,
                        "reasoning_output_tokens": 10,
                    },
                },
            },
        }

        cost, tokens, parsed = CodexAgent.parse_line(
            json.dumps(payload), pricing=mock_pricing
        )

        assert cost == pytest.approx(1.25)
        assert tokens is not None
        assert tokens.input == 100
        assert tokens.output == 50
        assert tokens.cache_read == 25
        assert tokens.reasoning == 10
        assert parsed == payload

    def test_run_uses_codex_reported_total_cost_when_available(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """Reported token_count totals take precedence over local repricing."""
        turn_completed = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cached_input_tokens": 1_000_000,
            },
        }
        token_count = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_cost": 1.25,
                    "total_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cached_input_tokens": 25,
                        "reasoning_output_tokens": 10,
                    },
                },
            },
        }
        stdout = f"{json.dumps(turn_completed)}\n{json.dumps(token_count)}\n"
        runtime = FakeRuntime()
        runtime.events = [
            RuntimeEvent(kind="stdout", text=stdout),
            RuntimeEvent(
                kind="finished",
                result=RuntimeResult(
                    exit_code=0,
                    stdout=stdout,
                    stderr="",
                    setup_stdout="",
                    setup_stderr="",
                    elapsed=0.1,
                    timed_out=False,
                ),
            ),
        ]
        spec = DockerEnvironmentSpec(
            name="test",
            docker=DockerConfig(image="test-image"),
        )
        session = FakeSession(
            runtime=runtime,
            working_dir=tmp_path,
            spec=spec,
        )
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        agent.setup(cast("Session", session))
        agent.run("do something")

        assert agent.usage.cost == pytest.approx(1.25)
        assert agent.usage.net_tokens.input == 100
        assert agent.usage.net_tokens.output == 50
        assert agent.usage.net_tokens.cache_read == 25
        assert agent.usage.net_tokens.reasoning == 10

    def test_run_uses_codex_trace_token_count_when_stdout_lacks_reasoning(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """Trace token_count totals fill reasoning missing from stdout."""
        stdout_payload = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cached_input_tokens": 1_000_000,
            },
        }
        stdout = f"{json.dumps(stdout_payload)}\n"
        runtime = FakeRuntime()
        runtime.events = [
            RuntimeEvent(kind="stdout", text=stdout),
            RuntimeEvent(
                kind="finished",
                result=RuntimeResult(
                    exit_code=0,
                    stdout=stdout,
                    stderr="",
                    setup_stdout="",
                    setup_stderr="",
                    elapsed=0.1,
                    timed_out=False,
                ),
            ),
        ]
        spec = DockerEnvironmentSpec(
            name="test",
            docker=DockerConfig(image="test-image"),
        )
        session = FakeSession(
            runtime=runtime,
            working_dir=tmp_path,
            spec=spec,
        )
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        agent.setup(cast("Session", session))
        assert agent._trace_dir is not None
        trace_file = agent._trace_dir / "rollout.jsonl"
        trace_file.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 100,
                                "output_tokens": 50,
                                "cached_input_tokens": 25,
                                "reasoning_output_tokens": 10,
                            },
                        },
                    },
                }
            )
            + "\n"
        )

        agent.run("do something")

        assert agent.usage.cost == pytest.approx(
            mock_pricing.get_cost(agent.usage.net_tokens)
        )
        assert agent.usage.net_tokens.input == 100
        assert agent.usage.net_tokens.output == 50
        assert agent.usage.net_tokens.cache_read == 25
        assert agent.usage.net_tokens.reasoning == 10

    @pytest.mark.parametrize("cached_only", [False, True])
    def test_run_prices_all_threads_once_and_preserves_cache_writes(
        self,
        tmp_path: Path,
        mock_cost_limits: AgentCostLimits,
        *,
        cached_only: bool,
    ) -> None:
        pricing = APIPricing.model_validate(
            {
                "input": 4,
                "output": 15,
                "cache_read": 0.2,
                "cache_write": 5,
                "prompt_tiers": [
                    {
                        "max_input_tokens": 272_000,
                        "input": 2,
                        "output": 10,
                        "cache_read": 0.1,
                        "cache_write": 2.5,
                    }
                ],
            }
        )
        usage = {
            "input_tokens": 100_000,
            "cached_input_tokens": 80_000,
            "cache_write_input_tokens": 5_000,
            "output_tokens": 1_000,
            "reasoning_output_tokens": 100,
        }
        if cached_only:
            usage = {
                "input_tokens": 1,
                "cached_input_tokens": 1,
                "output_tokens": 0,
            }
        stdout = json.dumps(
            {"type": "turn.completed", "usage": usage}
        ) + "\n"
        runtime = FakeRuntime()
        runtime.events = [
            RuntimeEvent(kind="stdout", text=stdout),
            RuntimeEvent(
                kind="finished",
                result=RuntimeResult(
                    exit_code=0,
                    stdout=stdout,
                    stderr="",
                    setup_stdout="",
                    setup_stderr="",
                    elapsed=0.1,
                    timed_out=False,
                ),
            ),
        ]
        session = FakeSession(
            runtime=runtime,
            working_dir=tmp_path,
            spec=DockerEnvironmentSpec(
                name="test", docker=DockerConfig(image="test-image")
            ),
        )
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )
        agent.setup(cast("Session", session))
        assert agent._trace_dir is not None
        records = [
            {
                "type": "token_usage_record",
                "payload": {
                    "thread_id": thread,
                    "response_id": response,
                    "usage": usage,
                },
            }
            for thread, response in [
                ("root", "one"), ("root", "two"), ("child", "three")
            ]
        ]
        (agent._trace_dir / "root.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records[:2])
        )
        (agent._trace_dir / "child.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records[::2])
        )

        agent.run("do something")

        expected_cost = 0.0000003 if cached_only else 0.1815
        expected_tokens = (
            TokenUsage(input=3, cache_read=3)
            if cached_only
            else TokenUsage(
                input=300_000, cache_read=240_000, cache_write=15_000,
                output=3_000, reasoning=300,
            )
        )
        assert agent.usage.cost == pytest.approx(expected_cost, abs=1e-12)
        assert agent.usage.net_tokens == expected_tokens
        agent.retry()
        assert agent.usage.cost == pytest.approx(expected_cost, abs=1e-12)
        assert agent.usage.net_tokens == expected_tokens
        agent.cleanup()

    def test_tiered_turn_totals_without_request_traces_are_rejected(
        self, mock_cost_limits: AgentCostLimits
    ) -> None:
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=APIPricing.model_validate(
                {"prompt_tiers": [{"max_input_tokens": 272_000}]}
            ),
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        with pytest.raises(AgentError, match="CLI turn totals are insufficient"):
            agent._sync_usage({"input_tokens": 300_000})

    def test_save_artifacts_copies_codex_traces(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """save_artifacts copies Codex trace jsonl files from home."""
        runtime = FakeRuntime()
        spec = DockerEnvironmentSpec(
            name="test",
            docker=DockerConfig(image="test-image"),
        )
        session = FakeSession(
            runtime=runtime,
            working_dir=tmp_path,
            spec=spec,
        )

        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        agent.setup(cast("Session", session))
        assert agent._trace_dir is not None
        trace_dir = agent._trace_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / "trace.jsonl"
        trace_file.write_text('{"type":"turn.started"}\n')

        output_dir = tmp_path / "artifacts"
        agent.save_artifacts(output_dir)

        saved_trace = output_dir / "trace.jsonl"
        assert saved_trace.exists()
        assert saved_trace.read_text() == trace_file.read_text()

    def test_setup_uses_default_home_for_docker(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """setup keeps HOME at agent home and mounts codex dir."""
        runtime = FakeRuntime()
        spec = DockerEnvironmentSpec(
            name="test",
            docker=DockerConfig(image="test-image"),
        )
        session = FakeSession(
            runtime=runtime,
            working_dir=tmp_path,
            spec=spec,
        )

        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        agent.setup(cast("Session", session))

        assert session.last_spawn_env_vars is not None
        assert session.last_spawn_env_vars.get("HOME") == HOME_PATH
        assert session.last_spawn_mounts is not None
        assert any(
            isinstance(value, dict)
            and value.get("bind") == f"{HOME_PATH}/.codex"
            for value in session.last_spawn_mounts.values()
        )

    def test_setup_uses_configured_workspace_directory_for_traces(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mock_cost_limits,
        mock_pricing,
    ) -> None:
        """SCB_WORKSPACE_DIR controls where host trace mounts live."""
        workspace_base = tmp_path / "workspaces"
        monkeypatch.setenv("SCB_WORKSPACE_DIR", str(workspace_base))
        runtime = FakeRuntime()
        spec = DockerEnvironmentSpec(
            name="test",
            docker=DockerConfig(image="test-image"),
        )
        session = FakeSession(
            runtime=runtime,
            working_dir=tmp_path,
            spec=spec,
        )
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        try:
            agent.setup(cast("Session", session))

            assert agent._trace_dir is not None
            assert agent._trace_dir.parent == workspace_base
            assert session.last_spawn_mounts is not None
            assert str(agent._trace_dir) in session.last_spawn_mounts
        finally:
            agent.cleanup()

        assert workspace_base.is_dir()

    def test_save_artifacts_logs_trace_counts(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """_save_codex_traces logs discovered and saved trace counts."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )
        logger = FakeLogger()
        agent.log = logger

        trace_dir = tmp_path / "codex_traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "trace.jsonl").write_text('{"type":"turn.started"}\n')
        agent._trace_dir = trace_dir

        output_dir = tmp_path / "artifacts"
        agent._save_codex_traces(output_dir)

        assert any(
            event == "agent.codex.traces.found" and kwargs.get("files") == 1
            for event, kwargs in logger.debug_calls
        )
        assert any(
            event == "agent.codex.traces.saved" and kwargs.get("saved") == 1
            for event, kwargs in logger.debug_calls
        )

    def test_save_artifacts_only_copies_new_codex_trace_files_between_checkpoints(
        self, tmp_path, mock_cost_limits, mock_pricing
    ):
        """Later checkpoints should only save newly created Codex trace files."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        trace_dir = tmp_path / "codex_traces"
        first_trace = trace_dir / "trace1.jsonl"
        trace_dir.mkdir(parents=True, exist_ok=True)
        first_trace.write_text('{"type":"turn.started"}\n')
        agent._trace_dir = trace_dir

        first_output = tmp_path / "checkpoint_1"
        agent._save_codex_traces(first_output)
        assert (first_output / "trace1.jsonl").exists()

        agent.reset()

        second_trace = trace_dir / "trace2.jsonl"
        second_trace.write_text('{"type":"turn.completed"}\n')

        second_output = tmp_path / "checkpoint_2"
        agent._save_codex_traces(second_output)

        assert not (second_output / "trace1.jsonl").exists()
        saved_new_trace = second_output / "trace2.jsonl"
        assert saved_new_trace.exists()
        assert saved_new_trace.read_text() == second_trace.read_text()

    def test_build_command_with_thinking_disabled(
        self, mock_cost_limits, mock_pricing
    ):
        """_build_command sets model_max_output_tokens=0 when thinking is disabled."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking="disabled",
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=None,
        )

        command = agent._build_command("do something")

        assert "--config" in command
        config_idx = command.index("--config")
        assert "model_max_output_tokens=0" in command[config_idx + 1]
        # model_reasoning_effort should NOT be present
        command_str = " ".join(command)
        assert "model_reasoning_effort" not in command_str

    def test_build_command_with_max_thinking_tokens(
        self, mock_cost_limits, mock_pricing
    ):
        """_build_command includes max_thinking_tokens when specified."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model=None,
            timeout=None,
            thinking=None,
            max_thinking_tokens=8192,
            extra_args=[],
            env={},
            model_provider=None,
        )

        command = agent._build_command("do something")

        assert "--config" in command
        config_idx = command.index("--config")
        assert "model_max_output_tokens=8192" in command[config_idx + 1]


class TestCodexModelProviderRouting:
    """Tests for routing Codex through a gateway provider endpoint."""

    @pytest.fixture
    def portkey_credential(self):
        """Credential resolved from the portkey provider."""
        from slop_code.agent_runner.credentials import CredentialType

        return ProviderCredential(
            provider="portkey",
            value="test-portkey-key",
            source="SCB_PORTKEY",
            destination_key="PORTKEY_API_KEY",
            credential_type=CredentialType.ENV_VAR,
        )

    @pytest.fixture
    def gateway_model(self, mock_pricing):
        """Model declaring a codex endpoint plus a portkey-specific slug."""
        return ModelDefinition(
            internal_name="gpt-5.6-sol",
            provider="openai",
            pricing=mock_pricing,
            provider_slugs={"portkey": "@openai/gpt-5.6-sol"},
            agent_specific={"codex": {"endpoint": "openai", "thinking": "high"}},
        )

    def test_from_config_without_endpoint_leaves_provider_unset(
        self, mock_cost_limits, mock_model_def, mock_credential
    ):
        """A model with no codex endpoint keeps Codex pointed at OpenAI."""
        config = CodexConfig(
            type="codex", version="1.0.0", cost_limits=mock_cost_limits
        )

        agent = CodexAgent._from_config(
            config=config,
            model=mock_model_def,
            credential=mock_credential,
            problem_name="test-problem",
            verbose=False,
            image="test-image",
        )

        assert agent.model_provider is None
        assert "model_provider" not in " ".join(agent._build_command("go"))

    def test_from_config_without_provider_endpoint_leaves_provider_unset(
        self, mock_cost_limits, gateway_model, mock_credential
    ):
        """Declaring an endpoint is inert when the provider declares none."""
        config = CodexConfig(
            type="codex", version="1.0.0", cost_limits=mock_cost_limits
        )

        agent = CodexAgent._from_config(
            config=config,
            model=gateway_model,
            credential=mock_credential,
            problem_name="test-problem",
            verbose=False,
            image="test-image",
        )

        assert agent.model_provider is None

    def test_from_config_builds_provider_from_credential_provider(
        self, mock_cost_limits, gateway_model, portkey_credential
    ):
        """The endpoint resolves against the credential provider, not the model's."""
        config = CodexConfig(
            type="codex", version="1.0.0", cost_limits=mock_cost_limits
        )

        agent = CodexAgent._from_config(
            config=config,
            model=gateway_model,
            credential=portkey_credential,
            problem_name="test-problem",
            verbose=False,
            image="test-image",
        )

        assert agent.model_provider == CodexModelProvider(
            provider_id="portkey",
            base_url="https://api.portkey.ai/v1",
            env_key="PORTKEY_API_KEY",
        )
        assert agent.model == "@openai/gpt-5.6-sol"

    def test_from_config_rejects_non_openai_endpoint(
        self, mock_cost_limits, mock_pricing, portkey_credential
    ):
        """Codex only speaks the OpenAI wire format."""
        model = ModelDefinition(
            internal_name="gpt-5.6-sol",
            provider="openai",
            pricing=mock_pricing,
            agent_specific={"codex": {"endpoint": "anthropic"}},
        )
        config = CodexConfig(
            type="codex", version="1.0.0", cost_limits=mock_cost_limits
        )

        with pytest.raises(ValueError, match="OpenAI-compatible endpoint"):
            CodexAgent._from_config(
                config=config,
                model=model,
                credential=portkey_credential,
                problem_name="test-problem",
                verbose=False,
                image="test-image",
            )

    def test_build_command_registers_and_selects_provider(
        self, mock_cost_limits, mock_pricing
    ):
        """_build_command emits the model_providers config flags before --model."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=None,
            binary="codex",
            model="@openai/gpt-5.6-sol",
            timeout=None,
            thinking="high",
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=CodexModelProvider(
                provider_id="portkey",
                base_url="https://api.portkey.ai/v1",
                env_key="PORTKEY_API_KEY",
            ),
        )

        command = agent._build_command("do something")
        configs = [
            command[idx + 1]
            for idx, arg in enumerate(command)
            if arg == "--config"
        ]

        assert configs == [
            'model_provider="portkey"',
            'model_providers.portkey.name="portkey"',
            'model_providers.portkey.base_url="https://api.portkey.ai/v1"',
            'model_providers.portkey.env_key="PORTKEY_API_KEY"',
            'model_providers.portkey.wire_api="responses"',
            'model_reasoning_effort="high"',
        ]
        assert command[command.index("--model") + 1] == "@openai/gpt-5.6-sol"

    def test_prepare_runtime_execution_exports_provider_env_key(
        self, mock_cost_limits, mock_pricing, portkey_credential
    ):
        """The env_key named in the config flags is what gets exported."""
        agent = CodexAgent(
            problem_name="test-problem",
            verbose=False,
            image="test-image",
            cost_limits=mock_cost_limits,
            pricing=mock_pricing,
            credential=portkey_credential,
            binary="codex",
            model="@openai/gpt-5.6-sol",
            timeout=None,
            thinking=None,
            max_thinking_tokens=None,
            extra_args=[],
            env={},
            model_provider=CodexModelProvider(
                provider_id="portkey",
                base_url="https://api.portkey.ai/v1",
                env_key="PORTKEY_API_KEY",
            ),
        )

        command, env_overrides = agent._prepare_runtime_execution("do it")

        assert env_overrides["PORTKEY_API_KEY"] == "test-portkey-key"
        assert 'model_providers.portkey.env_key="PORTKEY_API_KEY"' in command
