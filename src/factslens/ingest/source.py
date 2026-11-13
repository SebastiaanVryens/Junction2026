"""The interface every data source implements, so platforms plug into the same pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from factslens.common.models import Event


@dataclass(frozen=True, slots=True)
class SourceItem:
    """One message from a source. `event` is None when the message isn't something we ingest;
    the cursor still advances so a restart doesn't re-read it."""

    cursor: int
    event: Event | None


class Source(Protocol):
    name: str

    def items(self, cursor: int | None) -> AsyncIterator[SourceItem]:
        """Yield items forever, starting just after `cursor` (or from "now" when None).
        Implementations reconnect on their own and may re-deliver a few items."""
        ...
