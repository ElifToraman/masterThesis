from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from .pod import Pod
from .models import NodeConfig, NodeMetrics, PodConfig

if TYPE_CHECKING:
    from vm import VM


class Node:
    def __init__(
        self,
        config: NodeConfig,
        vm: VM,
    ) -> None:
        self.config = config
        self.vm = vm

    def gather_metrics(self) -> NodeMetrics:
        instance = self.config.prometheus_instance

        queries = {
            "cpu_usage": f"""
                100 * (
                    1 -
                    avg(
                        sum by (cpu) (
                            rate(
                                node_cpu_seconds_total{{
                                    instance="{instance}",
                                    mode=~"idle|iowait"
                                }}[1m]
                            )
                        )
                    )
                )
            """,
            "memory_total": (
                f'node_memory_MemTotal_bytes{{instance="{instance}"}}'
            ),
            "memory_available": (
                f'node_memory_MemAvailable_bytes{{instance="{instance}"}}'
            ),
            "cpu_core_count": f"""
                count(
                    count by (cpu) (
                        node_cpu_seconds_total{{
                            instance="{instance}",
                            mode="idle"
                        }}
                    )
                )
            """,
            "load_1m": f'node_load1{{instance="{instance}"}}',
            "load_5m": f'node_load5{{instance="{instance}"}}',
            "load_15m": f'node_load15{{instance="{instance}"}}',
        }

        query_started_at = time.perf_counter()

        results = self.vm.query_prometheus_many(queries)

        prometheus_query_latency_ms = (
            time.perf_counter() - query_started_at
        ) * 1000

        cpu_usage_percent = self._extract_single_value(
            results["cpu_usage"],
            "cpu_usage",
        )

        memory_total_bytes = int(
            self._extract_single_value(
                results["memory_total"],
                "memory_total",
            )
        )

        memory_available_bytes = int(
            self._extract_single_value(
                results["memory_available"],
                "memory_available",
            )
        )

        memory_used_bytes = (
            memory_total_bytes - memory_available_bytes
        )

        memory_usage_percent = (
            memory_used_bytes / memory_total_bytes * 100
            if memory_total_bytes > 0
            else 0.0
        )

        return NodeMetrics(
            timestamp=datetime.now(timezone.utc),
            cluster_name=self.vm.config.name,
            prometheus_query_latency_ms=round(prometheus_query_latency_ms, 2),
            node_name=self.config.name,
            node_role=self.config.role,
            prometheus_instance=instance,
            cpu_usage_percent=round(cpu_usage_percent, 2),
            cpu_core_count=int(
                self._extract_single_value(
                    results["cpu_core_count"],
                    "cpu_core_count",
                )
            ),
            memory_total_bytes=memory_total_bytes,
            memory_available_bytes=memory_available_bytes,
            memory_used_bytes=memory_used_bytes,
            memory_usage_percent=round(
                memory_usage_percent,
                2,
            ),
            load_average_1m=self._extract_single_value(
                results["load_1m"],
                "load_1m",
            ),
            load_average_5m=self._extract_single_value(
                results["load_5m"],
                "load_5m",
            ),
            load_average_15m=self._extract_single_value(
                results["load_15m"],
                "load_15m",
            ),
        )
    def _extract_single_value(
        self,
        result: list[dict[str, Any]],
        metric_name: str,
    ) -> float:
        if len(result) != 1:
            raise RuntimeError(
                f"Expected one result for {metric_name} on "
                f"{self.config.name}, but received {len(result)}"
            )

        value = result[0].get("value")

        if not isinstance(value, list) or len(value) != 2:
            raise RuntimeError(
                f"Invalid value for {metric_name} on "
                f"{self.config.name}: {value!r}"
            )

        try:
            return float(value[1])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Non-numeric value for {metric_name} on "
                f"{self.config.name}: {value[1]!r}"
            ) from exc

    def discover_pods(self) -> list[Pod]:
        node_name = self.config.name

        result = self.vm.query_prometheus(
            f'kube_pod_info{{node="{node_name}"}}'
        )

        discovered: dict[tuple[str, str], Pod] = {}

        for item in result:
            metric = item.get("metric", {})

            if not isinstance(metric, dict):
                continue

            pod_name = metric.get("pod")
            namespace = metric.get("namespace")
            pod_uid = metric.get("uid")

            if not pod_name or not namespace:
                continue

            key = (str(namespace), str(pod_name))

            discovered[key] = Pod(
                config=PodConfig(
                    name=str(pod_name),
                    namespace=str(namespace),
                    node_name=node_name,
                    uid=str(pod_uid) if pod_uid else None,
                ),
                node=self,
            )

        return sorted(
            discovered.values(),
            key=lambda pod: (
                pod.config.namespace,
                pod.config.name,
            ),
        )