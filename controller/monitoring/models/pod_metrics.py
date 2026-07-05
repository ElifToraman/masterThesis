from dataclasses import dataclass
from datetime import datetime


@dataclass
class PodMetrics:
    timestamp: datetime
    cluster_name: str

    prometheus_query_latency_ms: float

    namespace: str
    pod_name: str
    node_name: str

    cpu_usage_cores: float
    cpu_usage_millicores: float

    memory_usage_bytes: int
    memory_rss_bytes: int

    network_receive_bytes_per_second: float
    network_transmit_bytes_per_second: float

    container_count: int