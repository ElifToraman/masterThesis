"""SeBS PageRank workload adapted to an HTTP function."""

from __future__ import annotations

import random
import threading
from time import perf_counter

import igraph


MAX_GRAPH_SIZE = 100_000
_GRAPH_LOCK = threading.Lock()


def run_graph_pagerank(*, size: int, seed: int) -> dict:
    if isinstance(size, bool) or not isinstance(size, int):
        raise ValueError("size must be an integer")
    if not 10 <= size <= MAX_GRAPH_SIZE:
        raise ValueError(f"size must be between 10 and {MAX_GRAPH_SIZE}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    # python-igraph exposes a process-level random number generator. Lock the
    # seeded graph construction so concurrent requests remain reproducible.
    with _GRAPH_LOCK:
        igraph.set_random_number_generator(random.Random(seed))
        generation_started = perf_counter()
        graph = igraph.Graph.Barabasi(size, 10)
        graph_generation_ms = (perf_counter() - generation_started) * 1000

        compute_started = perf_counter()
        pagerank = graph.pagerank()
        compute_ms = (perf_counter() - compute_started) * 1000

    return {
        "benchmark": "graph-pagerank",
        "success": True,
        "workload": {"size": size, "seed": seed},
        "duration_ms": round(graph_generation_ms + compute_ms, 3),
        "result": {
            "pagerank_first_vertex": float(pagerank[0]),
            "vertex_count": graph.vcount(),
            "edge_count": graph.ecount(),
            "graph_generation_ms": round(graph_generation_ms, 3),
            "compute_ms": round(compute_ms, 3),
        },
    }
