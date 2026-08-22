from pathlib import Path
import re

import matplotlib.pyplot as plt
import pandas as pd


INPUT_DIR = Path("../metrics")
OUTPUT_DIR = Path("metric_graphs")

METRICS = {
    "cpu_usage_percent": "CPU usage (%)",
    "cpu_usage_cores": "CPU usage (cores)",
    "cpu_usage_millicores": "CPU usage (millicores)",
    "memory_available_bytes": "Available memory (MiB)",
    "memory_used_bytes": "Used memory (MiB)",
    "memory_usage_percent": "Memory usage (%)",
    "memory_rss_bytes": "Memory RSS (MiB)",
    "load_average_1m": "Load average (1 minute)",
    "load_average_5m": "Load average (5 minutes)",
    "load_average_15m": "Load average (15 minutes)",
    "network_receive_bytes_per_second": "Network receive (KiB/s)",
    "network_transmit_bytes_per_second": "Network transmit (KiB/s)",
    "latency": "Collection latency (ms)",
}

BYTE_COLUMNS = {
    "memory_available_bytes",
    "memory_used_bytes",
    "memory_rss_bytes",
}

BYTE_RATE_COLUMNS = {
    "network_receive_bytes_per_second",
    "network_transmit_bytes_per_second",
}


def file_index(path: Path) -> int:
    match = re.fullmatch(r"metrics_(\d+)\.csv", path.name)
    return int(match.group(1)) if match else -1


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def entity_name(row: pd.Series) -> str:
    if row["metric_type"] == "vm":
        return str(row["hostname"])

    if row["metric_type"] == "node":
        return str(row["node_name"])

    return f'{row["namespace"]}_{row["pod_name"]}'


def plot_metric(group: pd.DataFrame, metric: str, label: str) -> None:
    values = group[metric].dropna()

    if values.empty:
        return

    group = group.dropna(subset=[metric]).sort_values("timestamp").copy()

    if metric in BYTE_COLUMNS:
        group[metric] = group[metric] / (1024 ** 2)

    if metric in BYTE_RATE_COLUMNS:
        group[metric] = group[metric] / 1024

    metric_type = group["metric_type"].iloc[0]
    cluster = group["cluster_name"].iloc[0]
    entity = entity_name(group.iloc[0])

    output_directory = OUTPUT_DIR / metric_type / metric
    output_directory.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 5))
    plt.plot(group["timestamp"], group[metric], marker="o")
    plt.title(f"{entity} — {label}")
    plt.xlabel("Time")
    plt.ylabel(label)
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.tight_layout()

    filename = safe_filename(f"{cluster}_{entity}_{metric}.png")
    plt.savefig(output_directory / filename)
    plt.close()


files = sorted(INPUT_DIR.glob("metrics_*.csv"), key=file_index)

if not files:
    raise FileNotFoundError(
        f"No metrics_<index>.csv files found in {INPUT_DIR.resolve()}"
    )

data = pd.concat(
    (pd.read_csv(file) for file in files),
    ignore_index=True,
)

data["timestamp"] = pd.to_datetime(data["timestamp"])

for metric_type in ("vm", "node", "pod"):
    type_data = data[data["metric_type"] == metric_type]

    if type_data.empty:
        continue

    if metric_type == "vm":
        group_columns = ["cluster_name", "hostname"]
    elif metric_type == "node":
        group_columns = ["cluster_name", "node_name"]
    else:
        group_columns = ["cluster_name", "namespace", "pod_name"]

    for _, group in type_data.groupby(group_columns):
        for metric, label in METRICS.items():
            plot_metric(group, metric, label)

print(f"Graphs saved in: {OUTPUT_DIR.resolve()}")
