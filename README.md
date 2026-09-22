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
- [x] WebSocket connection implemented
- [x] Authentication implemented
- [x] Continuous receive loop implemented
- [x] Initial state and readiness messages decoded and safely logged
- [ ] Domain model (offers, advertisements, transactions, commitments)
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
│       ├── config.py
│       ├── codec.py
│       ├── connection.py
│       ├── session.py
│       ├── logging_utils.py
│       ├── main.py
│       └── generated/
│           ├── __init__.py
│           └── bazaar_pb2.py
├── tests/
│   ├── fixtures.py
│   ├── test_config.py
│   ├── test_codec.py
│   ├── test_connection.py
│   ├── test_logging_utils.py
│   ├── test_main.py
│   ├── test_proto.py
│   └── test_session.py
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

## Client architecture

Each module owns one responsibility, so state tracking and decision logic
never depend on network calls, and each layer can be tested without a live
socket:

| Module | Owns |
| --- | --- |
| `config.py` | Resolving the endpoint, station ID, and token from CLI flags, environment variables, or `validation-credentials.json`. Redacts the token from `repr`/`str`. |
| `codec.py` | Binary Protobuf encode/decode and building `ClientMessage` payloads (`ready`, `sync`). |
| `connection.py` | The WebSocket lifecycle: connecting with the `Authorization` header and `bazaar.protobuf.v2` subprotocol, verifying the server selected it, sending, and a `messages()` async generator that decodes each binary frame as a `ServerMessage`. |
| `session.py` | Scenario orchestration: the required connect → read state → send ready → await readiness sequence (`perform_readiness_handshake`), then a continuous receive loop (`run`). |
| `logging_utils.py` | Turning a decoded `ServerMessage` into a one-line, safe-to-log summary (never touches the token, which lives only in the connection header). |
| `main.py` | Wiring: load config, run the session, translate `ConfigError`/`KeyboardInterrupt` into a clean exit. |

This slice implements the required connection sequence through readiness
(steps 1-4 of the brief's five-step sequence) and a continuous receive loop
that logs every subsequent message. It intentionally does not yet send
trading commands (`advertise`/`offer`/`accept`/`withdraw`) or track offers,
advertisements, and transactions as domain objects — that is the next branch,
building on `BazaarConnection.messages()` and `session.run()` as the
foundation.

## Configuration

No source edit is needed to point the client at a different server or
station. Every setting can be set by CLI flag or environment variable (CLI
flags win):

| Setting | CLI flag | Environment variable | Default |
| --- | --- | --- | --- |
| WebSocket endpoint | `--ws-url` | `BAZAAR_WS_URL` | `ws://127.0.0.1:3001/ws` |
| Bearer token | `--token` | `BAZAAR_TOKEN` | *(none; falls back to the credentials file)* |
| Credentials file | `--credentials-file` | `BAZAAR_CREDENTIALS_FILE` | `validation-credentials.json` |
| Station ID | `--station-id` | `BAZAAR_STATION_ID` | `P01` |

If `--token`/`BAZAAR_TOKEN` is not given, the client reads the token for
`--station-id` out of the credentials file's `players` list — the same file
the validator writes. The token is never printed: `ClientConfig.__repr__`
always shows `token='***redacted***'`.

Example: pointing at a different port without touching any source file:

```bash
python -m planet_charlu.main --ws-url ws://127.0.0.1:3002/ws --token "$BAZAAR_TOKEN"
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

Expect log lines like this, confirming the connection, the initial state, and
the readiness handshake:

```text
INFO __main__: starting Planet CharLu client: ClientConfig(ws_url='ws://127.0.0.1:3001/ws', station_id='P01', token='***redacted***')
INFO planet_charlu.connection: connected to ws://127.0.0.1:3001/ws (subprotocol=bazaar.protobuf.v2)
INFO planet_charlu.session: initial state seq=1 world_version=2 tick=0 phase=PHASE_RUNNING self=P01 health=100
INFO planet_charlu.session: received readiness run_id=<run id> ready=True seq=1
INFO planet_charlu.session: readiness confirmed for run_id=<run id>; entering continuous receive loop
```

Cross-check `validation-report.json` in the validator's working directory:
it should independently show `"last_completed_step": 1` and a matched
`ready`/`readiness` exchange. The report proves the server's view of what
was sent; the log lines above prove the client decoded and understood it —
neither one alone is sufficient evidence.

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

Measure coverage on handwritten code with:

```bash
python -m pytest --cov=planet_charlu --cov-report=term-missing
```

`.coveragerc` excludes `src/planet_charlu/generated/` from the report: it is
machine-generated by `protoc` from `bazaar.proto`, not handwritten, and a
coverage percentage there would say nothing about the client's correctness.
`tests/test_proto.py` still smoke-tests that the generated bindings import
and expose message classes.

Covered so far, module by module:

- `config.py`: CLI/env/default precedence, reading a token from a credentials
  file, missing/malformed/empty-token failure paths, and token redaction.
- `codec.py`: encode/decode round trips, that `false`/`0` survive (proto2
  required fields, unlike proto3 optional fields, always serialize even
  falsy values), and that malformed bytes raise `DecodeError`.
- `connection.py`: the `Authorization` header and subprotocol are sent and
  verified against a real local WebSocket server (`websockets.serve`, no
  validator binary needed), a subprotocol mismatch raises before any data is
  exchanged, binary frames round-trip, an unexpected text frame is rejected,
  and calling `send`/`messages` before `connect` fails clearly.
- `session.py`: the readiness handshake in isolation (wrong first message,
  wrong second message, mismatched `run_id`) and the full `run()` loop
  end-to-end against a fake server.
- `logging_utils.py`: one summary per message kind, including the unset case.
- `main.py`: the `ConfigError` → exit-1 path, the happy path wiring
  `load_config` into `session.run`, and swallowing `KeyboardInterrupt`.

Not yet covered, because the underlying feature does not exist yet: request
ID correlation/retries, offer/advertisement/transaction domain objects, and
the full ten-step validation sequence. Those land with the domain-model and
decision-policy branches.

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

`feature/websocket-connection` implemented and validated the connection,
authentication, codec, and readiness handshake described above (see
[Client architecture](#client-architecture)); step 1 of the required
validation scenario passes against the real validator binary.

The next feature branch should build the domain model and drive steps 2-10:

1. Domain types for bundles, offers, advertisements, transactions, and
   commitments, kept separate from the raw `bazaar_pb2` wire types (per the
   assignment brief, trading logic should not repeatedly unpack transport
   fields or rebuild resource arithmetic).
2. Request ID generation/correlation, so a `result` can be matched back to
   the command that produced it, including retry-with-same-ID and
   `REQUEST_ID_CONFLICT` handling.
3. Snapshot replacement by `snapshot_sequence`/`world_version`, keeping
   server facts separate from local pending-command state (a newer snapshot
   already includes settled trades; it must not be re-applied).
4. Scenario orchestration for the ten-step exercise (`advertise`, `offer`,
   `accept`, `withdraw`, the intentional `RATE_LIMITED`/capacity error, and
   final `sync`), building on `session.run()`/`BazaarConnection.messages()`.
5. Fixture-based tests for state handling (repeated snapshots must not
   double-count trades) and integration tests running the full ten-step
   exercise against the validator.

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

