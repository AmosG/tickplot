from enum import Enum, auto
from typing import Protocol, Any

class InstrumentType(Enum):
    STOCK = auto()
    FUTURE = auto()
    OPTION = auto()

class CollectorStatus(Enum):
    RUNNING = auto()
    RETRYING = auto()
    STOPPED = auto()
    ERROR = auto()

class CollectorProtocol(Protocol):
    symbol: str
    instrument_type: InstrumentType
    status: CollectorStatus
    async def run(self) -> None: ...
    def stop(self) -> None: ... 