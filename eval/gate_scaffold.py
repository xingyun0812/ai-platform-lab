#!/usr/bin/env python3
"""生成 eval 门禁骨架：*_gate.py、可选 *_smoke.py、*_baseline.jsonl。

用法（在目标仓库根目录，且脚本位于 eval/ 下）：
  python eval/gate_scaffold.py init --slug my-feature --phase S --title "My Feature"
  python eval/gate_scaffold.py init --slug my-feature --phase S --repo-root /path/to/repo

生成后：
  1. 编辑 eval/<slug>_gate.py 的 CHECKS / REQUIRED_PATHS
  2. 实现 eval/<slug>_smoke.py
  3. python eval/<slug>_gate.py run
  4. 接入 .github/workflows/ci.yml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from textwrap import dedent

_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def resolve_repo_root(explicit: str | None = None) -> Path:
    """仓库根：--repo-root > 脚本在 eval/ 时 parents[1] > 当前工作目录。"""
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve()
    if here.parent.name == "eval":
        return here.parents[1]
    return Path.cwd().resolve()


def _gate_py(slug: str, phase: str, title: str) -> str:
    gate_name = f"{slug}_gate"
    class_prefix = "".join(p.capitalize() for p in slug.replace("-", "_").split("_"))
    slug_safe = slug.replace("-", "_")
    check_unit = f"{slug_safe}_unit"
    check_smoke = f"{slug_safe}_smoke"
    smoke = f"{slug}_smoke.py"
    baseline = f"{slug}_baseline.jsonl"
    test_guess = f"tests/test_{slug_safe}.py"
    return dedent(
        f'''\
        #!/usr/bin/env python3
        """Phase {phase} — {title} 离线 CI 门禁。

        用法：
          python eval/{gate_name}.py list
          python eval/{gate_name}.py check
          python eval/{gate_name}.py run
          python eval/{gate_name}.py run --json
        """

        from __future__ import annotations

        import argparse
        import json
        import subprocess
        import sys
        import time
        from dataclasses import asdict, dataclass
        from pathlib import Path
        from typing import Any

        REPO_ROOT = Path(__file__).resolve().parents[1]
        BASELINE_PATH = REPO_ROOT / "eval" / "{baseline}"
        PYTHON = sys.executable


        @dataclass(frozen=True)
        class {class_prefix}GateCheck:
            name: str
            issue: str
            command: tuple[str, ...]
            description: str = ""


        @dataclass
        class {class_prefix}GateResult:
            name: str
            issue: str
            passed: bool
            duration_ms: float
            returncode: int
            detail: str


        CHECKS: tuple[{class_prefix}GateCheck, ...] = (
            {class_prefix}GateCheck(
                "{check_unit}",
                "{phase}",
                (PYTHON, "-m", "pytest", "{test_guess}", "-q"),
                "单元测试（请按实际路径修改）",
            ),
            {class_prefix}GateCheck(
                "{check_smoke}",
                "{phase}",
                (PYTHON, str(REPO_ROOT / "eval" / "{smoke}")),
                "smoke 脚本",
            ),
        )

        REQUIRED_PATHS: tuple[tuple[str, Path], ...] = (
            # ("module_label", REPO_ROOT / "packages" / "..." / "module.py"),
        )


        def verify_required_paths() -> list[str]:
            return [label for label, path in REQUIRED_PATHS if not path.is_file()]


        def load_baseline_cases() -> list[dict[str, Any]]:
            if not BASELINE_PATH.is_file():
                return []
            cases: list[dict[str, Any]] = []
            for line_no, line in enumerate(BASELINE_PATH.read_text(encoding="utf-8").splitlines(), 1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError as e:
                    raise ValueError(f"invalid JSONL line {{line_no}}: {{e}}") from e
                if not isinstance(row, dict):
                    raise ValueError(f"line {{line_no}}: expected object")
                cases.append(row)
            return cases


        def validate_baseline(cases: list[dict[str, Any]] | None = None) -> list[str]:
            errors: list[str] = []
            if not BASELINE_PATH.is_file():
                return errors
            rows = cases if cases is not None else load_baseline_cases()
            seen: set[str] = set()
            for row in rows:
                case_id = row.get("id")
                if not isinstance(case_id, str) or not case_id.strip():
                    errors.append("每条 baseline 须含非空 id")
                    continue
                if case_id in seen:
                    errors.append(f"重复 id: {{case_id}}")
                seen.add(case_id)
            return errors


        def run_check(check: {class_prefix}GateCheck) -> {class_prefix}GateResult:
            t0 = time.perf_counter()
            proc = subprocess.run(
                list(check.command),
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            detail = (proc.stdout or proc.stderr or "").strip()
            if len(detail) > 400:
                detail = detail[:400] + "..."
            return {class_prefix}GateResult(
                name=check.name,
                issue=check.issue,
                passed=proc.returncode == 0,
                duration_ms=duration_ms,
                returncode=proc.returncode,
                detail=detail,
            )


        def run_gate() -> tuple[bool, list[{class_prefix}GateResult]]:
            missing = verify_required_paths()
            if missing:
                return False, [
                    {class_prefix}GateResult(
                        name="required_paths",
                        issue="{phase}",
                        passed=False,
                        duration_ms=0.0,
                        returncode=1,
                        detail=f"missing: {{', '.join(missing)}}",
                    )
                ]
            baseline_errors = validate_baseline()
            if baseline_errors:
                return False, [
                    {class_prefix}GateResult(
                        name="baseline_check",
                        issue="{phase}",
                        passed=False,
                        duration_ms=0.0,
                        returncode=1,
                        detail="; ".join(baseline_errors[:5]),
                    )
                ]
            results = [run_check(c) for c in CHECKS]
            return all(r.passed for r in results), results


        def main() -> None:
            parser = argparse.ArgumentParser(description="Phase {phase} {title} gate")
            sub = parser.add_subparsers(dest="command", required=True)
            sub.add_parser("list", help="列出检查项")
            check_p = sub.add_parser("check", help="静态校验")
            check_p.add_argument("--json", action="store_true")
            run_p = sub.add_parser("run", help="运行全部离线检查")
            run_p.add_argument("--json", action="store_true")
            args = parser.parse_args()

            if args.command == "list":
                payload = [
                    {{"name": c.name, "issue": c.issue, "description": c.description, "command": list(c.command)}}
                    for c in CHECKS
                ]
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                raise SystemExit(0)

            if args.command == "check":
                missing = verify_required_paths()
                baseline_errors = validate_baseline()
                passed = not missing and not baseline_errors
                if getattr(args, "json", False):
                    print(json.dumps(
                        {{"passed": passed, "missing_paths": missing, "baseline_errors": baseline_errors}},
                        ensure_ascii=False,
                        indent=2,
                    ))
                else:
                    if missing:
                        print(f"[FAIL] missing paths: {{', '.join(missing)}}")
                    for err in baseline_errors:
                        print(f"[FAIL] baseline: {{err}}")
                    if passed:
                        print("[OK] static check passed")
                raise SystemExit(0 if passed else 1)

            passed, results = run_gate()
            if args.json:
                print(json.dumps(
                    {{"passed": passed, "results": [asdict(r) for r in results]}},
                    ensure_ascii=False,
                    indent=2,
                ))
            else:
                for r in results:
                    mark = "OK" if r.passed else "FAIL"
                    print(f"[{{mark}}] {{r.issue}} {{r.name}} ({{r.duration_ms}}ms)")
                    if not r.passed and r.detail:
                        print(f"       {{r.detail}}")
                print(f"\\n{gate_name}: {{'PASSED' if passed else 'FAILED'}} ({{sum(r.passed for r in results)}}/{{len(results)}})")
            raise SystemExit(0 if passed else 1)


        if __name__ == "__main__":
            main()
        '''
    )


def _smoke_py(slug: str, phase: str, title: str) -> str:
    return dedent(
        f'''\
        #!/usr/bin/env python3
        """eval/{slug}_smoke.py — Phase {phase} {title} smoke。

        运行：python eval/{slug}_smoke.py
        """

        from __future__ import annotations


        def main() -> None:
            # TODO: 实现最小 smoke（无 LLM Key 也应 exit 0）
            print("OK {slug}_smoke (stub — replace with real checks)")


        if __name__ == "__main__":
            main()
        '''
    )


def _baseline_jsonl(slug: str, phase: str) -> str:
    slug_safe = slug.replace("-", "_")
    rows = [
        {
            "id": f"{slug}-case-1",
            "phase": phase,
            "description": "TODO: 描述验收场景",
            "required_checks": [f"{slug_safe}_smoke"],
        },
    ]
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


def cmd_init(args: argparse.Namespace) -> int:
    slug: str = args.slug
    if not _SLUG_RE.match(slug):
        print(f"invalid slug {slug!r}: use kebab-case, e.g. my-feature", file=sys.stderr)
        return 1

    repo_root = resolve_repo_root(getattr(args, "repo_root", None))
    eval_dir = repo_root / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    phase = args.phase.upper()
    title = args.title or slug.replace("-", " ").title()
    files = {
        eval_dir / f"{slug}_gate.py": _gate_py(slug, phase, title),
        eval_dir / f"{slug}_smoke.py": _smoke_py(slug, phase, title),
        eval_dir / f"{slug}_baseline.jsonl": _baseline_jsonl(slug, phase),
    }

    existing = [p for p in files if p.exists()]
    if existing and not args.force:
        print("已存在（加 --force 覆盖）:", file=sys.stderr)
        for p in existing:
            print(f"  {p.relative_to(repo_root)}", file=sys.stderr)
        return 1

    if args.dry_run:
        for path, content in files.items():
            print(f"--- would write {path.relative_to(repo_root)} ({len(content)} bytes) ---")
        return 0

    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        print(f"wrote {path.relative_to(repo_root)}")

    print(f"\nNext: edit CHECKS in eval/{slug}_gate.py, implement smoke, then:")
    print(f"  python eval/{slug}_gate.py run")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold eval gate/smoke/baseline files")
    sub = parser.add_subparsers(dest="command", required=True)
    init_p = sub.add_parser("init", help="生成 gate + smoke + baseline 骨架")
    init_p.add_argument("--slug", required=True, help="kebab-case，如 harness-capability")
    init_p.add_argument("--phase", required=True, help="Phase 字母，如 R / S")
    init_p.add_argument("--title", default="", help="门禁标题")
    init_p.add_argument("--repo-root", default="", help="仓库根目录（默认：cwd 或 eval/ 的上一级）")
    init_p.add_argument("--dry-run", action="store_true")
    init_p.add_argument("--force", action="store_true", help="覆盖已存在文件")
    args = parser.parse_args()

    if args.command == "init":
        raise SystemExit(cmd_init(args))


if __name__ == "__main__":
    main()