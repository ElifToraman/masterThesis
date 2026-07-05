from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ConnectionStatus:
    timestamp: datetime
    cluster_name: str
    host: str
    reachable: bool
    response_time_ms: float | None
    error: str | None