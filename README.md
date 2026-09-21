# Planet Charlu — Spaceport Bazaar Client

Planet Charlu is a two-person AI Engineering project to build a client for the
Spaceport Bazaar protocol. The client connects to the course-provided local
validation server over WebSockets, exchanges binary Protocol Buffer messages,
tracks marketplace state, and completes a required ten-step trading exercise as
player **P01** while the validator controls **P02**.

The immediate goal is a small, reliable command-line client—not a website or
hosted service. Everything in the proposed stack is free and open source, and
the validator runs locally. No paid cloud account is required.

## Technology choices

- **Language:** Python 3.12
- **Concurrency:** Python `asyncio`
- **WebSocket client:** `websockets`
- **Serialization:** Google Protocol Buffers (`protobuf` and `grpcio-tools`)
- **Tests:** `pytest` and `pytest-asyncio`
- **Recommended environment:** Docker or a VS Code dev container on macOS or
  Windows, because the supplied validator is a Linux executable

FastAPI, React, AWS, Firebase, and PostgreSQL are not needed for the core
assignment. The client initiates a WebSocket connection to an existing server;
it does not need to host an HTTP API, render a browser interface, or persist data
in a database.

## Current status

- [x] Empty GitHub repository created and collaborator added
- [ ] Repository scaffolded
- [ ] Python environment and dependencies added
- [ ] Protobuf schema added
- [ ] Protocol classes generated
- [ ] Validator binary configured
- [ ] WebSocket connection implemented
- [ ] Authentication implemented
- [ ] Continuous receive loop implemented
- [ ] Ten-step validation exercise completed
- [ ] Validation report reviewed and submitted as required

Update this list as work is merged into `main`. Do not mark an item complete
until it works from a fresh clone using the instructions below.

## Planned repository layout

```text
planet_charlu/
├── README.md
├── .gitignore
├── requirements.txt
├── protos/
│   └── bazaar.proto
├── src/
│   └── planet_charlu/
│       ├── __init__.py
│       ├── main.py
│       ├── connection.py
│       ├── messages.py
│       └── generated/
│           └── __init__.py
├── tests/
├── scripts/
│   ├── generate_proto.sh
│   └── run_validator.sh
├── validator/
│   └── README.md
└── validation-credentials.example.json
```

Generated files, module names, and commands may need small adjustments after we
inspect the package declarations in `bazaar.proto`.

## Prerequisites

Install the following free tools:

- Git
- Python 3.12
- Docker Desktop or another Docker-compatible runtime if the host is not Linux
- The course-provided `bazaar.proto`
- One course-provided validator binary:
  - `spaceport-validate-linux-arm64`
  - `spaceport-validate-linux-x86_64`

The validator requires Linux with glibc 2.34 or newer and `libgcc_s.so.1`.
Running both the client and validator inside the same Linux container avoids
host-platform and networking differences.

## Clone and create the Python environment

```bash
git clone <PLANET_CHARLU_REPOSITORY_URL>
cd planet_charlu

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell outside a container, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

The initial `requirements.txt` should contain version-pinned releases of:

```text
websockets
protobuf
grpcio-tools
pytest
pytest-asyncio
```

Pin exact versions once the first working environment is confirmed so both
partners and the grader use consistent dependencies.

## Add and generate the Protobuf code

Place the supplied schema at `protos/bazaar.proto`. Do not rewrite the schema
unless the assignment explicitly instructs us to do so.

From the repository root, generate Python classes with:

```bash
python -m grpc_tools.protoc \
  -I protos \
  --python_out=src/planet_charlu/generated \
  protos/bazaar.proto
```

The planned `scripts/generate_proto.sh` should wrap this command so regeneration
is consistent. After generating, confirm the module imports successfully:

```bash
PYTHONPATH=src python -c "from planet_charlu.generated import bazaar_pb2"
```

We plan to commit the generated Python module unless the course instructions
prohibit it. Committing it lets a partner run the client immediately while the
generation script keeps the output reproducible.

## Select the validator binary

The validator is supplied in two Linux builds. Inside the Linux environment
where it will run, check the architecture:

```bash
uname -m
```

Use the matching binary:

| `uname -m` result | Validator |
|---|---|
| `x86_64` or `amd64` | `spaceport-validate-linux-x86_64` |
| `aarch64` or `arm64` | `spaceport-validate-linux-arm64` |

Place the matching file in `validator/` and make it executable:

```bash
chmod +x validator/spaceport-validate-linux-x86_64
```

Substitute the ARM64 filename when appropriate. Whether the binaries themselves
belong in Git must be confirmed against course redistribution rules and GitHub's
file-size limits. If they are not committed, `validator/README.md` must tell each
partner where to obtain them.

## Local credentials

Copy the committed example file:

```bash
cp validation-credentials.example.json validation-credentials.json
```

Then add the P01 token supplied by the validator or course materials. Never
commit the real token. These entries must be present in `.gitignore`:

```gitignore
.venv/
__pycache__/
.pytest_cache/
*.py[cod]
.env
validation-credentials.json
validation-report.json
```

## Start the validator

The final wrapper command will be:

```bash
./scripts/run_validator.sh
```

Until that script exists, run the matching binary directly from the repository
root. The validator must use the Protobuf codec:

```bash
./validator/spaceport-validate-linux-x86_64 --codec protobuf
```

The expected client endpoint is:

```text
ws://127.0.0.1:3001/ws
```

The validator may support flags for the address, credentials filename, or report
filename. Check its `--help` output before encoding those options in the wrapper:

```bash
./validator/spaceport-validate-linux-x86_64 --help
```

Keep this terminal running while using the client.

## Run the client

In a second terminal, activate the same Python environment and run:

```bash
source .venv/bin/activate
PYTHONPATH=src python -m planet_charlu.main
```

The client will eventually:

1. Read P01 credentials from local configuration.
2. Connect to `ws://127.0.0.1:3001/ws` with the required headers and WebSocket
   subprotocol.
3. Send and receive binary Protobuf frames.
4. Run a continuous receive loop for asynchronous server messages.
5. Track `run_id`, `request_id`, object IDs, snapshot sequence, world version,
   tick, and expiration tick values.
6. Execute the required ten-step validation exercise.

The exact authentication header and subprotocol values must come from the
course schema, README, or validator output; do not guess them or hard-code a
secret in source control.

## Run tests

Run the complete test suite from the repository root:

```bash
PYTHONPATH=src pytest -v
```

The first smoke tests should verify that:

- generated Protobuf classes import correctly;
- representative messages serialize and deserialize;
- configuration loads without logging the token;
- request IDs are correlated with responses;
- retry logic reuses the exact request ID only when retrying the same request;
- the receive loop can process unsolicited server messages.

## Required validation scenario

The completed client must perform the validator's ten-step scenario:

1. Receive the initial state and readiness messages.
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

The expected final P01 inventory is **28 water, 31 food, and 31 components**.
The validation report is expected to indicate **8 messages sent**, **16 messages
received**, status `sample exchange completed`, and completion through step 10.
Treat those numbers as validation targets, not values to hard-code into the
client's behavior.

## Recommended next task

The next contributor should implement the initial connection and receive path on
a feature branch:

```bash
git switch -c feature/websocket-connection
```

Suggested scope:

1. Confirm generated Protobuf types and the required authentication fields.
2. Load the P01 token from `validation-credentials.json` or an environment
   variable without printing it.
3. Connect to the local validator endpoint with the required headers and
   subprotocol.
4. Keep an asynchronous receive loop running.
5. Decode and log the message type and safe identifiers from initial
   state/readiness messages.
6. Add unit tests for configuration and message decoding.
7. Update **Current status**, push the branch, and open a pull request.

Do not begin by implementing all ten trading steps in `main.py`. Keep connection,
message encoding/decoding, state tracking, and scenario orchestration separate so
both partners can work without repeatedly editing the same file.

## Git workflow

Start each task from an updated `main` branch:

```bash
git switch main
git pull --ff-only
git switch -c feature/short-description
```

Commit focused changes, push the branch, and open a pull request:

```bash
git add <files>
git commit -m "Implement short description"
git push -u origin feature/short-description
```

Do not commit credentials, tokens, validation reports, virtual environments, or
editor-specific files.

## Open questions

Resolve these before implementation is considered stable:

- Are validator binaries permitted in the shared GitHub repository?
- Will both partners use the same Docker/dev-container environment?
- What exact authentication header and WebSocket subprotocol does the schema or
  validator require?
- Are generated Protobuf Python files expected to be committed?
- What files must be included in the final course submission?
- Does the instructor require a particular Python version, dependency format, or
  test command?

