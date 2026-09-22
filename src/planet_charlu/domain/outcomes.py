"""CommandOutcome: the result of one previously-sent command.

A thin wrapper, not a rewrite -- ``code`` stays the raw wire ``ResultCode``
int rather than a duplicated Python enum, since callers naturally compare it
against ``bazaar_pb2.RESULT_CODE_*`` constants and a second parallel enum
would just be another thing to keep in sync with the .proto file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from planet_charlu.generated import bazaar_pb2


@dataclass(frozen=True)
class CommandOutcome:
    request_id: str
    ok: bool
    code: int
    object_id: Optional[str]
    transaction_id: Optional[str]
    retry_after_tick: Optional[int]

    @classmethod
    def from_wire(cls, wire: bazaar_pb2.Result) -> "CommandOutcome":
        return cls(
            request_id=wire.request_id,
            ok=wire.ok,
            code=wire.code,
            object_id=None if wire.object_id.null else wire.object_id.value,
            transaction_id=None if wire.transaction_id.null else wire.transaction_id.value,
            retry_after_tick=(
                None if wire.retry_after_tick.null else wire.retry_after_tick.value
            ),
        )

    @property
    def code_name(self) -> str:
        return bazaar_pb2.ResultCode.Name(self.code)
