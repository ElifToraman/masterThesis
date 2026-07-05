from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class NodeMetrics:
    timestamp: datetime
    cluster_name: str

    prometheus_query_latency_ms: float

    node_name: str
    node_role: str
    prometheus_instance: str

    cpu_usage_percent: float
    cpu_core_count: int

    memory_total_bytes: int
    memory_available_bytes: int
    memory_used_bytes: int
    memory_usage_percent: float

    load_average_1m: float
    load_average_5m: float
    load_average_15m: float