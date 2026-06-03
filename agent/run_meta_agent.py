"""Agent-of-Agent 主流程：spec → Claude 生成 prompt → 模板生成代码 → 跑测试 → 评估 → 落盘。

CLI:
  python run_meta_agent.py --spec templates/AGENT_SPEC_EXAMPLE.md
  python run_meta_agent.py --spec templates/AGENT_SPEC_EXAMPLE.md --dry-run

输出落到 artifacts/<job_id>/，与 RULES §6 token 预算配合。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 让本脚本以 `python run_meta_agent.py` 直接运行时也能 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meta_agent.code_generator import CodeGenerator
from meta_agent.evaluator import Evaluator
from meta_agent.executor import Executor
from meta_agent.feedback_analyzer import FeedbackAnalyzer
from meta_agent.llm_client import BudgetExceeded, ClaudeClient
from meta_agent.prompt_generator import PromptGenerator
from meta_agent.spec_parser import parse_spec_file


PROJECT_ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
LOGS_DIR = PROJECT_ROOT / "logs" / "jobs"


def _new_job_id(agent_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{agent_name}_{ts}_{uuid.uuid4().hex[:6]}"


def run_pipeline(spec_path: str | Path, *, dry_run: bool = False,
                 max_iterations: int | None = None,
                 job_id: str | None = None) -> dict:
    spec_path = Path(spec_path).resolve()
    if not spec_path.exists():
        raise FileNotFoundError(spec_path)
    spec = parse_spec_file(spec_path)
    task = spec.asdict()

    job_id = job_id or _new_job_id(spec.name)
    out_dir = ARTIFACTS_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "spec.md").write_text(spec.raw_md, encoding="utf-8")

    log_path = out_dir / "claude_log.jsonl"
    pipeline_log = out_dir / "pipeline.log"

    def _plog(msg: str):
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
        print(line, flush=True)
        with pipeline_log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    _plog(f"job_id={job_id}")
    _plog(f"spec={spec_path}")
    _plog(f"agent_name={spec.name} v{spec.version}")
    _plog(f"tools={[t['name'] for t in spec.tools]}")
    _plog(f"test_cases={len(spec.test_cases)}")

    # 初始化 Claude（dry_run 时跳过）
    client = None
    if not dry_run:
        budget = spec.token_budget
        os.environ.setdefault("TOKEN_BUDGET_INPUT",  str(budget.get("max_input_tokens", 50000)))
        os.environ.setdefault("TOKEN_BUDGET_OUTPUT", str(budget.get("max_output_tokens", 20000)))
        try:
            client = ClaudeClient(log_path=log_path)
            _plog(f"claude model={client.model} budget=[in:{client.budget_input},out:{client.budget_output}]")
        except SystemExit as e:
            _plog(f"FATAL: {e}")
            return _save_failure(out_dir, str(e), job_id)

    prompt_gen = PromptGenerator(client=client)
    code_gen = CodeGenerator()
    executor = Executor(timeout=60, project_root=PROJECT_ROOT)
    feedback = FeedbackAnalyzer(client=client)
    evaluator = Evaluator(acceptance=spec.acceptance)

    target = spec.acceptance.get("overall_score", 0.7)
    max_iter = max_iterations or int(spec.token_budget.get("max_iterations", 1))
    _plog(f"target_score={target} max_iterations={max_iter}")

    try:
        prompt = prompt_gen.generate(task)
    except BudgetExceeded as e:
        _plog(f"BudgetExceeded: {e}")
        return _save_failure(out_dir, str(e), job_id, reason="budget_exhausted")

    best = {"score": -1.0, "prompt": prompt, "code": "", "metrics": {}, "test_report": None,
            "iteration": 0}

    for it in range(1, max_iter + 1):
        _plog(f"--- iter {it} ---")
        code = code_gen.generate(prompt, spec.tools, spec.name,
                                 data_source=_first_data_source(spec.tools, spec.data_access),
                                 project_root=str(PROJECT_ROOT))
        # 立即落盘当前 iter 的代码（便于调试）
        (out_dir / f"agent_code.iter{it}.py").write_text(code, encoding="utf-8")
        (out_dir / f"system_prompt.iter{it}.txt").write_text(prompt, encoding="utf-8")

        report = executor.run_tests(code, spec.test_cases, agent_name=spec.name)
        metrics = evaluator.evaluate(report)
        _plog(f"metrics={metrics}")
        _plog(f"pass_rate={report['passed']}/{report['total']}")

        if metrics["overall_score"] > best["score"]:
            best.update({"score": metrics["overall_score"],
                         "prompt": prompt, "code": code,
                         "metrics": metrics, "test_report": report,
                         "iteration": it})

        if evaluator.meets_threshold(metrics) and metrics["overall_score"] >= target:
            _plog(f"达标 (overall={metrics['overall_score']} >= {target})")
            break

        if it < max_iter:
            try:
                fb = feedback.analyze(report)
                _plog(f"feedback:\n{fb}")
                prompt = prompt_gen.optimize(prompt, fb, task)
            except BudgetExceeded as e:
                _plog(f"BudgetExceeded mid-iter: {e}")
                break
            except Exception as e:
                _plog(f"feedback/optimize 失败: {e}; 提前结束")
                break

    # 落盘 best
    (out_dir / "agent_code.py").write_text(best["code"], encoding="utf-8")
    (out_dir / "system_prompt.txt").write_text(best["prompt"], encoding="utf-8")
    (out_dir / "metrics.json").write_text(
        json.dumps(best["metrics"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "test_report.json").write_text(
        json.dumps(best["test_report"], ensure_ascii=False, indent=2), encoding="utf-8")

    register = {
        "job_id": job_id,
        "agent_name": spec.name,
        "version": spec.version,
        "score": best["score"],
        "metrics": best["metrics"],
        "iteration": best["iteration"],
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "spec_path": str(spec_path),
        "code_path": str(out_dir / "agent_code.py"),
        "data_source": _first_data_source(spec.tools, spec.data_access),
        "claude_usage": client.usage.asdict() if client else None,
        "passed_acceptance": evaluator.meets_threshold(best["metrics"]),
    }
    (out_dir / "REGISTER.json").write_text(
        json.dumps(register, ensure_ascii=False, indent=2), encoding="utf-8")
    _plog(f"DONE -> {out_dir}")
    return register


def _first_data_source(tools: list, data_access: dict) -> str:
    for t in tools:
        ds = (t.get("data_source") or "").strip()
        if ds:
            return ds
    db = data_access.get("数据库") or data_access.get("db")
    path = data_access.get("路径") or data_access.get("path") or "data/ksipms_dev.db"
    if db:
        return f"{db}:{path}"
    return "sqlite:data/ksipms_dev.db"


def _save_failure(out_dir: Path, err: str, job_id: str, reason: str = "error") -> dict:
    register = {"job_id": job_id, "passed_acceptance": False,
                "score": -1, "error": err, "reason": reason,
                "produced_at": datetime.now(timezone.utc).isoformat()}
    (out_dir / "REGISTER.json").write_text(
        json.dumps(register, ensure_ascii=False, indent=2), encoding="utf-8")
    return register


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="path to AGENT_SPEC.md")
    ap.add_argument("--dry-run", action="store_true", help="don't call Claude (template fallback)")
    ap.add_argument("--max-iterations", type=int, default=None)
    ap.add_argument("--job-id", default=None)
    args = ap.parse_args()
    try:
        register = run_pipeline(args.spec, dry_run=args.dry_run,
                                max_iterations=args.max_iterations,
                                job_id=args.job_id)
    except Exception:
        traceback.print_exc()
        sys.exit(2)
    print(json.dumps(register, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
