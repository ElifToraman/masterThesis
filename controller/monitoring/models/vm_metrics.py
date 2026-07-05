from dataclasses import dataclass
from datetime import datetime


@dataclass
class VMMetrics:
    timestamp: datetime
    cluster_name: str
    host: str
    hostname: str

    ssh_latency_ms: float


    cpu_usage_percent: float

    memory_total_bytes: int
    memory_available_bytes: int
    memory_used_bytes: int
    memory_usage_percent: float

    load_average_1m: float
    load_average_5m: float
    load_average_15m: float

    cpu_core_count: int