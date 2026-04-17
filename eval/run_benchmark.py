"""Run a DAB-style benchmark over remote query bundles and write results JSON."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.orchestrator import Orchestrator
from src.dab.remote_dab_adapter import RemoteDABAdapter
from src.tools.remote_sandbox import RemoteSandboxClient, RemoteSandboxConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Oracle Forge across DAB query bundles.")
    parser.add_argument("--agent", default="oracle_forge", help="Agent import path or preset name.")
    parser.add_argument("--trials", type=int, default=1, help="Trials per query.")
    parser.add_argument("--output", required=True, help="Path to the results JSON file.")
    parser.add_argument("--datasets", default="", help="Comma-separated dataset filter.")
    parser.add_argument("--query-ids", default="", help="Comma-separated query id filter per dataset.")
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
    os.environ.setdefault("REMOTE_SANDBOX_ENABLED", "true")

    remote_config = RemoteSandboxConfig(
        host=args.remote_host,
        dab_path=args.remote_dab_path,
        python_executable=args.remote_python,
    )
    agent = resolve_agent(args.agent, remote_config=remote_config)
    adapter = RemoteDABAdapter(config=remote_config)
    sandbox = RemoteSandboxClient(remote_config)

    datasets = parse_filter_list(args.datasets)
    query_id_filter = parse_filter_list(args.query_ids, cast=int)
    discovered = discover_datasets(sandbox)

    if datasets:
        discovered = {name: ids for name, ids in discovered.items() if name in datasets}
    if query_id_filter:
        discovered = {name: [qid for qid in ids if qid in query_id_filter] for name, ids in discovered.items()}

    all_query_results: list[dict[str, Any]] = []
    total_trials = 0
    passed_queries = 0

    for dataset, query_ids in sorted(discovered.items()):
        for query_id in sorted(query_ids):
            bundle = adapter.get_query_bundle(dataset, query_id)
            if not bundle or bundle.get("ok") is False and "query_text" not in bundle:
                all_query_results.append(
                    {
                        "dataset": dataset,
                        "query_id": query_id,
                        "query_name": f"query{query_id}",
                        "error": f"Failed to fetch remote query bundle: {bundle}",
                        "trial_results": [],
                        "success_rate": 0.0,
                        "passed_trials": 0,
                    }
                )
                continue

            trial_results: list[dict[str, Any]] = []
            for trial_index in range(1, args.trials + 1):
                result = run_single_trial(agent, dataset, query_id, bundle, trial_index)
                trial_results.append(result)
                total_trials += 1

            passed_trials = sum(1 for result in trial_results if result.get("passed"))
            success_rate = passed_trials / len(trial_results) if trial_results else 0.0
            passed_queries += 1 if passed_trials > 0 else 0
            all_query_results.append(
                {
                    "dataset": dataset,
                    "query_id": query_id,
                    "query_name": f"query{query_id}",
                    "question": bundle["query_text"],
                    "databases_used": sorted(bundle.get("db_clients", {}).keys()),
                    "trials": args.trials,
                    "passed_trials": passed_trials,
                    "success_rate": success_rate,
                    "trial_results": trial_results,
                }
            )

    total_queries = len(all_query_results)
    summary = {
        "total_queries": total_queries,
        "trials_per_query": args.trials,
        "total_trials": total_trials,
        "pass_at_1": passed_queries / total_queries if total_queries else 0.0,
        "passed_queries": passed_queries,
    }
    payload = {
        "benchmark": "DataAgentBench",
        "agent": args.agent,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "results": all_query_results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "summary": summary}, indent=2))


def run_single_trial(
    agent: Any,
    dataset: str,
    query_id: int,
    bundle: dict[str, Any],
    trial_index: int,
) -> dict[str, Any]:
    result = execute_agent(agent, bundle["query_text"], bundle)
    validation = result.get("validation", {})
    passed = validation.get("status") == "passed"
    return {
        "trial_index": trial_index,
        "answer": result.get("final_answer"),
        "query_trace": result.get("trace", []),
        "confidence": 1.0 if passed else 0.0,
        "passed": passed,
        "validation": validation,
        "remote_validation": result.get("remote_validation"),
        "tool_calls": result.get("execution_result", {}).get("tool_calls", []),
        "retries": result.get("retries"),
    }


def execute_agent(agent: Any, question: str, benchmark_context: dict[str, Any]) -> dict[str, Any]:
    if hasattr(agent, "execute_turn"):
        return agent.execute_turn(question, benchmark_context=benchmark_context)
    if callable(agent):
        try:
            return agent(question=question, benchmark_context=benchmark_context)
        except TypeError:
            try:
                return agent(question, benchmark_context)
            except TypeError:
                return agent(question)
    raise TypeError("Resolved agent does not expose execute_turn or a compatible callable interface.")


def resolve_agent(agent_spec: str, remote_config: RemoteSandboxConfig | None = None) -> Any:
    if agent_spec in {"oracle_forge", "src.agent.orchestrator", "Orchestrator"}:
        return Orchestrator(remote_config=remote_config)

    if ":" in agent_spec:
        module_name, attr_name = agent_spec.split(":", 1)
        module = importlib.import_module(module_name)
        target = getattr(module, attr_name)
        if inspect.isclass(target):
            try:
                return target(remote_config=remote_config)
            except TypeError:
                return target()
        return target

    module = importlib.import_module(agent_spec)
    for attr_name in ("Orchestrator", "Agent", "build_agent", "create_agent"):
        if hasattr(module, attr_name):
            target = getattr(module, attr_name)
            if inspect.isclass(target):
                try:
                    return target(remote_config=remote_config)
                except TypeError:
                    return target()
            return target
    if callable(module):
        return module
    raise ImportError(
        f"Could not resolve agent '{agent_spec}'. Use a module with Orchestrator/Agent or module:Class syntax."
    )


def discover_datasets(sandbox: RemoteSandboxClient) -> dict[str, list[int]]:
    root = sandbox.config.dab_path
    response = sandbox.run_command(f"find {root} -maxdepth 1 -type d -name 'query_*' -printf '%f\\n' | sort")
    if not response.get("ok"):
        raise RuntimeError(f"Failed to discover datasets: {response.get('stderr') or response.get('stdout')}")

    datasets: dict[str, list[int]] = {}
    for line in response.get("stdout", "").splitlines():
        dataset = line.removeprefix("query_")
        query_response = sandbox.run_command(
            f"find {root}/{line} -maxdepth 1 -type d -regex '.*/query[0-9]+' -printf '%f\\n' | sort -V"
        )
        if not query_response.get("ok"):
            continue
        query_ids = []
        for query_dir in query_response.get("stdout", "").splitlines():
            match = re.fullmatch(r"query(\d+)", query_dir.strip())
            if match:
                query_ids.append(int(match.group(1)))
        if query_ids:
            datasets[dataset] = query_ids
    return datasets


def parse_filter_list(value: str, cast=str) -> list[Any]:
    if not value.strip():
        return []
    items = [item.strip() for item in value.split(",") if item.strip()]
    return [cast(item) for item in items]


if __name__ == "__main__":
    main()
