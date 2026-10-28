# Running benchmark experiments

This README describes the fork's five workflow conditions. For the original
upstream project overview, see [README_OLD.md](README_OLD.md).

Run `bash run-benchmark.sh` from the repository root. Set `workflow` near the
top of the script to one of these five conditions:

| Workflow | Agent configuration | Environment | Output directory |
| --- | --- | --- | --- |
| `openspec` | `configs/agents/codex-openspec.yaml` | `docker-python3.12-uv` | `outputs/OpenSpec-v1.7.0/` |
| `artnet` | `configs/agents/codex-artnet.yaml` | `docker-python3.12-uv` | `outputs/ArtNet-v1.4.3/` |
| `synergyspec` | `configs/agents/codex-synergyspec.yaml` | `docker-python3.12-uv` | `outputs/SynergySpec-v3.0.2/` |
| `matspec` | `configs/agents/codex-matrixspec.yaml` | `docker-python3.12-uv` | `outputs/MatrixSpec-v0.4.1-beta.3/` |
| `matspec-artnet` | `configs/agents/codex-matrixspec-artnet.yaml` | `docker-python3.12-uv` | `outputs/MatrixSpec-ArtNet-v0.4.1-beta.3-latest/` |

All five use Codex CLI `0.162.1`. The script currently selects
`codex_auth/gpt-6.1-sol`, `thinking=high`, 16 problems, and four workers. Edit the
`problems` array and `--num-workers` to change the batch. The `workflows` map
stores each condition's output name, experiment version, and agent config
suffix in one row, separated by `|`. These versions are experiment labels:
they name output directories but do not select source checkouts, dependency
versions, or Docker images.

The ArtNet CLI image uses `slop-artnet:latest`. Compose builds it from the local
checkout before building the agent image, which copies its installed files.
ArtNet and MatrixSpec launches refresh their CLI and agent images using
Docker's layer cache. MatrixSpec uses `slop-matrixspec:0.4.1-beta.3`. Codex,
OpenSpec, and SynergySpec installation versions remain pinned in their dependency
configuration. All conditions use the shared environment directly.

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
The launcher builds the required images automatically, reusing Docker's layer
cache. All conditions use `configs/environments/docker-python3.12-uv.yaml`.

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
from ArtNet's Dockerfile as `slop-artnet:latest`. This target includes the
skill prompts for both OpenSpec and MatSpec adapters. The agent image copies
the CLI and installs OpenSpec `1.7.0`. Node.js package
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

Place a MatrixSpec `0.4.1-beta.3` checkout at `../MatrixSpec`, beside this
repository. Its Dockerfile must provide a `runtime-base` target with the CLI,
production dependencies, templates, and workflow packs under `/app`.
`configs/agents/codex-matrixspec.compose.yaml` builds that target as
`slop-matrixspec:0.4.1-beta.3`. The native agent configuration selects
`matrixspec-cli` through `workflow.compose.build_services` and sets
`start_services: false`. The harness builds the CLI before refreshing the
agent image and starting problem workers, without starting Compose runtime
services. To use a different checkout, edit the build context in that
Compose file.

The MatrixSpec Dockerfile uses Node.js `22.21.1`, matching the agent runtime,
checks the package version, and installs locked production dependencies with
`npm ci`. Host-side dependency installation is unnecessary. The agent image
copies `/app` to `/opt/matrixspec` and links `matspec` into its npm bin
directory. Both Codex and MatSpec run in the agent container; the native
condition starts no additional Compose services and mounts no MatrixSpec
source directory. It runs the complete Light lifecycle in one workflow node
per checkpoint.

Image preparation runs automatically through `slop-code run`, including
resume, and through `slop-code docker build-agent`. The launcher only selects
the run configuration and does not build images itself.

## MatrixSpec + ArtNet

Set `workflow="matspec-artnet"` in `run-benchmark.sh` to combine MatrixSpec
Light with the local ArtNet checkout. This condition uses the MatrixSpec
image described above. Prepare an ArtNet checkout containing the
`matspec` adapter beside this repository and export `OPENROUTER_API_KEY`, as in
the ArtNet setup.

Both ArtNet conditions use `configs/agents/codex-artnet.compose.yaml`, which
reuses the MatrixSpec build service through Compose `extends`. The combined
condition builds both CLI images and runs Neo4j for each problem. Separate
Docker templates select the required CLI images using the existing template
parameters. Each condition has its own agent image and output directory.

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

Resume a saved run with:

```bash
uv run slop-code run --resume outputs/<run-dir>
```

ArtNet and MatrixSpec conditions rebuild their CLI images from the current
checkouts on resume. Image tags alone do not preserve historical source snapshots;
retain the source revision used for an experiment. MatrixSpec's package
version must still match `0.4.1-beta.3`.

## Archiving results

Archive only after the benchmark has stopped writing, and check the saved
`agent.workflow.type` in each run's configuration before grouping results
under one condition/version label. ArtNet uses `agent.workflow.version: latest`,
native MatrixSpec uses `0.4.1-beta.3`, and MatrixSpec + ArtNet uses
`0.4.1-beta.3-latest`. The combined version labels MatrixSpec `0.4.1-beta.3`
with ArtNet `latest`; the output directory carries the experiment label:

```bash
cd outputs
tar -I 'zstd -T0 -10' -cf ArtNet-v1.4.3.tar.zst ArtNet-v1.4.3
zstd --test ArtNet-v1.4.3.tar.zst
tar --zstd -tf ArtNet-v1.4.3.tar.zst >/dev/null
```

The script already creates versioned output directories. Keep the original
until the archive has been verified and copied to its storage destination.
Restore with `tar --zstd -xf ArtNet-v1.4.3.tar.zst`.
