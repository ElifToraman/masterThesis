"""FunctionBench gzip workload adapted to a concurrent HTTP function."""

from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter


MAX_FILE_SIZE_MIB = 64


def run_gzip_compression(*, file_size_mb: int) -> dict:
    if isinstance(file_size_mb, bool) or not isinstance(file_size_mb, int):
        raise ValueError("file_size_mb must be an integer")
    if not 1 <= file_size_mb <= MAX_FILE_SIZE_MIB:
        raise ValueError(f"file_size_mb must be between 1 and {MAX_FILE_SIZE_MIB}")

    requested_bytes = file_size_mb * 1024 * 1024

    with TemporaryDirectory(prefix="gzip-benchmark-", dir="/tmp") as tmp:
        input_file = Path(tmp) / "input.bin"
        output_file = Path(tmp) / "result.gz"

        write_started = perf_counter()
        input_file.write_bytes(os.urandom(requested_bytes))
        disk_write_ms = (perf_counter() - write_started) * 1000

        compression_started = perf_counter()
        with input_file.open("rb") as source:
            with gzip.open(output_file, "wb") as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
        compression_ms = (perf_counter() - compression_started) * 1000

        output_bytes = output_file.stat().st_size
        digest = hashlib.sha256(output_file.read_bytes()).hexdigest()

    return {
        "benchmark": "gzip-compression",
        "success": True,
        "workload": {"file_size_mb": file_size_mb},
        "duration_ms": round(disk_write_ms + compression_ms, 3),
        "result": {
            "input_bytes": requested_bytes,
            "output_bytes": output_bytes,
            "disk_write_ms": round(disk_write_ms, 3),
            "compression_ms": round(compression_ms, 3),
            "output_sha256": digest,
        },
    }
