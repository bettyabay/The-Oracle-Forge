"""List discovered DAB datasets from the remote DataAgentBench checkout."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.dab.remote_dab_adapter import RemoteDABAdapter
from src.tools.remote_sandbox import RemoteSandboxClient, RemoteSandboxConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="List discovered DAB datasets.")
    parser.add_argument("--remote-host", default="trp-gpt5", help="Remote DAB host.")
    parser.add_argument(
        "--remote-dab-path",
        default="/shared/DataAgentBench",
        help="Remote DAB checkout path.",
    )
    parser.add_argument(
        "--remote-python",
        default="/usr/bin/python3",
        help="Remote Python executable used by the sandbox bridge.",
    )
    args = parser.parse_args()

    remote_config = RemoteSandboxConfig(
        host=args.remote_host,
        dab_path=args.remote_dab_path,
        python_executable=args.remote_python,
    )
    adapter = RemoteDABAdapter(config=remote_config)
    sandbox = RemoteSandboxClient(remote_config)
    discovery = sandbox.run_command(
        "find /shared/DataAgentBench -maxdepth 1 -type d -name 'query_*' -printf '%f\\n' | sort"
    )
    if not discovery.get("ok"):
        raise RuntimeError(discovery.get("stderr") or discovery.get("stdout") or "Failed to discover datasets")

    rows = []
    for line in discovery.get("stdout", "").splitlines():
        dataset = line.removeprefix("query_")
        query_count = sandbox.run_command(
            f"find /shared/DataAgentBench/{line} -maxdepth 1 -type d -regex '.*/query[0-9]+' -printf '%f\\n' | wc -l"
        )
        count = int((query_count.get("stdout") or "0").strip() or "0") if query_count.get("ok") else 0
        db_types: list[str] = []
        try:
            bundle = adapter.get_query_bundle(dataset, 1)
            db_types = sorted(bundle.get("db_clients", {}).keys()) if bundle and bundle.get("db_clients") else []
        except Exception:
            db_types = []
        rows.append({"dataset": dataset, "query_count": count, "db_types": db_types})

    print(json.dumps({"total_datasets": len(rows), "datasets": rows}, indent=2))


if __name__ == "__main__":
    main()
