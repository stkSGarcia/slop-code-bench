# Running benchmark experiments

The `run-benchmark.sh` script in the project root runs the benchmark with
Codex. It supports three workflows:

- `openspec`: Codex with OpenSpec.
- `artnet`: Codex with OpenSpec, ArtNet, and a Neo4j knowledge graph.
- `synergyspec`: Codex with SynergySpec Self-Evolving.

The workflow is configured inside the script rather than passed as a command-line
argument. Before starting a run, set the value near the top of the script:

```bash
workflow=openspec
```

or:

```bash
workflow=artnet
```

or:

```bash
workflow=synergyspec
```

## Workflow options

The script selects the agent configuration, environment, and output directory
from the `workflow` value:

| Workflow | Agent configuration | Environment | Output directory |
| --- | --- | --- | --- |
| `openspec` | `configs/agents/codex-openspec.yaml` | `docker-python3.12-uv` | `outputs/codex_openspec/` |
| `artnet` | `configs/agents/codex-artnet.yaml` | `docker-python3.12-uv-artnet` | `outputs/codex_artnet/` |
| `synergyspec` | `configs/agents/codex-synergyspec.yaml` | `docker-python3.12-uv` | `outputs/codex_synergyspec/` |

Each run uses this directory layout:

```text
outputs/codex_<workflow>/<model>_<thinking>_<timestamp>/
```

For example:

```text
outputs/codex_artnet/gpt-5.5_high_20260806T1210/
```

## Common prerequisites

Install the Python dependencies:

```bash
uv sync
```

Make sure the Docker daemon is running:

```bash
docker info
```

The script uses the host machine's Codex authentication. Log in on the host
before the first run:

```bash
codex login
```

The current model configuration in `run-benchmark.sh` is:

```text
codex_auth/gpt-5.5
```

The host Codex login is only used by the Codex CLI. It cannot replace the
`OPENROUTER_API_KEY` required by ArtNet for its embedding and LLM calls.

## Selecting problems and concurrency

Edit the `problems` array in `run-benchmark.sh`. Uncomment the problems that
should be included in the run:

```bash
problems=(
  cfgpipe
  code_search
  # env_manager
  # execution_server
  # forge
)
```

Configure concurrency directly in `run-benchmark.sh`:

```bash
--num-workers 3
```

The OpenSpec and SynergySpec workflows have no additional resource-pool
restriction, so their worker counts can be chosen independently.

**For ArtNet, set `--num-workers` to the number of configured problems. The
number of problems must not exceed the number of Neo4j URIs in the resource
pool, because each problem is assigned one Neo4j URI.**

## OpenSpec workflow

Select the workflow in `run-benchmark.sh`:

```bash
workflow=openspec
```

The OpenSpec agent image contains these pinned versions:

- Codex CLI `0.146.1`
- OpenSpec `1.7.0`

The OpenSpec workflow does not require Neo4j or an additional API key.

## SynergySpec workflow

Select the workflow in `run-benchmark.sh`:

```bash
workflow=synergyspec
```

The SynergySpec agent image contains these pinned versions:

- Codex CLI `0.146.1`
- SynergySpec Self-Evolving `3.0.2`

The SynergySpec workflow does not require Neo4j or an additional API key.

## ArtNet prerequisites

The current ArtNet configuration uses Codex CLI `0.146.1`, ArtNet `1.3.0`, and
OpenSpec `1.7.0`.

### 1. Prepare the local ArtNet project

`configs/agents/codex-artnet.yaml` currently contains:

```yaml
local_packages:
  - source: ../artnet
    target: artnet
    exclude:
      - .git
```

The expected directory layout is therefore:

```text
parent/
├── slop-code-bench/
└── artnet/
```

The image build copies the complete ArtNet project except for `.git`, then runs
the project's `_link.mjs` script to link the `artnet` CLI into the container's
global npm bin directory.

`_link.mjs` does not reinstall dependencies or rebuild ArtNet. The host ArtNet
project must already contain:

- `dist/`
- `node_modules/`
- `bin/artnet.js`
- `_link.mjs`

Prepare ArtNet on the host:

```bash
cd ../artnet
pnpm install
pnpm run build
cd ../slop-code-bench
```

pnpm is only used to prepare ArtNet on the host. The current agent image does
not install pnpm.

### 2. Configure the OpenRouter API key

ArtNet uses OpenRouter for its embedding and LLM calls. The host process that
starts the benchmark must have `OPENROUTER_API_KEY` in its environment:

```bash
export OPENROUTER_API_KEY="<your-api-key>"
```

Check that the variable exists without printing its value:

```bash
test -n "${OPENROUTER_API_KEY:-}" && echo "OPENROUTER_API_KEY is set"
```

### 3. Create the Docker network

The Codex agent containers access Neo4j by container name, so both the agent
containers and Neo4j containers must join the same user-defined Docker network.
The current ArtNet environment uses:

```text
slopcodebench
```

Inspect the network:

```bash
docker network inspect slopcodebench
```

Create it if it does not exist:

```bash
docker network create slopcodebench
```

### 4. Prepare the Neo4j container pool

The current ArtNet resource pool is:

```yaml
values:
  - bolt://neo4j-exp1:7687
  - bolt://neo4j-exp2:7687
  - bolt://neo4j-exp3:7687
  - bolt://neo4j-exp4:7687
```

These are addresses inside the Docker network. Each Neo4j instance has a
different host name but uses the same container port, `7687`. The containers do
not need to expose the same host port.

Create and start the four containers with separate data directories and host
ports. Run these commands only when the containers do not already exist:

```bash
mkdir -p "${HOME}/neo4j"/data_exp{1,2,3,4}

docker run -d \
  --name neo4j-exp1 \
  --network slopcodebench \
  -p 7474:7474 \
  -p 7687:7687 \
  -v "${HOME}/neo4j/data_exp1:/data" \
  -e NEO4J_AUTH=neo4j/12345678 \
  neo4j

docker run -d \
  --name neo4j-exp2 \
  --network slopcodebench \
  -p 7475:7474 \
  -p 7688:7687 \
  -v "${HOME}/neo4j/data_exp2:/data" \
  -e NEO4J_AUTH=neo4j/12345678 \
  neo4j

docker run -d \
  --name neo4j-exp3 \
  --network slopcodebench \
  -p 7476:7474 \
  -p 7689:7687 \
  -v "${HOME}/neo4j/data_exp3:/data" \
  -e NEO4J_AUTH=neo4j/12345678 \
  neo4j

docker run -d \
  --name neo4j-exp4 \
  --network slopcodebench \
  -p 7477:7474 \
  -p 7690:7687 \
  -v "${HOME}/neo4j/data_exp4:/data" \
  -e NEO4J_AUTH=neo4j/12345678 \
  neo4j
```

The HTTP interfaces are exposed on host ports `7474` through `7477`, and Bolt
is exposed on host ports `7687` through `7690`. ArtNet does not use these host
ports; it connects through the shared Docker network on container port `7687`.

If existing containers were created without `--network slopcodebench`, connect
them to the network:

```bash
docker network connect slopcodebench neo4j-exp1
docker network connect slopcodebench neo4j-exp2
docker network connect slopcodebench neo4j-exp3
docker network connect slopcodebench neo4j-exp4
```

Running `docker network connect` again for a container that is already attached
will report an `already exists` error. No additional action is required for that
container.

Make sure the required containers are running:

```bash
docker start neo4j-exp1 neo4j-exp2 neo4j-exp3 neo4j-exp4
```

The Neo4j credentials must match `configs/agents/codex-artnet.yaml`. The current
configuration uses the `neo4j` database and connects through
`bolt://<container-name>:7687`.

Resources are assigned in the order of the `problems` array. The first problem
uses the first URI, the second problem uses the second URI, and so on. Every
checkpoint of a problem continues to use the same Neo4j URI.

**There is no resource wait queue, and the runner does not create, clear, or
delete Neo4j data. Before starting another batch of problems, manually clear
the databases that were used by the previous batch.**

### 5. Build the ArtNet agent image

Build the image with the ArtNet agent and environment configurations:

```bash
uv run slop-code docker build-agent \
  configs/agents/codex-artnet.yaml \
  configs/environments/docker-python3.12-uv-artnet.yaml
```

The first execution of `run-benchmark.sh` also builds this image automatically.
Building it manually first is useful because local ArtNet, npm linking, and
Docker context errors can be discovered before starting paid inference.

To ignore an existing matching image and rebuild it:

```bash
uv run slop-code docker build-agent \
  configs/agents/codex-artnet.yaml \
  configs/environments/docker-python3.12-uv-artnet.yaml \
  --force-build
```

Changes to the local ArtNet project normally change the local hash in the image
name, causing a new image to be built.

## Running the benchmark

After selecting the workflow, problems, and worker count in the script, run:

```bash
./run-benchmark.sh
```

If the script is not executable, use:

```bash
bash run-benchmark.sh
```

OpenSpec and SynergySpec execute the following nodes for every checkpoint:

1. Propose
2. Apply
3. Sync
4. Archive

ArtNet executes Propose, Apply, and Archive. Each node is a separate
`codex exec` invocation. Propose receives the checkpoint task, while the
remaining nodes receive the change ID created by Propose.

## Archiving experiment data

Archive a completed condition as a `.tar.zst` file using zstd compression level
10. Before compression, rename its output directory to
`<Condition>-v<version>`, for example `ArtNet-v1.3.0`.

First, stop any benchmark process that is still writing to the directory. Check
the `agent.workflow.type` and `agent.workflow.version` fields in the runs'
`config.yaml` files and make sure every run being archived belongs to the same
condition and version. Do not label a directory with one version if it contains
runs from multiple versions.

For an ArtNet `1.3.0` batch stored in `outputs/codex_artnet`, run:

```bash
cd outputs
mv codex_artnet ArtNet-v1.3.0
tar -I 'zstd -T0 -10' -cf ArtNet-v1.3.0.tar.zst ArtNet-v1.3.0
```

Use the corresponding condition name and configured workflow version for other
experiments, such as `OpenSpec-v1.7.0` or `SynergySpec-v3.0.2`. `-10` selects
zstd compression level 10, and `-T0` uses all available CPU threads.

Verify the compressed archive before removing the uncompressed directory:

```bash
zstd --test ArtNet-v1.3.0.tar.zst
tar --zstd -tf ArtNet-v1.3.0.tar.zst >/dev/null
```

After both checks succeed and the archive has been copied to its long-term
storage location, the uncompressed `ArtNet-v1.3.0` directory can be removed if
it is no longer needed. To restore the archive later:

```bash
tar --zstd -xf ArtNet-v1.3.0.tar.zst
```

## Troubleshooting

### `Missing host environment variables required by workflow: OPENROUTER_API_KEY`

This error only applies to ArtNet. Export the variable in the shell that starts
the benchmark, then run the script again.

### `openspec: command not found`

Rebuild the corresponding agent image. The current Dockerfile configures the
npm global bin directory for login shells; an older image may not contain that
fix.

### Neo4j cannot be reached by container name

Confirm that the agent environment and Neo4j containers are all attached to
the `slopcodebench` network:

```bash
docker network inspect slopcodebench
```

The ArtNet URI must use a Neo4j container name and the container port `7687`.
Do not use `127.0.0.1`: inside the Codex container, it refers to the Codex
container itself.

### More problems than Neo4j resources

Reduce the `problems` list or add usable Neo4j URIs to
`resource_pool.values` in `configs/agents/codex-artnet.yaml`. The number of
problems must be less than or equal to the number of resources.

### Manual interruption

Pressing Ctrl+C while a checkpoint is running records the overall problem run
as an error. Files for previously completed checkpoints may still be complete,
but the resulting `run_info.yaml` summary must not be treated as a complete
benchmark run.
