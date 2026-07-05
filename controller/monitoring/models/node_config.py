from dataclasses import dataclass
from pathlib import Path


@dataclass
class NodeConfig:
    name: str
    role: str
    prometheus_instance: str