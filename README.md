# pymerlin

<!-- start elevator-pitch -->
pymerlin is a discrete event simulation framework, built for use in the [Aerie](https://github.com/NASA-AMMOS/aerie) ecosystem.

To learn more about Aerie, read the [Aerie Docs](https://nasa-ammos.github.io/aerie-docs).
<!-- end elevator-pitch -->

### TODO:

- [x] Daemon tasks
- [x] More interesting cells and resources
- [x] Conditions on static cells
- [x] Conditions on autonomous cells
- [x] Child tasks
- [x] Spiceypy
- [ ] daemon activities
- [x] JPL time
- [x] pip-installable models
- [x] build Aerie-compatible jars and provide docker-compose file with python
- [ ] checkpoint restart
- [ ] Use available port to allow multiple pymerlin programs to run independently
- [ ] Cell expiry
- [ ] Polynomial evolution of cells
- [ ] Value schemas (inferred from python types, maybe?)
- [ ] Garbage collection of cells and effects (perhaps by wrapping integers in Supplier, and using `weakref.finalize`?)
- [ ] Simulation configuration
- [ ] Run with java that isn't simply the "java" executable on the path

## Prerequisites

- Python >=3.11
- Java >=21 (for local simulation via `simulate()`)
- Docker and Docker Compose (for Aerie/PlanDev deployment)

**Install from source:**

```shell
python -m venv venv
source venv/bin/activate
pip install -e .
```

**Run the demo via Aerie (Docker):**

See [Deploying a Python model to Aerie](#deploying-a-python-model-to-aerie-plandev--docker) below.
The demo model at `demo/model.py` is a spacecraft simulation with the following activity types and resources:

Activity types:
- `science_observation` — collects science data, drains battery
- `downlink` — transmits stored data to ground
- `charge_battery` — charges battery via solar panels
- `contact_pass` — combined observation + downlink sequence

Resources:
- `/power/battery_soc_wh` — battery state of charge (Wh)
- `/power/draw_w` — current power draw (W)
- `/data/volume_mb` — onboard data volume (MB)
- `/spacecraft/mode` — current mode (`nominal`, `science`, `downlink`, `safe`)

## Architecture

pymerlin is to merlin as pyspark is to spark. This means that pymerlin uses [py4j](https://www.py4j.org/) as a bridge
between a python process and a java process. This allows pymerlin to use the Aerie simulation engine directly, without
having to re-implement it in python.

This means that running `simulate` starts a subprocess using `java -jar /path/to/pymerlin.jar`.

### Approachability over performance

The main tenet of pymerlin is approachability, and its aim is to enable rapid prototyping of models and activities.
While where possible, performance will be considered, it is expected that someone who wants to seriously engineer the
performance of their simulation will port their code to Java - which has the double benefit of removing socket
communication overhead, as well as giving the engineer a single Java process to instrument and analyze, rather than a
hybrid system, which may be more difficult to characterize.

## Building the Java components

There are three Java subprojects under `java/`:

- **`pymerlin`** — the core simulation JAR used by local `simulate()` calls
- **`pymerlin-gateway`** — the Java agent injected into Aerie JVMs for Docker deployment
- **`pymerlin-bridge`** — the minimal Aerie `MerlinPlugin` that delegates to Python via py4j

To rebuild all:

```shell
cd java
./gradlew assemble
```

To rebuild only what's needed for Docker deployment:

```shell
cd java
./gradlew :pymerlin-gateway:jar :pymerlin-bridge:jar
```

The core simulation JAR (`pymerlin.jar`) lives inside `pymerlin/_internal/jars/` so it is packaged with the Python distribution.

## Deploying a Python model to Aerie (PlanDev / Docker)

pymerlin models can be deployed to a live [Aerie](https://github.com/NASA-AMMOS/aerie) instance running via
PlanDev Docker Compose. This section explains the
architecture and workflow.

### How it works

Native Aerie mission models are Java JARs. pymerlin bridges the gap using two components:

1. **`pymerlin-gateway.jar`** — a Java agent that starts a [py4j](https://www.py4j.org/) `GatewayServer` inside each
   Aerie JVM (merlin server, merlin workers, scheduler workers). It also hosts a `ModelRegistry` singleton on the
   bootstrap classloader so that all classloaders in the JVM share one registry.

2. **`pymerlin-bridge.jar`** (stamped as `my-model.jar`) — a minimal Aerie-compatible mission model JAR containing only
   a `MerlinPlugin` implementation that, when Aerie asks for the model, looks up the Python sidecar in the registry and
   delegates to it via py4j callback.

3. **Python sidecar containers** — one per JVM service. Each sidecar connects to the JVM's py4j gateway, registers a
   `ModelType` implementation backed by the Python model class, and then blocks waiting for Aerie to call back.

```
Aerie JVM  ──(py4j gateway port)──►  Python sidecar
           ◄──(py4j callback port)──
```

### Prerequisites

- [PlanDev](https://github.com/NASA-AMMOS/aerie-ts-user-code-runner) cloned and configured (`.env` filled in)
- Java 21+ and Gradle (to build the gateway and bridge JARs)
- Docker and Docker Compose

### One-time setup

**1. Build the JARs**

```shell
cd java
./gradlew :pymerlin-gateway:jar :pymerlin-bridge:jar
```

**2. Copy the gateway JAR into PlanDev**

```shell
cp java/pymerlin-gateway/build/libs/pymerlin-gateway.jar /path/to/plandev/pymerlin/pymerlin-gateway.jar
cp java/pymerlin-bridge/build/libs/pymerlin-bridge.jar pymerlin/_internal/pymerlin-bridge.jar
```

**3. Build the mission model JAR** (replace `my-model` and `1.0.0` with your model name and version)

```shell
pymerlin build-plandev-jar --name my-model --version 1.0.0 --out my-model.jar
```

**4. Add sidecars to PlanDev's `docker-compose.yml`**

The pymerlin repo ships a `Dockerfile` that bakes the pymerlin library into a sidecar image. Reference it from
`docker-compose.yml` alongside PlanDev's existing services. See `plandev/docker-compose.yml` for a working example
with five sidecar services (one per JVM).

Each JVM service needs two additions to `JAVA_TOOL_OPTIONS`:

```
-Xbootclasspath/a:/usr/src/app/lib/pymerlin-gateway.jar
-javaagent:/usr/src/app/lib/pymerlin-gateway.jar=<GATEWAY_PORT>:<CALLBACK_PORT>
```

And the gateway JAR must be mounted into `/usr/src/app/lib/`:

```yaml
volumes:
  - ./pymerlin/pymerlin-gateway.jar:/usr/src/app/lib/pymerlin-gateway.jar:ro
```

**5. Start the stack**

```shell
# Start JVM services first, wait for them to come up
docker compose up -d aerie_merlin aerie_merlin_worker_1 ...
sleep 12

# Then start sidecars (they retry until the gateway is ready)
docker compose up -d pymerlin_sidecar_merlin pymerlin_sidecar_worker_1 ...
```

**6. Upload the mission model JAR**

Upload `my-model.jar` to the Aerie UI. Aerie will call into the Python sidecar for model extraction (activity types,
resource types, model parameters).

### Iteration workflow

| What changed | Action |
|---|---|
| `model.py` or other model files | `docker compose restart pymerlin_sidecar_*` |
| pymerlin library code (`_serve.py`, `_model_type.py`, etc.) | `docker compose build pymerlin_sidecar_* && docker compose restart pymerlin_sidecar_*` |
| Model name or version | Rebuild bridge JAR, re-upload to Aerie UI |
| `GatewayAgent.java` or `ModelRegistry.java` | Rebuild `pymerlin-gateway.jar`, copy to plandev, restart all JVM services and sidecars |

### Structuring your model

The model entry point is specified with `--model /models/model.py:ClassName`. The `/models` directory is a volume
mount, so you can import other files from the same directory freely:

```python
# model.py
from subsystems.power import PowerModel
from activities.maneuver import Maneuver
```

To use third-party packages (e.g. `numpy`, `spiceypy`), add them to a `requirements.txt` in the pymerlin repo root
and extend the `Dockerfile`:

```dockerfile
FROM python:3.11-slim
COPY . /pymerlin-pkg
RUN pip install /pymerlin-pkg --quiet --no-cache-dir
RUN pip install numpy spiceypy   # add your deps here
```

### Port conventions

| Service | Gateway port | Callback port |
|---|---|---|
| `aerie_merlin` | 25340 | 25440 |
| `aerie_merlin_worker_1` | 25333 | 25433 |
| `aerie_merlin_worker_2` | 25334 | 25434 |
| `aerie_scheduler_worker_1` | 25335 | 25435 |
| `aerie_scheduler_worker_2` | 25336 | 25436 |