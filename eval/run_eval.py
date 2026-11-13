"""Evaluation harness: runs every case in eval/cases.json and reports quality metrics.

    python -m eval.run_eval                 # offline (fake) mode
    LLM_PROVIDER=bedrock python -m eval.run_eval   # same cases against Bedrock

Metrics
  task success      final status matches the expected status
  routing accuracy  the exact sequence of tools called matches the expected one
  approval precision / recall   approvals requested vs approvals that should be requested
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

CASES = Path(__file__).with_name("cases.json")


def main() -> int:
    os.environ.setdefault("LLM_PROVIDER", "fake")
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="agentflow-eval-")
    from app.config import get_settings
    from app.runner import Runner

    runner = Runner(get_settings())
    cases = json.loads(CASES.read_text())
    rows, tp, fp, fn, success, routed = [], 0, 0, 0, 0, 0

    for c in cases:
        t0 = time.perf_counter()
        run_id = runner.start(c["request"], c["inputs"], "eval@example.com")
        approvals_seen = []
        while True:  # answer every approval with the case's scripted human decision
            pending = runner.state(run_id).get("pending")
            if not pending or runner.history.get_run(run_id)["status"] != "awaiting_approval":
                break
            approvals_seen.append(pending["tool"])
            runner.decide(pending["approval_id"], c["human"], "eval-approver")
            runner.resume(run_id, c["human"], "eval-approver")
        secs = time.perf_counter() - t0

        status = runner.history.get_run(run_id)["status"]
        tools = [e["data"]["tool"] for e in runner.history.events(run_id) if e["type"] == "tool_call"]
        ok_status = status == c["expected_status"]
        ok_route = tools == c["expected_tools"]
        exp, got = set(c["expected_approvals"]), set(approvals_seen)
        tp += len(exp & got)
        fp += len(got - exp)
        fn += len(exp - got)
        success += ok_status
        routed += ok_route
        rows.append((c["name"], status, ok_status, ok_route, approvals_seen, secs))

    print(f"{'case':42} {'status':18} {'ok':4} {'route':6} approvals")
    for name, status, ok_s, ok_r, appr, secs in rows:
        print(f"{name:42} {status:18} {'Y' if ok_s else 'N':4} {'Y' if ok_r else 'N':6} {appr} ({secs:.2f}s)")
    n = len(cases)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    print(f"\nTask success:       {success}/{n} = {success / n:.0%}")
    print(f"Routing accuracy:   {routed}/{n} = {routed / n:.0%}")
    print(f"Approval precision: {precision:.0%}   recall: {recall:.0%}")
    return 0 if success == n else 1


if __name__ == "__main__":
    sys.exit(main())
