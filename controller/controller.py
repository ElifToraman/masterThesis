import logging
import signal
import threading
import time
from pathlib import Path

from monitoring.models import VMConfig
from monitoring.monitoring_service import MonitoringService
from monitoring.vm import VM


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def create_vms() -> list[VM]:
    return [
        VM(
            VMConfig(
                name="vm1-cluster",
                host="129.114.25.182",
                ssh_user="cc",
                ssh_key=(
                    Path.home()
                    / ".ssh"
                    / "chameleon_new"
                ),
                prometheus_url=(
                    "http://127.0.0.1:19091"
                ),
            )
        ),
        VM(
            VMConfig(
                name="vm2-cluster",
                host="129.114.25.80",
                ssh_user="cc",
                ssh_key=(
                    Path.home()
                    / ".ssh"
                    / "chameleon_new"
                ),
                prometheus_url=(
                    "http://127.0.0.1:19092"
                ),
            )
        ),
    ]


def make_scheduling_decision(
    monitoring: MonitoringService,
) -> str:
    snapshot = monitoring.require_latest_snapshot(
        maximum_age_seconds=30,
    )

    candidates = list(
        snapshot.node_metrics.values()
    )

    if not candidates:
        raise RuntimeError(
            "No node metrics are available for scheduling"
        )

    selected_node = min(
        candidates,
        key=lambda metrics: (
            metrics.cpu_usage_percent,
            metrics.memory_usage_percent,
        ),
    )

    return selected_node.node_name


def main() -> None:
    controller_directory = (
        Path(__file__).resolve().parent
    )

    monitoring = MonitoringService(
        vms=create_vms(),
        collection_interval_seconds=10,
        output_directory=(
            controller_directory
            / "monitoring"
            / "metrics"
        ),
    )

    shutdown_event = threading.Event()

    def handle_shutdown(
        signum: int,
        frame: object,
    ) -> None:
        logger.info(
            "Shutdown signal received: %s",
            signum,
        )
        shutdown_event.set()

    signal.signal(
        signal.SIGTERM,
        handle_shutdown,
    )
    signal.signal(
        signal.SIGINT,
        handle_shutdown,
    )

    monitoring.start()

    try:
        monitoring.wait_for_first_snapshot(
            timeout_seconds=60,
        )

        while not shutdown_event.is_set():
            try:
                selected_node = (
                    make_scheduling_decision(
                        monitoring
                    )
                )

                print(
                    f"Selected node: {selected_node}"
                )

            except RuntimeError as error:
                logger.error(
                    "Could not make scheduling "
                    "decision: %s",
                    error,
                )

            shutdown_event.wait(timeout=10)

    finally:
        monitoring.stop()


if __name__ == "__main__":
    main()
