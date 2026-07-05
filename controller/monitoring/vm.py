from __future__ import annotations

import subprocess
import time
import shlex, json
from datetime import datetime, timezone
from typing import Any
from pathlib import Path
from .node import Node
from .models import (
    ConnectionStatus,
    NodeConfig,
    NodeMetrics,
    PodMetrics,
    VMConfig,
    VMMetrics,
)

SSH_CONTROL_DIRECTORY = Path.home() / ".ssh" / "controlmasters"
SSH_CONTROL_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True,
)

class VM:
    def __init__(self, config: VMConfig) -> None:
        self.config = config

    def _ssh_control_path(self) -> Path:
        return (
            SSH_CONTROL_DIRECTORY
            / f"{self.config.ssh_user}-"
            f"{self.config.host}-"
            f"{self.config.ssh_port}"
        )

    def _run_ssh_command(
        self,
        remote_command: str,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "ssh",
            "-i",
            str(self.config.ssh_key.expanduser()),
            "-p",
            str(self.config.ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.config.connection_timeout_seconds}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=120s",
            "-o",
            f"ControlPath={self._ssh_control_path()}",
            f"{self.config.ssh_user}@{self.config.host}",
            remote_command,
        ]

        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.config.connection_timeout_seconds + 30,
            check=False,
        )

    def check_connection(self) -> ConnectionStatus:
        timestamp = datetime.now(timezone.utc)
        started_at = time.perf_counter()

        try:
            result = self._run_ssh_command("true")
            elapsed_ms = (time.perf_counter() - started_at) * 1000

            if result.returncode == 0:
                return ConnectionStatus(
                    timestamp=timestamp,
                    cluster_name=self.config.name,
                    host=self.config.host,
                    reachable=True,
                    response_time_ms=round(elapsed_ms, 2),
                    error=None,
                )

            return ConnectionStatus(
                timestamp=timestamp,
                cluster_name=self.config.name,
                host=self.config.host,
                reachable=False,
                response_time_ms=round(elapsed_ms, 2),
                error=result.stderr.strip() or "SSH command failed",
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - started_at) * 1000

            return ConnectionStatus(
                timestamp=timestamp,
                cluster_name=self.config.name,
                host=self.config.host,
                reachable=False,
                response_time_ms=round(elapsed_ms, 2),
                error="SSH connection timed out",
            )

    def gather_metrics(self) -> VMMetrics:
        connection_status = self.check_connection()

        if not connection_status.reachable:
            raise RuntimeError(
                f"VM {self.config.name} is unreachable: "
                f"{connection_status.error}"
            )

        ssh_latency_ms = connection_status.response_time_ms

        if ssh_latency_ms is None:
            raise RuntimeError(f"No SSH latency available for {self.config.name}")

        remote_command = r"""
        set -e

        read_cpu() {
            read -r cpu user nice system idle iowait irq softirq steal guest guest_nice \
                < /proc/stat

            idle_all=$((idle + iowait))
            non_idle=$((user + nice + system + irq + softirq + steal))
            total=$((idle_all + non_idle))

            echo "$total $idle_all"
        }

        read -r total_1 idle_1 <<< "$(read_cpu)"
        sleep 1
        read -r total_2 idle_2 <<< "$(read_cpu)"

        total_delta=$((total_2 - total_1))
        idle_delta=$((idle_2 - idle_1))

        if [ "$total_delta" -gt 0 ]; then
            cpu_usage=$(awk -v total="$total_delta" -v idle="$idle_delta" \
                'BEGIN { printf "%.2f", 100 * (total - idle) / total }')
        else
            cpu_usage="0.00"
        fi

        memory_total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
        memory_available_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)

        memory_total_bytes=$((memory_total_kb * 1024))
        memory_available_bytes=$((memory_available_kb * 1024))
        memory_used_bytes=$((memory_total_bytes - memory_available_bytes))

        memory_usage=$(awk \
            -v total="$memory_total_bytes" \
            -v available="$memory_available_bytes" \
            'BEGIN {
                if (total > 0) {
                    printf "%.2f", 100 * (total - available) / total
                } else {
                    printf "0.00"
                }
            }')

        read -r load_1m load_5m load_15m _ < /proc/loadavg

        hostname_value=$(hostname)
        cpu_cores=$(nproc)

        printf '%s\n' \
            "hostname=$hostname_value" \
            "cpu_usage_percent=$cpu_usage" \
            "memory_total_bytes=$memory_total_bytes" \
            "memory_available_bytes=$memory_available_bytes" \
            "memory_used_bytes=$memory_used_bytes" \
            "memory_usage_percent=$memory_usage" \
            "load_average_1m=$load_1m" \
            "load_average_5m=$load_5m" \
            "load_average_15m=$load_15m" \
            "cpu_core_count=$cpu_cores"
        """

        try:
            result = self._run_ssh_command(remote_command)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"VM metric collection timed out for {self.config.name}"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"Could not collect VM metrics from {self.config.name}: "
                f"{result.stderr.strip() or 'remote command failed'}"
            )

        values: dict[str, str] = {}

        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")

            if separator:
                values[key.strip()] = value.strip()

        required_fields = {
            "hostname",
            "cpu_usage_percent",
            "memory_total_bytes",
            "memory_available_bytes",
            "memory_used_bytes",
            "memory_usage_percent",
            "load_average_1m",
            "load_average_5m",
            "load_average_15m",
            "cpu_core_count",
        }

        missing_fields = required_fields - values.keys()

        if missing_fields:
            raise RuntimeError(
                f"Incomplete VM metrics from {self.config.name}. "
                f"Missing fields: {sorted(missing_fields)}"
            )

        return VMMetrics(
            timestamp=datetime.now(timezone.utc),
            cluster_name=self.config.name,
            host=self.config.host,
            hostname=values["hostname"],
            ssh_latency_ms=ssh_latency_ms,
            cpu_usage_percent=float(values["cpu_usage_percent"]),
            memory_total_bytes=int(values["memory_total_bytes"]),
            memory_available_bytes=int(values["memory_available_bytes"]),
            memory_used_bytes=int(values["memory_used_bytes"]),
            memory_usage_percent=float(values["memory_usage_percent"]),
            load_average_1m=float(values["load_average_1m"]),
            load_average_5m=float(values["load_average_5m"]),
            load_average_15m=float(values["load_average_15m"]),
            cpu_core_count=int(values["cpu_core_count"]),
        )
    def query_prometheus(
        self,
        promql: str,
    ) -> list[dict[str, Any]]:
        endpoint = (
            f"{self.config.prometheus_url.rstrip('/')}/api/v1/query"
        )

        remote_command = (
            "curl "
            "--silent "
            "--show-error "
            "--fail "
            "--get "
            f"{shlex.quote(endpoint)} "
            f"--data-urlencode {shlex.quote(f'query={promql}')}"
        )

        try:
            result = self._run_ssh_command(remote_command)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Prometheus query timed out for {self.config.name}"
            ) from exc

        if result.returncode != 0:
            error = result.stderr.strip() or "Remote curl command failed"

            raise RuntimeError(
                f"Could not query Prometheus for "
                f"{self.config.name}: {error}"
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Prometheus returned invalid JSON for "
                f"{self.config.name}: {result.stdout[:300]!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Unexpected Prometheus response type for "
                f"{self.config.name}: {type(payload).__name__}"
            )

        if payload.get("status") != "success":
            error_type = payload.get("errorType", "unknown")
            error_message = payload.get("error", "unknown error")

            raise RuntimeError(
                f"Prometheus query failed for {self.config.name}: "
                f"{error_type}: {error_message}"
            )

        data = payload.get("data")

        if not isinstance(data, dict):
            raise RuntimeError(
                f"Prometheus response has no valid data object for "
                f"{self.config.name}"
            )

        query_result = data.get("result")

        if not isinstance(query_result, list):
            raise RuntimeError(
                f"Prometheus response has no valid result list for "
                f"{self.config.name}"
            )

        return query_result

    
    def discover_nodes(self) -> list[Node]:
        result = self.query_prometheus("node_uname_info")

        nodes: list[Node] = []

        for item in result:
            metric = item.get("metric", {})

            instance = metric.get("instance")
            node_name = metric.get("nodename")

            if not instance or not node_name:
                continue

            nodes.append(
                Node(
                    config=NodeConfig(
                        name=str(node_name),
                        role=self._determine_node_role(
                            str(node_name)
                        ),
                        prometheus_instance=str(instance),
                    ),
                    vm=self,
                )
            )

        if not nodes:
            raise RuntimeError(
                f"No Kubernetes nodes discovered on {self.config.name}"
            )

        return nodes
    @staticmethod
    def _determine_node_role(node_name: str) -> str:
        normalized_name = node_name.lower()

        if (
            "control-plane" in normalized_name
            or "master" in normalized_name
        ):
            return "control-plane"

        return "worker"

    def query_prometheus_many(
        self,
        queries: dict[str, str],
    ) -> dict[str, list[dict[str, Any]]]:
        endpoint = (
            f"{self.config.prometheus_url.rstrip('/')}/api/v1/query"
        )

        commands: list[str] = []

        for name, promql in queries.items():
            marker = f"__QUERY__{name}"

            commands.append(
                f"printf '%s\\n' {shlex.quote(marker)} && "
                "curl --silent --show-error --fail --get "
                f"{shlex.quote(endpoint)} "
                f"--data-urlencode {shlex.quote(f'query={promql}')}"
            )

        remote_command = " && ".join(commands)

        try:
            result = self._run_ssh_command(remote_command)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Prometheus batch query timed out for "
                f"{self.config.name}"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"Could not query Prometheus for "
                f"{self.config.name}: "
                f"{result.stderr.strip() or 'remote curl failed'}"
            )

        responses: dict[str, list[dict[str, Any]]] = {}

        sections = result.stdout.split("__QUERY__")

        for section in sections:
            section = section.strip()

            if not section:
                continue

            name, separator, raw_json = section.partition("\n")

            if not separator:
                raise RuntimeError(
                    f"Invalid Prometheus batch response section: "
                    f"{section[:300]!r}"
                )

            name = name.strip()
            raw_json = raw_json.strip()

            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Prometheus returned invalid JSON for query "
                    f"{name}: {raw_json[:300]!r}"
                ) from exc

            if not isinstance(payload, dict):
                raise RuntimeError(
                    f"Unexpected Prometheus response for query "
                    f"{name}: {type(payload).__name__}"
                )

            if payload.get("status") != "success":
                raise RuntimeError(
                    f"Prometheus query {name} failed: "
                    f"{payload.get('error', 'unknown error')}"
                )

            data = payload.get("data")

            if not isinstance(data, dict):
                raise RuntimeError(
                    f"Prometheus query {name} returned invalid data"
                )

            query_result = data.get("result")

            if not isinstance(query_result, list):
                raise RuntimeError(
                    f"Prometheus query {name} returned invalid results"
                )

            responses[name] = query_result

        missing_queries = set(queries) - set(responses)

        if missing_queries:
            raise RuntimeError(
                f"Missing Prometheus batch responses for "
                f"{self.config.name}: {sorted(missing_queries)}"
            )

        return responses

    def gather_all_pod_metrics(
        self,
    ) -> dict[tuple[str, str], PodMetrics]:
        queries = {
            "cpu": """
                sum by (namespace, pod) (
                    rate(
                        container_cpu_usage_seconds_total{
                            namespace!="",
                            pod!="",
                            container!="",
                            container!="POD"
                        }[1m]
                    )
                )
            """,
            "memory_working_set": """
                sum by (namespace, pod) (
                    container_memory_working_set_bytes{
                        namespace!="",
                        pod!="",
                        container!="",
                        container!="POD"
                    }
                )
            """,
            "memory_rss": """
                sum by (namespace, pod) (
                    container_memory_rss{
                        namespace!="",
                        pod!="",
                        container!="",
                        container!="POD"
                    }
                )
            """,
            "network_receive": """
                sum by (namespace, pod) (
                    rate(
                        container_network_receive_bytes_total{
                            namespace!="",
                            pod!=""
                        }[1m]
                    )
                )
            """,
            "network_transmit": """
                sum by (namespace, pod) (
                    rate(
                        container_network_transmit_bytes_total{
                            namespace!="",
                            pod!=""
                        }[1m]
                    )
                )
            """,
            "container_count": """
                count by (namespace, pod) (
                    container_last_seen{
                        namespace!="",
                        pod!="",
                        container!="",
                        container!="POD"
                    }
                )
            """,
            "pod_info": """
                max by (namespace, pod, node) (
                    kube_pod_info
                )
            """,
        }

        query_started_at = time.perf_counter()

        results = self.query_prometheus_many(queries)

        prometheus_query_latency_ms = (
            time.perf_counter() - query_started_at
        ) * 1000

        values: dict[
            tuple[str, str],
            dict[str, float | str],
        ] = {}

        def ensure_pod(
            namespace: str,
            pod_name: str,
        ) -> dict[str, float | str]:
            key = (namespace, pod_name)

            return values.setdefault(
                key,
                {
                    "node_name": "",
                    "cpu": 0.0,
                    "memory_working_set": 0.0,
                    "memory_rss": 0.0,
                    "network_receive": 0.0,
                    "network_transmit": 0.0,
                    "container_count": 0.0,
                },
            )

        for item in results["pod_info"]:
            metric = item.get("metric", {})

            if not isinstance(metric, dict):
                continue

            namespace = metric.get("namespace")
            pod_name = metric.get("pod")
            node_name = metric.get("node")

            if not namespace or not pod_name:
                continue

            pod_values = ensure_pod(
                str(namespace),
                str(pod_name),
            )

            if node_name:
                pod_values["node_name"] = str(node_name)

        metric_names = (
            "cpu",
            "memory_working_set",
            "memory_rss",
            "network_receive",
            "network_transmit",
            "container_count",
        )

        for metric_name in metric_names:
            for item in results[metric_name]:
                metric = item.get("metric", {})
                value = item.get("value")

                if not isinstance(metric, dict):
                    continue

                if not isinstance(value, list) or len(value) != 2:
                    continue

                namespace = metric.get("namespace")
                pod_name = metric.get("pod")

                if not namespace or not pod_name:
                    continue

                try:
                    numeric_value = float(value[1])
                except (TypeError, ValueError):
                    continue

                pod_values = ensure_pod(
                    str(namespace),
                    str(pod_name),
                )
                pod_values[metric_name] = numeric_value

        timestamp = datetime.now(timezone.utc)

        metrics_by_pod: dict[
            tuple[str, str],
            PodMetrics,
        ] = {}

        for key, pod_values in values.items():
            namespace, pod_name = key

            cpu_usage_cores = float(
                pod_values["cpu"]
            )

            metrics_by_pod[key] = PodMetrics(
                timestamp=timestamp,
                cluster_name=self.config.name,
                prometheus_query_latency_ms=round(prometheus_query_latency_ms, 2),
                namespace=namespace,
                pod_name=pod_name,
                node_name=str(
                    pod_values["node_name"]
                ),
                cpu_usage_cores=round(
                    cpu_usage_cores,
                    6,
                ),
                cpu_usage_millicores=round(
                    cpu_usage_cores * 1000,
                    2,
                ),
                memory_usage_bytes=int(
                    float(
                        pod_values[
                            "memory_working_set"
                        ]
                    )
                ),
                memory_rss_bytes=int(
                    float(
                        pod_values["memory_rss"]
                    )
                ),
                network_receive_bytes_per_second=round(
                    float(
                        pod_values[
                            "network_receive"
                        ]
                    ),
                    2,
                ),
                network_transmit_bytes_per_second=round(
                    float(
                        pod_values[
                            "network_transmit"
                        ]
                    ),
                    2,
                ),
                container_count=int(
                    float(
                        pod_values[
                            "container_count"
                        ]
                    )
                ),
            )

        return metrics_by_pod

    def gather_all_node_metrics(
        self,
    ) -> dict[str, NodeMetrics]:
        queries = {
            "node_info": """
                node_uname_info
            """,
            "cpu_usage": """
                100 * (
                    1 -
                    avg by (instance) (
                        sum by (instance, cpu) (
                            rate(
                                node_cpu_seconds_total{
                                    mode=~"idle|iowait"
                                }[1m]
                            )
                        )
                    )
                )
            """,
            "cpu_core_count": """
                count by (instance) (
                    count by (instance, cpu) (
                        node_cpu_seconds_total{
                            mode="idle"
                        }
                    )
                )
            """,
            "memory_total": """
                node_memory_MemTotal_bytes
            """,
            "memory_available": """
                node_memory_MemAvailable_bytes
            """,
            "load_1m": """
                node_load1
            """,
            "load_5m": """
                node_load5
            """,
            "load_15m": """
                node_load15
            """,
        }

        query_started_at = time.perf_counter()

        results = self.query_prometheus_many(queries)

        prometheus_query_latency_ms = (
            time.perf_counter() - query_started_at
        ) * 1000

        node_names_by_instance: dict[str, str] = {}

        for item in results["node_info"]:
            metric = item.get("metric", {})

            if not isinstance(metric, dict):
                continue

            instance = metric.get("instance")
            node_name = metric.get("nodename")

            if instance and node_name:
                node_names_by_instance[str(instance)] = str(node_name)

        values: dict[str, dict[str, float]] = {}

        metric_names = (
            "cpu_usage",
            "cpu_core_count",
            "memory_total",
            "memory_available",
            "load_1m",
            "load_5m",
            "load_15m",
        )

        for metric_name in metric_names:
            for item in results[metric_name]:
                metric = item.get("metric", {})
                value = item.get("value")

                if not isinstance(metric, dict):
                    continue

                instance = metric.get("instance")

                if not instance:
                    continue

                if not isinstance(value, list) or len(value) != 2:
                    continue

                try:
                    numeric_value = float(value[1])
                except (TypeError, ValueError):
                    continue

                values.setdefault(
                    str(instance),
                    {},
                )[metric_name] = numeric_value

        timestamp = datetime.now(timezone.utc)
        metrics_by_node: dict[str, NodeMetrics] = {}

        for instance, node_name in node_names_by_instance.items():
            node_values = values.get(instance)

            if node_values is None:
                continue

            memory_total = int(
                node_values.get(
                    "memory_total",
                    0.0,
                )
            )

            memory_available = int(
                node_values.get(
                    "memory_available",
                    0.0,
                )
            )

            memory_used = (
                memory_total - memory_available
            )

            memory_usage = (
                memory_used / memory_total * 100
                if memory_total > 0
                else 0.0
            )

            metrics_by_node[node_name] = NodeMetrics(
                timestamp=timestamp,
                cluster_name=self.config.name,
                prometheus_query_latency_ms=round(prometheus_query_latency_ms, 2),
                node_name=node_name,
                node_role=self._determine_node_role(
                    node_name
                ),
                prometheus_instance=instance,
                cpu_usage_percent=round(
                    node_values.get(
                        "cpu_usage",
                        0.0,
                    ),
                    2,
                ),
                cpu_core_count=int(
                    node_values.get(
                        "cpu_core_count",
                        0.0,
                    )
                ),
                memory_total_bytes=memory_total,
                memory_available_bytes=memory_available,
                memory_used_bytes=memory_used,
                memory_usage_percent=round(
                    memory_usage,
                    2,
                ),
                load_average_1m=node_values.get(
                    "load_1m",
                    0.0,
                ),
                load_average_5m=node_values.get(
                    "load_5m",
                    0.0,
                ),
                load_average_15m=node_values.get(
                    "load_15m",
                    0.0,
                ),
            )

        return metrics_by_node