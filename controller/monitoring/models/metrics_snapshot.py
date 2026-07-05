from dataclasses import dataclass
from datetime import datetime

from .node_metrics import NodeMetrics
from .pod_metrics import PodMetrics
from .vm_metrics import VMMetrics

@dataclass
class MetricsSnapshot:
    timestamp: datetime
    vm_metrics: dict[str, VMMetrics]
    node_metrics: dict[str, NodeMetrics]
    pod_metrics: dict[tuple[str, str, str], PodMetrics]