# Running benchmark experiments

This README describes the fork's five workflow conditions. For the original
upstream project overview, see [README_OLD.md](README_OLD.md).

Run `bash run-benchmark.sh` from the repository root. Set `workflow` near the
top of the script to one of these five conditions:

| Workflow | Agent configuration | Environment | Output directory |
| --- | --- | --- | --- |
| `openspec` | `configs/agents/codex-openspec.yaml` | `docker-python3.12-uv` | `outputs/OpenSpec-v1.7.0/` |
| `artnet` | `configs/agents/codex-artnet.yaml` | `docker-python3.12-uv-artnet` | `outputs/ArtNet-v1.4.3/` |
| `synergyspec` | `configs/agents/codex-synergyspec.yaml` | `docker-python3.12-uv` | `outputs/SynergySpec-v3.0.2/` |
| `matspec` | `configs/agents/codex-matrixspec.yaml` | `docker-python3.12-uv-matrixspec` | `outputs/MatrixSpec-v0.4.1-beta.3/` |
| `matspec-artnet` | `configs/agents/codex-matrixspec-artnet.yaml` | `docker-python3.12-uv-matrixspec` | `outputs/MatrixSpec-ArtNet-v0.4.1-beta.3-artnet-1.4.4/` |

All five use Codex CLI `0.162.1`. The script currently selects
`codex_auth/gpt-6.1-sol`, `thinking=high`, 16 problems, and four workers. Edit the
`problems` array and `--num-workers` to change the batch. Output naming is
controlled by `workflow_save_names` and `experiment_versions`. These versions
are experiment labels: they name output directories and are saved as
`experiment_version` in the build's `experiment.env`. They do not select source
checkouts, dependency versions, or Docker images.

`run-benchmark.sh` generates one UTC timestamp and passes it through the
existing `agent.workflow.version` CLI override. It exports the same value as
`SCB_WORKFLOW_BUILD_TIMESTAMP` for Compose. For ArtNet conditions, it renders
the Docker template's timestamp marker into a persistent `agent.j2` and
passes that file through the existing `agent.docker_template` override. The
agent and ArtNet images therefore share one build timestamp across workers,
without changes to the Python harness. Codex, OpenSpec, and SynergySpec
installation versions remain pinned in their dependency configuration.

Generated templates and the experiment record are kept under
`outputs/<Condition>-v<label>/.builds/<timestamp>/`. The saved run
configuration includes the supplied workflow timestamp and rendered template
path. Keep these files when retaining or resuming a run.

## Common prerequisites

Install uv and Docker, and ensure your account can access the Docker daemon.
Use Python 3.12 for this fork's locked dependencies:

```bash
uv python install 3.12
uv sync --locked --python 3.12
docker info
```

The `codex_auth` provider reads `~/.codex/auth.json`. Supply that file on the
benchmark host before running. The separate `OPENROUTER_API_KEY` below is
needed by both conditions that use ArtNet.

For comparable experiments, use the same problem catalog release:

```bash
uv run slop-code sync v1.0
uv run slop-code problems ls
```

The harness runs Docker containers as the invoking host user's UID/GID. This
keeps bind-mounted workspaces writable when the host user is not UID 1000.
The launcher supplies the required workflow timestamp and builds missing
agent images automatically, reusing Docker's layer cache. Use the launcher
for these conditions: the source YAML files intentionally omit the workflow
version, and the shared ArtNet Docker template requires timestamp substitution.

## OpenSpec and SynergySpec

OpenSpec `1.7.0` and SynergySpec Self-Evolving `3.0.2` install inside their
agent images. They require no additional host Node.js installation or
database. Both execute Propose, Apply, Sync, and Archive for each checkpoint,
with a separate `codex exec` invocation per node.

SynergySpec's configuration deliberately skips its gen-tests, run-tests,
verify, and learn stages. The archive prompt includes the benchmark policy
for those skipped evidence gates.

## ArtNet

Place the ArtNet source checkout intended for the experiment next to this
repository:

```text
parent/
├── slop-code-bench/
└── artnet/
```

`configs/agents/codex-artnet.compose.yaml` builds the `runtime-base` target
from ArtNet's Dockerfile as `slop-artnet:<build-timestamp>`. This target
includes the skill prompts for both OpenSpec and MatSpec adapters. The launcher
exports `SCB_WORKFLOW_BUILD_TIMESTAMP` for Compose and uses the same tag in
the agent image's `COPY`. The agent installs OpenSpec `1.7.0`. Node.js package
installation and the ArtNet build run in Docker. The build uses the local
checkout as it stands; changing an experiment label never checks out a Git
tag or changes the code being built.

Export the API key in the shell that launches the benchmark. For interactive
Bash sessions, this can be set in `~/.bashrc` and loaded with
`source ~/.bashrc`:

```bash
export OPENROUTER_API_KEY="<your-api-key>"
```

Docker Compose must be available as `docker compose`. The harness starts one
Neo4j service per problem, waits for its health check, and attaches the agent
to that problem's private network. The agent connects to `bolt://neo4j:7687`;
HTTP and Bolt host ports are allocated dynamically on loopback. Workers do
not need a preconfigured Neo4j URI pool.

After each problem, Compose removes its containers and network, retaining
named volumes. The output's `workflow-compose.json` records the project name
for resuming and manual cleanup. If cleanup fails, use the command printed in
the log. ArtNet executes Propose, Apply, and Archive for each checkpoint.

## MatrixSpec

MatrixSpec currently runs from a read-only host checkout mounted at
`/opt/matrixspec`. Before running on another machine, edit the host path in
`configs/environments/docker-python3.12-uv-matrixspec.yaml`:

```yaml
extra_mounts:
  /absolute/path/to/MatrixSpec:
    bind: /opt/matrixspec
    mode: ro
```

Replace the existing `/home/stk/projects/MatrixSpec` key with your checkout's
absolute path, for example `/home/Samuel/projects/MatrixSpec`. Relative mount
paths resolve against the temporary problem workspace, so `../MatrixSpec`
does not refer to the repository's sibling directory.

Use a `0.4.1-beta.3` checkout containing `bin/matspec.js`, its source and
templates, and installed dependencies in `node_modules`. Install those
dependencies in that checkout if needed:

```bash
cd /absolute/path/to/MatrixSpec
npm ci
```

This local-checkout condition needs its dependencies prepared before the
read-only mount is used. Codex and the `matspec` executable run inside Docker.
The agent configuration links `matspec` into the container's npm bin directory
and runs the complete Light lifecycle in one workflow node per checkpoint.

## MatrixSpec + ArtNet

Set `workflow="matspec-artnet"` in `run-benchmark.sh` to combine MatrixSpec
Light with the local ArtNet checkout. This condition uses the MatrixSpec
environment and its read-only checkout mount described above. Prepare an
ArtNet checkout containing the `matspec` adapter beside this repository and export
`OPENROUTER_API_KEY`, as in the ArtNet setup.

Both ArtNet conditions share `configs/agents/codex-artnet.compose.yaml` and
`configs/agents/codex-artnet.j2`. Compose builds `slop-artnet:<build-timestamp>`
from ArtNet's `runtime-base` target, which includes both adapters' bundled skill
prompts, and runs Neo4j for each problem. Each launch uses its own timestamp.
The condition has its own agent image and output directory.

Initialization runs `matspec init`, copies the native skills to
`.codex/skills`, then runs `artnet init --adapter matspec` to replace the
main matspec skill and the proposal, delta-spec, tasks, and review skills
with ArtNet's versions, preserving the native implementation skill. The
entire Light lifecycle still runs in one Codex session per checkpoint; its
checkpoint prompt follows the native MatrixSpec prompt except that full
spec/design synchronization is omitted. A project workflow extends the native
Light profile with no full-document update or coverage requirements, retaining
the native stages and checks. The installed ArtNet skills manage retrieval,
staging, and graph publication before archive. Graph publication failures
block archive and must be reported.

## Running and inspecting results

After setting the condition and completing its prerequisites:

```bash
bash run-benchmark.sh
```

Each run is saved under
`outputs/<Condition>-v<version>/<model>_<thinking>_<timestamp>/`. The
`checkpoint-only` prompt supplies the checkpoint specification to the
workflow. Inspect `run_info.yaml`, `result.json`, and the logs to distinguish
run completion from passing every evaluation case. Interrupting a run can
leave completed checkpoint artifacts alongside an incomplete problem run.

To reevaluate a saved run, use `uv run slop-code eval outputs/<run-dir>`.

Before resuming an ArtNet run or running its Compose cleanup command in a new
shell, restore the exported timestamp from its build record:

```bash
source outputs/<Condition>-v<label>/.builds/<timestamp>/experiment.env
uv run slop-code run --resume outputs/<run-dir>
```

## Archiving results

Archive only after the benchmark has stopped writing, and check the saved
`agent.workflow.type` and the experiment record under `.builds/` before
grouping results under one condition/version name. `agent.workflow.version`
in the run configuration records the build timestamp separately:

```bash
cd outputs
tar -I 'zstd -T0 -10' -cf ArtNet-v1.4.3.tar.zst ArtNet-v1.4.3
zstd --test ArtNet-v1.4.3.tar.zst
tar --zstd -tf ArtNet-v1.4.3.tar.zst >/dev/null
```

The script already creates versioned output directories. Keep the original
until the archive has been verified and copied to its storage destination.
Restore with `tar --zstd -xf ArtNet-v1.4.3.tar.zst`.
