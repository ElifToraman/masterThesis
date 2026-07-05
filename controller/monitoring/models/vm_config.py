from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class VMConfig:
    name: str
    host: str
    ssh_user: str
    ssh_key: Path
    prometheus_url: str

    ssh_port: int = 22
    connection_timeout_seconds: int = 5