"""Evaluation harness: runs every case in eval/cases.json and reports quality, latency and token use.

    python -m eval.run_eval                         # mode from .env (LLM_PROVIDER), default offline
    python -m eval.run_eval --provider bedrock      # force Amazon Bedrock
    python -m eval.run_eval --save                  # also write eval/results/<provider>-<timestamp>.md/.json

Metrics
  task success        final status matches the expected status
  routing accuracy    offline: exact tool sequence; bedrock: required tools appear in order and no forbidden tool ran
                      (a model may legitimately add an extra read step)
  approval precision / recall   approvals requested vs approvals that should be requested
  tokens / latency    from the llm_call and run events in the execution history
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
CASES = HERE / "cases.json"


def in_order(required: list[str], actual: list[str]) -> bool:
    it = iter(actual)
    return all(any(a == r for a in it) for r in required)


def run(provider: str | None, save: bool, only: str | None) -> int:
    from app.config import _load_dotenv

    _load_dotenv()
    if provider:
        os.environ["LLM_PROVIDER"] = provider
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="agentflow-eval-")

    from app.config import get_settings
    from app.runner import Runner

    settings = get_settings()
    mode = settings.llm_provider
    runner = Runner(settings)
    cases = [c for c in json.loads(CASES.read_text()) if mode in c.get("modes", ["fake", "bedrock"])]
    if only:
        cases = [c for c in cases if only.lower() in c["name"].lower()]

    rows, tp, fp, fn = [], 0, 0, 0
    for c in cases:
        t0 = time.perf_counter()
        error = None
        try:
            run_id = runner.start(c["request"], c["inputs"], "eval@example.com")
            approvals_seen = []
            while runner.history.get_run(run_id)["status"] == "awaiting_approval":
                pending = runner.state(run_id).get("pending")
                if not pending:
                    break
                approvals_seen.append(pending["tool"])
                runner.decide(pending["approval_id"], c["human"], "eval-approver")
                runner.resume(run_id, c["human"], "eval-approver")
            run_rec = runner.history.get_run(run_id)
            events = runner.history.events(run_id)
            status, planner = run_rec["status"], (runner.state(run_id).get("planner") or "-")
        except Exception as e:  # a crashing case is a failed case, not a crashed eval
            events, approvals_seen, status, planner = [], [], "error", "-"
            error = f"{type(e).__name__}: {e}"
        secs = time.perf_counter() - t0

        tools = [e["data"]["tool"] for e in events if e["type"] == "tool_call"]
        llm = [e["data"] for e in events if e["type"] == "llm_call"]
        if mode == "fake":
            ok_route = tools == c["expected_tools"]
        else:
            ok_route = in_order(c["expected_tools"], tools) and not set(c.get("forbidden_tools", [])) & set(tools)
        exp, got = set(c["expected_approvals"]), set(approvals_seen)
        tp += len(exp & got)
        fp += len(got - exp)
        fn += len(exp - got)
        rows.append({
            "case": c["name"], "status": status, "expected": c["expected_status"],
            "ok": status == c["expected_status"], "routing_ok": ok_route, "approvals": approvals_seen,
            "tools": tools, "planner": planner, "seconds": round(secs, 2),
            "llm_calls": len(llm), "input_tokens": sum(x.get("input_tokens", 0) for x in llm),
            "output_tokens": sum(x.get("output_tokens", 0) for x in llm), "error": error,
        })

    n = len(rows) or 1
    summary = {
        "provider": mode,
        "supervisor_model": settings.supervisor_model_id if mode == "bedrock" else "-",
        "worker_model": settings.worker_model_id if mode == "bedrock" else "-",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "cases": len(rows),
        "task_success": sum(r["ok"] for r in rows) / n,
        "routing_accuracy": sum(r["routing_ok"] for r in rows) / n,
        "approval_precision": tp / (tp + fp) if tp + fp else 1.0,
        "approval_recall": tp / (tp + fn) if tp + fn else 1.0,
        "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else 0,
        "avg_tokens_per_run": round(sum(r["input_tokens"] + r["output_tokens"] for r in rows) / n),
    }

    print(f"\nProvider: {mode}   supervisor: {summary['supervisor_model']}   worker: {summary['worker_model']}\n")
    print(f"{'case':44} {'status':18} {'ok':3} {'route':6} {'secs':>6} {'tokens':>7}  planner")
    for r in rows:
        print(f"{r['case'][:44]:44} {r['status']:18} {'Y' if r['ok'] else 'N':3} {'Y' if r['routing_ok'] else 'N':6} "
              f"{r['seconds']:>6} {r['input_tokens'] + r['output_tokens']:>7}  {r['planner'][:40]}")
        if r["error"]:
            print(f"    ERROR {r['error'][:200]}")
        elif not (r["ok"] and r["routing_ok"]):
            print(f"    tools: {r['tools']}")
    print(f"\nTask success:       {summary['task_success']:.0%}")
    print(f"Routing accuracy:   {summary['routing_accuracy']:.0%}")
    print(f"Approval precision: {summary['approval_precision']:.0%}   recall: {summary['approval_recall']:.0%}")
    print(f"Median run time:    {summary['median_seconds']}s   avg tokens/run: {summary['avg_tokens_per_run']}")

    if save:
        out = HERE / "results"
        out.mkdir(exist_ok=True)
        stem = f"{mode}-{datetime.now().strftime('%Y%m%d-%H%M')}"
        (out / f"{stem}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=2))
        (out / f"{stem}.md").write_text(to_markdown(summary, rows))
        print(f"\nSaved eval/results/{stem}.md and .json")
    runner.close()
    return 0 if all(r["ok"] for r in rows) else 1


def to_markdown(s: dict, rows: list[dict]) -> str:
    lines = [
        f"# Eval results: {s['provider']}",
        "",
        f"Run {s['timestamp']} · supervisor `{s['supervisor_model']}` · worker `{s['worker_model']}` "
        f"· {s['cases']} cases",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Task success | {s['task_success']:.0%} |",
        f"| Routing accuracy | {s['routing_accuracy']:.0%} |",
        f"| Approval precision / recall | {s['approval_precision']:.0%} / {s['approval_recall']:.0%} |",
        f"| Median run time | {s['median_seconds']} s |",
        f"| Average tokens per run | {s['avg_tokens_per_run']:,} |",
        "",
        "| Case | Status | Pass | Routing | Seconds | Tokens | Planner |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        passed, routed = ("✅" if r["ok"] else "❌"), ("✅" if r["routing_ok"] else "❌")
        lines.append(f"| {r['case']} | {r['status']} | {passed} | {routed} "
                     f"| {r['seconds']} | {r['input_tokens'] + r['output_tokens']:,} | {r['planner']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", choices=["fake", "bedrock"], help="override LLM_PROVIDER")
    p.add_argument("--save", action="store_true", help="write results to eval/results/")
    p.add_argument("--only", help="run only cases whose name contains this text")
    a = p.parse_args()
    return run(a.provider, a.save, a.only)


if __name__ == "__main__":
    sys.exit(main())
