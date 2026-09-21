# Planet CharLu — SpacePort Bazaar Client

Planet CharLu builds a Python client for the
SpacePort Bazaar protocol. The client connects to the course-provided validator over WebSockets, exchanges binary Protocol Buffer messages,
tracks marketplace state, and completes the required ten-step trading exercise as
player **P01** while the validator controls **P02**.

The project is a command-line client. 

## Technology choices

- Python 3.11
- `asyncio` and `websockets`
- Protocol Buffers with `protobuf` and `grpcio-tools`
- `pytest` and `pytest-asyncio`
- Docker Compose for a consistent Linux development environment

`grpcio-tools` is used only to generate Python classes from `bazaar.proto`; the
client communicates over WebSockets, not gRPC.

## Current status

- [x] GitHub repository created and collaborator added
- [x] Repository structure created
- [x] Python dependencies configured
- [x] Protobuf schema added
- [x] Protocol classes generated and imported successfully
- [x] Protobuf smoke tests passing
- [x] Docker development environment configured and verified
- [x] Validator binary selected and started successfully in Docker
- [x] Python client entry point runs
- [ ] WebSocket connection implemented
- [ ] Authentication implemented
- [ ] Continuous receive loop implemented
- [ ] Server messages decoded and state tracked
- [ ] Ten-step validation exercise completed
- [ ] Final validation report reviewed

Update this list as work is merged into `main`. Do not mark an item complete
until it works from a fresh clone using the instructions below.

## Repository layout

```text
planet_charlu/
├── README.md
├── requirements.txt
├── Dockerfile
├── compose.yaml
├── .dockerignore
├── .gitignore
├── protos/
│   └── bazaar.proto
├── scripts/
│   ├── generate_proto.sh
│   ├── verify_proto.sh
│   └── run_validator.sh
├── src/
│   └── planet_charlu/
│       ├── __init__.py
│       ├── main.py
│       └── generated/
│           ├── __init__.py
│           └── bazaar_pb2.py
├── tests/
│   └── test_proto.py
└── validator/
    ├── spaceport-validate-linux-arm64
    └── spaceport-validate-linux-x86_64
```

## Where should I run commands?

There are only two environments you need to distinguish in the recommended
workflow:

| Environment | How to recognize it | Use it for |
|---|---|---|
| Host terminal | Normal macOS, Windows, or Linux terminal | Git, editing files, and starting or stopping Docker |
| Container terminal | Opened with `docker compose exec app bash`; the prompt may begin with `root@...:/workspace#` | Python, Protobuf generation, tests, validator, and client |

The **Docker container** is the Linux environment. A **Docker terminal** is
simply a shell opened inside that running container; they are not separate
environments.

A local `.venv` is optional. It is useful when running Python directly on the
host without Docker, but it is not required for the recommended Docker workflow.
Do not activate `.venv` inside the container—Docker already has its own isolated
Python installation.

In short:

- Run `git ...` and `docker compose ...` in the host terminal.
- Run `python ...`, `pytest`, and `./scripts/...` in the container terminal.
- Use `.venv` only when intentionally running Python outside Docker.

## First-time setup

### 1. Clone the repository on the host

```bash
git clone https://github.com/lucykgood/planet_charlu.git
cd planet_charlu
```

### 2. Build and start the development container

Make sure Docker Desktop is running, then use the host terminal:

```bash
docker compose up --detach --build
docker compose ps
```

The `app` service should show as running.

### 3. Enter the container

```bash
docker compose exec app bash
```

The repository is mounted at `/workspace`, so edits made on the host are
immediately visible in the container.

### 4. Verify the setup inside the container

```bash
python --version
./scripts/verify_proto.sh
python -m pytest -v
python -m planet_charlu.main
```

Expected results:

- Python reports version 3.11.
- Protobuf generation and import succeed.
- Tests pass.
- The client entry point runs without an import error.

If these commands pass, first-time setup is complete.

## Returning to the project

Use this sequence each time you return.

### Host terminal

```bash
cd /path/to/planet_charlu
git switch main
git pull --ff-only
docker compose up --detach
docker compose ps
```

If `requirements.txt`, `Dockerfile`, or `compose.yaml` changed after pulling,
rebuild instead:

```bash
docker compose up --detach --build
```

Create a branch for new work:

```bash
git switch -c feature/short-description
```

Then enter the container:

```bash
docker compose exec app bash
```

### Container terminal

Before coding, run:

```bash
./scripts/verify_proto.sh
python -m pytest -v
```

When finished, type `exit` to leave the container shell. The container continues
running in the background until you stop it from the host:

```bash
docker compose down
```

`docker compose down` does not delete the repository or source code.

## Protobuf generation

The source schema is `protos/bazaar.proto`. Generate `bazaar_pb2.py` inside the
container with:

```bash
./scripts/generate_proto.sh
```

Verify generation and import together with:

```bash
./scripts/verify_proto.sh
```

Do not manually edit `src/planet_charlu/generated/bazaar_pb2.py`; regenerate it
from the schema. Do not add `--grpc_python_out` because this project does not use
gRPC networking.

When `bazaar.proto` changes, regenerate and run the tests before committing:

```bash
./scripts/generate_proto.sh
python -m pytest -v
git diff -- src/planet_charlu/generated
```

## Run the validator and client

The validator and client should run in two shells inside the same container so
the client can use `ws://127.0.0.1:3001/ws`.

### Terminal 1: validator

From the host:

```bash
docker compose exec app bash
```

Then, inside the container:

```bash
./scripts/run_validator.sh
```

The validator does not support a standalone `--help` flag. The wrapper selects
the ARM64 or x86-64 binary and supplies `--codec protobuf` automatically. Leave
this terminal running.

### Terminal 2: client

Open another host terminal:

```bash
cd /path/to/planet_charlu
docker compose exec app bash
```

Then run inside the container:

```bash
python -m planet_charlu.main
```

Stop the validator with `Ctrl+C`. Use `exit` to leave either container shell.

The validator may create `validation-credentials.json` and
`validation-report.json`. Both are local files ignored by Git. Never commit
tokens, credentials, or private validation output.

## Run tests

Inside the container:

```bash
python -m pytest -v
```

Run a particular test file with:

```bash
python -m pytest tests/test_proto.py -v
```

Tests should eventually cover:

- Protobuf generation, imports, serialization, and deserialization
- Configuration loading without printing the token
- WebSocket connection and authentication behavior
- Request and response correlation
- Retry behavior and request ID reuse
- Processing of unsolicited server messages
- Local state updates and the validation sequence

## Optional: local virtual environment

Use this only if you want to run Python directly on the host instead of Docker.
Docker remains required for the Linux validator.

Create the environment once:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On later sessions, reactivate it with:

```bash
source .venv/bin/activate
```

Leave it with:

```bash
deactivate
```

Changing directories does not deactivate a virtual environment. The `.venv`
directory is ignored by Git and will not appear when another contributor clones
the repository.

## Required validation scenario

The completed client must:

1. Receive initial state and readiness messages.
2. Advertise water in exchange for food.
3. Replace that advertisement with one seeking components.
4. Offer two water for one food.
5. Observe P02 accepting the offer.
6. Observe P02 creating a gift.
7. Accept the gift.
8. Withdraw the active advertisement.
9. Intentionally request more inventory capacity than allowed and receive the
   expected protocol error.
10. Synchronize and verify final state.

Expected final P01 inventory: **28 water, 31 food, and 31 components**.

Expected report targets: **8 messages sent**, **16 messages received**, status
`sample exchange completed`, and completion through step 10. These are
validation targets, not values to hard-code into the client.

## Next task

Implement the initial connection and receive path on a feature branch:

```bash
git switch -c feature/websocket-connection
```

The task should:

1. Load the P01 token without printing or committing it.
2. Connect to `ws://127.0.0.1:3001/ws` with the required headers and
   subprotocol from the supplied protocol documentation.
3. Send and receive binary Protobuf frames.
4. Maintain a continuous asynchronous receive loop.
5. Decode and safely log initial state and readiness messages.
6. Add tests for configuration and message decoding.

Keep connection management, message encoding/decoding, state tracking, and
scenario orchestration in separate modules rather than implementing everything
inside `main.py`.

## Git workflow

Run Git commands from the host terminal. At the end of a task:

```bash
git status
git add <files>
git commit -m "Describe the completed change"
git push -u origin HEAD
```

Open a pull request into `main`. Do not commit `.venv`, credentials, tokens,
reports, caches, or editor-specific files.

