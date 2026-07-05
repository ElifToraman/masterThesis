from __future__ import annotations

from abc import ABC, abstractmethod

from .models import BenchmarkRequest


class FunctionPlatform(ABC):
    @abstractmethod
    def service_exists(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def deploy_service(
        self,
        kubernetes_context: str,
        request: BenchmarkRequest,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def wait_until_ready(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
        timeout_seconds: float,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_service_url(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def delete_service(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
    ) -> None:
        raise NotImplementedError