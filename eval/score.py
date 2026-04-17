"""Score a benchmark results JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a benchmark results JSON file.")
    parser.add_argument("--results", required=True, help="Path to the results JSON file.")
    args = parser.parse_args()

    path = Path(args.results)
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results", [])

    total_queries = len(results)
    passed_queries = 0
    total_trials = 0
    passed_trials = 0
    failure_classes: dict[str, int] = {}

    for query_result in results:
        trial_results = query_result.get("trial_results", [])
        total_trials += len(trial_results)
        query_passed = False
        for trial in trial_results:
            if trial.get("passed"):
                passed_trials += 1
                query_passed = True
            else:
                failure_class = trial.get("validation", {}).get("failure_class", "unknown")
                failure_classes[failure_class] = failure_classes.get(failure_class, 0) + 1
        if query_passed:
            passed_queries += 1

    summary = {
        "total_queries": total_queries,
        "total_trials": total_trials,
        "passed_queries": passed_queries,
        "passed_trials": passed_trials,
        "pass_at_1": passed_queries / total_queries if total_queries else 0.0,
        "trial_pass_rate": passed_trials / total_trials if total_trials else 0.0,
        "failure_classes": failure_classes,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
