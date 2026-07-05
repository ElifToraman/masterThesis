from dataclasses import dataclass, field

@dataclass
class PodConfig:
    name: str
    namespace: str
    node_name: str
    uid: str | None = None