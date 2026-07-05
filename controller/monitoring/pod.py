from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import PodConfig, PodMetrics

if TYPE_CHECKING:
    from node import Node


class Pod:
    def __init__(
        self,
        config: PodConfig,
        node: Node,
    ) -> None:
        self.config = config
        self.node = node

    @property
    def vm(self):
        return self.node.vm

    def _query_single_value(
        self,
        promql: str,
        *,
        default: float | None = None,
    ) -> float:
        result = self.vm.query_prometheus(promql)

        if not result:
            if default is not None:
                return default

            raise RuntimeError(
                f"No Prometheus result for pod "
                f"{self.config.namespace}/{self.config.name}. "
                f"Query: {promql}"
            )

        if len(result) != 1:
            raise RuntimeError(
                f"Expected one Prometheus result for pod "
                f"{self.config.namespace}/{self.config.name}, "
                f"but received {len(result)}. Query: {promql}"
            )

        value = result[0].get("value")

        if not isinstance(value, list) or len(value) != 2:
            raise RuntimeError(
                f"Invalid Prometheus value for pod "
                f"{self.config.namespace}/{self.config.name}: "
                f"{value!r}"
            )

        try:
            return float(value[1])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Prometheus returned a non-numeric value for pod "
                f"{self.config.namespace}/{self.config.name}: "
                f"{value[1]!r}"
            ) from exc

    def gather_metrics(self) -> PodMetrics:
        namespace = self.config.namespace
        pod_name = self.config.name

        container_filter = (
            f'namespace="{namespace}",'
            f'pod="{pod_name}",'
            'container!="",'
            'container!="POD"'
        )

        queries = {
            "cpu": f"""
                sum(
                    rate(
                        container_cpu_usage_seconds_total{{
                            {container_filter}
                        }}[1m]
                    )
                )
            """,
            "memory_working_set": f"""
                sum(
                    container_memory_working_set_bytes{{
                        {container_filter}
                    }}
                )
            """,
            "memory_rss": f"""
                sum(
                    container_memory_rss{{
                        {container_filter}
                    }}
                )
            """,
            "network_receive": f"""
                sum(
                    rate(
                        container_network_receive_bytes_total{{
                            namespace="{namespace}",
                            pod="{pod_name}"
                        }}[1m]
                    )
                )
            """,
            "network_transmit": f"""
                sum(
                    rate(
                        container_network_transmit_bytes_total{{
                            namespace="{namespace}",
                            pod="{pod_name}"
                        }}[1m]
                    )
                )
            """,
            "container_count": f"""
                count(
                    container_last_seen{{
                        {container_filter}
                    }}
                )
            """,
        }

        query_started_at = time.perf_counter()

        results = self.vm.query_prometheus_many(queries)

        prometheus_query_latency_ms = (
            time.perf_counter() - query_started_at
        ) * 1000

        cpu_usage_cores = self._extract_value_or_default(
            results["cpu"],
            default=0.0,
        )

        memory_usage_bytes = int(
            self._extract_value_or_default(
                results["memory_working_set"],
                default=0.0,
            )
        )

        memory_rss_bytes = int(
            self._extract_value_or_default(
                results["memory_rss"],
                default=0.0,
            )
        )

        network_receive = self._extract_value_or_default(
            results["network_receive"],
            default=0.0,
        )

        network_transmit = self._extract_value_or_default(
            results["network_transmit"],
            default=0.0,
        )

        container_count = int(
            self._extract_value_or_default(
                results["container_count"],
                default=0.0,
            )
        )

        return PodMetrics(
            timestamp=datetime.now(timezone.utc),
            cluster_name=self.vm.config.name,
            prometheus_query_latency_ms=round(prometheus_query_latency_ms, 2),
            namespace=namespace,
            pod_name=pod_name,
            node_name=self.config.node_name,
            cpu_usage_cores=round(cpu_usage_cores, 6),
            cpu_usage_millicores=round(
                cpu_usage_cores * 1000,
                2,
            ),
            memory_usage_bytes=memory_usage_bytes,
            memory_rss_bytes=memory_rss_bytes,
            network_receive_bytes_per_second=round(
                network_receive,
                2,
            ),
            network_transmit_bytes_per_second=round(
                network_transmit,
                2,
            ),
            container_count=container_count,
        )

    def _extract_value_or_default(
        self,
        result: list[dict[str, object]],
        *,
        default: float,
    ) -> float:
        if not result:
            return default

        if len(result) != 1:
            raise RuntimeError(
                f"Expected one result for pod "
                f"{self.config.namespace}/{self.config.name}, "
                f"but received {len(result)}"
            )

        value = result[0].get("value")

        if not isinstance(value, list) or len(value) != 2:
            raise RuntimeError(
                f"Invalid Prometheus value for pod "
                f"{self.config.namespace}/{self.config.name}: "
                f"{value!r}"
            )

        try:
            return float(value[1])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Non-numeric Prometheus value for pod "
                f"{self.config.namespace}/{self.config.name}: "
                f"{value[1]!r}"
            ) from exc