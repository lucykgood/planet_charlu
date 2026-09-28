# Repository architecture

## Runtime flow

```text
main → config → connection → session.open_session
                                  │
                   initial state → ready → readiness
                                  │
                    ClientSession background receive loop
                       ├─ state → replace WorldView
                       └─ result / protocol_error → PendingRequests
                                  │
                      strategy (trade) or scenario (validation)
                       → codec command builder → session.send
                       → connection → server
```

`main.py` owns connection and session cleanup. A session has one reader: its
background pump. Policy code sends through the session and waits on results or
snapshots rather than reading the socket itself.

## Source map

Paths below are relative to `src/planet_charlu/`.

| File | Responsibility |
| --- | --- |
| `main.py` | CLI entry point; selects trade or validation mode and cleans up the session. |
| `config.py` | CLI/environment/file configuration and token-redacted configuration display. |
| `connection.py` | Authentication, `bazaar.protobuf.v2` negotiation, binary frames, socket lifecycle. |
| `codec.py` | Protobuf encode/decode and builders for ready, sync, and trading commands. |
| `commands.py` | Request IDs and futures that correlate results/errors with awaiting callers. |
| `session.py` | Readiness handshake, receive pump, phase gating, snapshot waits, and sync. `run()` is a receive-only diagnostic helper. |
| `strategy.py` | Cooperative decision policy, attempt accounting, and continuous trading runner. |
| `scenario.py` | Fixed validator steps 2–10; step 1 is the session handshake. |
| `logging_utils.py` | Logging setup and concise server-message summaries. |
| `domain/resources.py` | Resource enum and nonnegative bundle arithmetic. |
| `domain/offers.py`, `domain/advertisements.py` | Wire-to-domain conversion, status, ownership, and expiry helpers. |
| `domain/station.py`, `domain/transactions.py`, `domain/outcomes.py` | Own-station observations, settled trades, and command outcomes. |
| `domain/world.py` | Full snapshots, trading rules, offer filtering, and commitment estimates. |
| `domain/__init__.py` | Public domain-type exports. |
| `generated/bazaar_pb2.py` | Generated wire types; regenerate from `protos/bazaar.proto`. |
| Other `__init__.py` files | Python package markers. |

## State and command invariants

- Each state is a complete snapshot. Replace the previous `WorldView`; never
  replay transactions into inventory. The pump ignores non-increasing snapshot
  sequences and rejects a changed run ID on an existing connection.
- Domain values are frozen dataclasses. Rules and directory membership come
  from the snapshot, not hard-coded peer assignments.
- An offer's `give` and `receive` are from its proposer's perspective. Accepting
  an incoming offer costs its `receive` bundle.
- Open offers do not reserve stock on the server. Commitment subtraction is a
  client estimate; the strategy also excludes offers whose expiry tick has
  arrived. `WorldView`'s generic helpers filter by open status only.
- Register a pending request before sending so a fast reply cannot be lost.
  The pump resolves results, rejects protocol errors, and fails outstanding
  requests when the connection ends. Cleanup removes cancelled pending entries.
- `send()` requires `PHASE_RUNNING`. `sync()` has no request ID and waits for
  any newer snapshot; it cannot distinguish a sync response from a concurrent
  pushed state. It is not an explicit per-command acknowledgement.
- `wait_for()` uses snapshot notifications. Its default timeout is five seconds;
  the trading runner waits indefinitely when idle and bounds command/sync waits
  at fifteen seconds. Validation command sends have no separate deadline.

## Trading policy

`CooperativeStrategy.choose(world)` returns at most one `Decision`. The runner
calls `record()` before sending, so even rejected attempts count toward local
limits. Candidate selection runs in this order:

1. Withdraw commitments that threaten upkeep reserves.
2. Accept affordable gifts, useful exchanges, or small help requests without
   worsening protected reserves.
3. Publish or refresh surplus/needs advertisements.
4. Seek reciprocal trades, prioritizing the shortest supply.
5. Offer small gifts of surplus specialty resources, rotating among partners.

The policy protects three ticks of upkeep and targets six, capped by the run's
remaining duration. Outgoing offers also retain upkeep for their lifetime.
Production is spendable only after it appears in inventory. Server rules bound
message size, expiry, outgoing offers, and command counts. Rate-limit results
postpone further attempts until the retry tick. After each result the runner
requests a fresh snapshot before deciding again; it does not retry uncertain
commands automatically.

Change policy thresholds and selection in `strategy.py`; change connection
behavior in `connection.py` or `session.py`. Keep validator-specific IDs and
expected sequencing in `scenario.py`.

## Tests and supporting files

| Path | Purpose |
| --- | --- |
| `tests/fixtures.py` | Builders for complete wire messages used across tests. |
| `tests/test_domain_*.py` | Conversion, resource arithmetic, status/expiry boundaries, and snapshot independence. |
| `tests/test_codec.py`, `tests/test_proto.py` | Command fields, wire round trips, and generated binding smoke checks. |
| `tests/test_config.py`, `tests/test_logging_utils.py`, `tests/test_main.py` | Configuration precedence/redaction, message summaries, and entry-point wiring. |
| `tests/test_commands.py` | Request correlation, duplicate pending IDs, and error delivery. |
| `tests/test_connection.py`, `tests/test_session.py` | Local WebSocket transport, handshake, phase gating, and session updates. |
| `tests/test_scenario.py` | Scripted ten-step validator exchange and failure paths. |
| `tests/test_strategy.py` | Reserve protection, policy limits, partner rotation, and a simulated nine-planet exchange. |
| `scripts/check_validation.py` | Manual assertions against a fresh real validator exercise. |
| `scripts/generate_proto.sh`, `scripts/verify_proto.sh` | Binding generation and generation/import verification. |
| `scripts/run_validator.sh` | Selects the bundled Linux binary for the current CPU and forwards options. |
| `validator/` | Supplied ARM64/x86-64 binaries and the detailed exchange specification. |
| `Dockerfile`, `compose.yaml`, `.dockerignore` | Python/Linux development environment and repository mount. |
| `requirements.txt`, `pytest.ini`, `.coveragerc` | Dependencies, test discovery/import configuration, async tests, and handwritten-code coverage. |
| `.gitignore` | Excludes local environments, caches, credentials, and validator output. |

The generated bindings and validator binaries have specific build/test purposes;
they are not unused scaffolding. Local `.venv`, caches, credentials, and reports
are outside the tracked handoff. The README contains operating instructions;
this file contains the structural map; the validator guide remains the detailed
protocol exercise reference.
