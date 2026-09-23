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
- [x] Domain model (bundles, offers, advertisements, transactions, commitments)
- [x] Trading command builders (`advertise`/`offer`/`accept`/`withdraw`) and request ID correlation
- [x] Phase gating and `WorldView` wired into a live session (`ClientSession`)
- [x] Ten-step validation exercise scripted and running as part of the client
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
│       ├── commands.py
│       ├── session.py
│       ├── logging_utils.py
│       ├── main.py
│       ├── scenario.py
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── resources.py
│       │   ├── offers.py
│       │   ├── advertisements.py
│       │   ├── transactions.py
│       │   ├── station.py
│       │   ├── outcomes.py
│       │   └── world.py
│       └── generated/
│           ├── __init__.py
│           └── bazaar_pb2.py
├── tests/
│   ├── fixtures.py
│   ├── test_config.py
│   ├── test_codec.py
│   ├── test_connection.py
│   ├── test_commands.py
│   ├── test_logging_utils.py
│   ├── test_main.py
│   ├── test_proto.py
│   ├── test_scenario.py
│   ├── test_session.py
│   ├── test_domain_resources.py
│   ├── test_domain_offers.py
│   ├── test_domain_advertisements.py
│   ├── test_domain_transactions.py
│   ├── test_domain_station.py
│   ├── test_domain_outcomes.py
│   └── test_domain_world.py
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
| `codec.py` | Binary Protobuf encode/decode and building `ClientMessage` payloads (`ready`, `sync`, `advertise`, `offer`, `accept`, `withdraw`). |
| `connection.py` | The WebSocket lifecycle: connecting with the `Authorization` header and `bazaar.protobuf.v2` subprotocol, verifying the server selected it, sending, and a `messages()` async generator that decodes each binary frame as a `ServerMessage`. |
| `commands.py` | Request ID correlation: `PendingRequests` tracks in-flight commands as futures keyed by `request_id` and resolves them from either a successful `result` or a failing `protocol_error` (`ProtocolErrorReceived`); `send_command()` sends a built message and awaits the matching outcome. |
| `session.py` | Scenario orchestration: the required connect → read state → send ready → await readiness sequence (`perform_readiness_handshake`); `run()`, a continuous receive loop that just logs; and `ClientSession`/`open_session()`, a live session that tracks the current `WorldView`, gates sends on `is_running()`, and correlates commands via `commands.py` as its background pump processes incoming messages. |
| `logging_utils.py` | Turning a decoded `ServerMessage` into a one-line, safe-to-log summary (never touches the token, which lives only in the connection header). |
| `scenario.py` | `run_sample_scenario(session)`: drives steps 2-10 of the guided exercise on top of an already-open `ClientSession` (step 1 is `open_session()`'s job), following `validator/README.md`'s request IDs and ordering exactly, and raises `ScenarioError` if a server response stops matching the documented exercise. |
| `main.py` | Wiring: load config, open a `ClientSession`, run `run_sample_scenario`, translate `ConfigError`/`ScenarioError`/`KeyboardInterrupt` into a clean exit. |

This slice implements the required connection sequence through readiness
(steps 1-4 of the brief's five-step sequence). `codec.py` can build all four
trading commands, and `session.ClientSession` ties everything built so far
together into a usable live session: `open_session(connection)` completes
the handshake, then starts a pump task that keeps `session.world` (a
`WorldView`) current from every incoming `state`, resolves outgoing
commands' `PendingRequests` from `result`/`protocol_error` messages, and
`session.send(request_id=..., message=...)` refuses to send unless the
latest `WorldView.is_running()` is true. `main.py` now drives the full
ten-step exercise through `scenario.run_sample_scenario(client_session)`
rather than the simpler `session.run()` receive loop, and has been verified
end-to-end against a live validator run.

## Domain model

`src/planet_charlu/domain/` turns raw `bazaar_pb2` wire messages into small,
immutable Python value objects, so trading logic reads `offer.is_open()`
instead of comparing against a raw `OFFER_STATUS_OPEN` int, and never
repeats resource arithmetic inline. Every type has a `from_wire()`
classmethod and no other way to construct one from server data.

| Type (`domain/...`) | Wraps | Adds |
| --- | --- | --- |
| `Bundle` (`resources.py`) | `bazaar_pb2.Bundle` | Non-negative-only arithmetic: `covers()`, `minus()` (raises if unaffordable), `saturating_subtract()` (clamps at zero for estimates). |
| `Resource` (`resources.py`) | `bazaar_pb2.Resource` | A Python enum (`WATER`/`FOOD`/`COMPONENTS`) instead of a raw int. |
| `Offer` (`offers.py`) | `bazaar_pb2.Offer` | `is_open()`, `is_gift()`, `is_expired_by(tick)`, `proposed_by()`/`directed_to()`. |
| `Advertisement` (`advertisements.py`) | `bazaar_pb2.Advertisement` | `is_active()`, `is_help_request()`, `is_expired_by(tick)`. |
| `Transaction` (`transactions.py`) | `bazaar_pb2.Transaction` | `involves(station_id)`. |
| `StationSelf` (`station.py`) | `bazaar_pb2.StationObservation` | `had_full_upkeep_last_tick()`; keeps only the fields reserve-protection/survival logic needs today. |
| `CommandOutcome` (`outcomes.py`) | `bazaar_pb2.Result` | `code_name` for logging; unwraps the `Nullable*` wire fields to plain `Optional[...]`. |
| `WorldView` (`world.py`) | a whole `bazaar_pb2.State` | `is_running()`, `open_offers_from_me()`/`open_offers_to_me()`, `committed_bundle()`, `available_bundle()`. |

**`WorldView` is the snapshot tracker.** It's built fresh from a whole
`State` via `WorldView.from_state(state)` and never mutated — a newer
snapshot means calling `from_state` again and replacing the caller's
reference, not patching fields on an existing instance. That structurally
rules out the bug the brief warns about most: re-applying a transaction a
new snapshot's inventory already includes, since there's no in-place update
path that could double-apply anything.

**Commitments, not reservations.** The server does not reserve stock when
an offer is posted ("no reservation... multiple offers can promise the same
stock"), so `WorldView.committed_bundle()` is our own bookkeeping — the sum
of `give` bundles across our own currently open offers — and
`available_bundle()` is `inventory.saturating_subtract(committed_bundle())`.
It's an estimate we maintain client-side, not a server-verified balance.

This does not yet include request ID correlation (matching a `result` back
to the command that caused it) or phase gating (blocking trading commands
unless `phase == PHASE_RUNNING`) — both need the client to be sending
trading commands first, which is the next branch's job.

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
INFO planet_charlu.session: initial state seq=1 world_version=2 tick=0 phase=PHASE_RUNNING self=P01 health=100 specialty=RESOURCE_WATER inventory=(water=30,food=30,components=30)
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
  falsy values), and that malformed bytes raise `DecodeError`. Each of the
  four trading command builders (`build_advertise`/`build_offer`/
  `build_accept`/`build_withdraw`) has a test asserting its required fields
  are set correctly.
- `commands.py`: `PendingRequests.register`/`resolve`/`reject` in isolation
  (a registered future resolves with the matching outcome or rejects with a
  `ProtocolErrorReceived`, a duplicate `request_id` raises, an unknown or
  already-resolved `request_id` is ignored rather than raising), and
  `send_command` end-to-end against a fake connection.
- `connection.py`: the `Authorization` header and subprotocol are sent and
  verified against a real local WebSocket server (`websockets.serve`, no
  validator binary needed), a subprotocol mismatch raises before any data is
  exchanged, binary frames round-trip, an unexpected text frame is rejected,
  and calling `send`/`messages` before `connect` fails clearly.
- `session.py`: the readiness handshake in isolation (wrong first message,
  wrong second message, mismatched `run_id`, mismatched readiness
  `snapshot_sequence`) and the full `run()` loop end-to-end against a fake
  server. One test pins the validator's exact Step 1 scenario end-to-end —
  station `P01`, inventory `(30,30,30)`, specialty water, P02 advertising
  food for water — decoded through `WorldView`, so a regression in either
  the handshake or the domain model fails a test instead of only showing up
  by eye against a live validator. `ClientSession`/`open_session()` are
  covered separately: `world` updates as new `state` messages arrive on the
  pump, `send()` raises `NotRunningError` when the latest phase isn't
  RUNNING, a successful command's `result` resolves `send()`'s return value,
  and a `protocol_error` for the same `request_id` raises
  `ProtocolErrorReceived` instead.
- `logging_utils.py`: one summary per message kind, including the unset
  case; the `state` summary asserts on the specialty and inventory fields
  it now includes.
- `main.py`: the `ConfigError` → exit-1 path, the happy path wiring
  `load_config` into `session.run`, and swallowing `KeyboardInterrupt`.
- `domain/resources.py`: `Bundle` arithmetic (`covers`/`minus`/
  `saturating_subtract`), negative-quantity rejection, and `Resource`
  wire round trips.
- `domain/offers.py`, `domain/advertisements.py`: `from_wire` mapping,
  status checks, gift/help-request detection, and the exclusive
  expiry-tick boundary (`is_expired_by(expires_tick)` is `True`, one tick
  earlier is `False`).
- `domain/transactions.py`, `domain/station.py`, `domain/outcomes.py`:
  `from_wire` field mapping and each type's small helper methods.
- `domain/world.py`: building a `WorldView` from a full `State` fixture,
  filtering open offers by proposer/recipient, `committed_bundle()` summing
  only our own open offers, and `available_bundle()` clamping at zero when
  overcommitted. Also checked live against a real snapshot from the
  validator (correctly decoded P02's real advertisement into `Resource`
  enum values).

Not yet covered, because the underlying feature does not exist yet: retries
on `REQUEST_ID_CONFLICT`, and a fixture-based test confirming a
repeated/duplicate snapshot doesn't double-count a transaction. Those are
next on this branch.

- `scenario.py`: `run_sample_scenario` end-to-end against a scripted fake
  server that reproduces the validator's exact ten-step sample exchange
  (`test_run_sample_scenario_matches_validator_spec`, asserting the final
  `WorldView` matches the documented inventory/version/transaction counts),
  plus two failure-path tests (`ScenarioError` when a step's `result` isn't
  `ok`, and when the deliberately-erroring step 9 succeeds instead of
  failing).

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
authentication, codec, and readiness handshake (see
[Client architecture](#client-architecture)); step 1 of the required
validation scenario passes against the real validator binary.

`feature/domain-model` added `src/planet_charlu/domain/` (see
[Domain model](#domain-model)): `Bundle`/`Offer`/`Advertisement`/
`Transaction`/`StationSelf`/`CommandOutcome` value types and a `WorldView`
snapshot tracker with commitment accounting, all tested against both
fixtures and a real snapshot from the validator.

`feature/trading-commands` added command builders for
`advertise`/`offer`/`accept`/`withdraw` in `codec.py`, and `commands.py`'s
`PendingRequests`/`send_command()` for correlating a sent command with its
async `result`. Verified manually against a live validator run through all
ten steps of the documented sample scenario.

`feature/scenario-orchestration` (in progress) has so far added, on top of
that:

- `commands.py`: `PendingRequests.reject()` and `ProtocolErrorReceived`, so
  a command answered with a `protocol_error` instead of a `result` (e.g.
  the required intentional-error validation step) fails its awaiter with an
  exception instead of hanging forever.
- `session.py`: `ClientSession`/`open_session()` — a live session whose
  background pump task keeps `session.world` (a `WorldView`) current from
  every incoming `state`, resolves or rejects outgoing commands'
  `PendingRequests` from `result`/`protocol_error` messages, and whose
  `send()` refuses to send unless the latest `WorldView.is_running()` is
  true. Verified manually against a live validator through all ten steps of
  the sample scenario, using `ClientSession` instead of hand-rolled message
  pumping.

Still to do on this branch:

1. Retry-with-same-`request_id` and `REQUEST_ID_CONFLICT` handling on top
   of `PendingRequests`.
2. The actual ten-step scenario driver, built on `ClientSession.send()` and
   `session.world`, and wired into `main.py` so the real client (not just a
   manual script) runs it.
3. Fixture-based tests confirming a repeated/duplicate snapshot doesn't
   double-count a transaction, and an automated integration test running
   the full ten-step exercise against the validator, checking the
   documented final inventory (28 water, 31 food, 31 components) — turning
   the manual verification above into something CI/the test suite can run.
4. A first explainable policy (protect upkeep reserves using
   `WorldView.available_bundle()`, discover suppliers via advertisements).

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

