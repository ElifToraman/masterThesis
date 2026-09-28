from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from controller.scripts.cleanup_supported_services import (
    cleanup_supported_services,
)


class CleanupSupportedServicesTests(unittest.TestCase):
    @patch("controller.scripts.cleanup_supported_services.delete_service")
    @patch("controller.scripts.cleanup_supported_services.service_exists")
    def test_deletes_each_existing_service_from_each_cluster(
        self,
        service_exists,
        delete_service,
    ) -> None:
        service_exists.side_effect = lambda **kwargs: (
            kwargs["service_name"] == "dynamic-html"
        )
        delete_service.return_value = "deleted"
        clusters = {
            "vm1": SimpleNamespace(kubernetes_context="vm1-context"),
            "vm2": SimpleNamespace(kubernetes_context="vm2-context"),
        }

        actions = cleanup_supported_services(
            clusters=clusters,
            service_names=["dynamic-html", "graph-pagerank"],
        )

        self.assertEqual(len(actions), 4)
        self.assertEqual(delete_service.call_count, 2)
        self.assertTrue(
            all(
                action["deleted"]
                for action in actions
                if action["service_name"] == "dynamic-html"
            )
        )
        self.assertTrue(
            all(
                not action["deleted"]
                for action in actions
                if action["service_name"] == "graph-pagerank"
            )
        )


if __name__ == "__main__":
    unittest.main()
