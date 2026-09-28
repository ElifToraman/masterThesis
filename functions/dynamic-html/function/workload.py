"""SeBS Dynamic HTML workload adapted to an HTTP function."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from random import Random
from time import perf_counter

from jinja2 import Template


TEMPLATE_FILE = Path(__file__).parent / "templates" / "template.html"
MAX_RANDOM_LENGTH = 100_000


def run_dynamic_html(
    *,
    username: str,
    random_len: int,
    seed: int,
) -> dict:
    if not username or len(username) > 128:
        raise ValueError("username must contain between 1 and 128 characters")
    if isinstance(random_len, bool) or not isinstance(random_len, int):
        raise ValueError("random_len must be an integer")
    if not 1 <= random_len <= MAX_RANDOM_LENGTH:
        raise ValueError(f"random_len must be between 1 and {MAX_RANDOM_LENGTH}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    started_at = perf_counter()
    random_numbers = Random(seed).sample(range(1_000_000), random_len)
    template = Template(TEMPLATE_FILE.read_text(encoding="utf-8"))
    html = template.render(
        username=username,
        cur_time=datetime.now(timezone.utc).isoformat(),
        random_numbers=random_numbers,
    )
    duration_ms = (perf_counter() - started_at) * 1000

    return {
        "benchmark": "dynamic-html",
        "success": True,
        "workload": {
            "username": username,
            "random_len": random_len,
            "seed": seed,
        },
        "duration_ms": round(duration_ms, 3),
        "result": {
            "html": html,
            "generated_count": len(random_numbers),
        },
    }
