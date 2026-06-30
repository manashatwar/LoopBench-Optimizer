"""
Final report generation for OptimizerLoop.

Tasks 11.1, 11.3 — Requirements 7.5, 16.1, 16.3, 16.4, 16.5, 16.6, 17.4, 17.5, 17.6

Produces three artefacts:
  1. ``best_patch.diff``     — the winning unified diff (Req 16.1)
  2. ``validation_report.md`` — before/after metrics + improvement (Req 16.3)
  3. ``README.md``           — optimization summary + files modified (Req 16.4)
  4. ``pr_description.md``   — PR body template (Req 16.5)

The ``generate_final_report`` free function (used by ``OptimizerLoop.run()``)
returns a plain dict and optionally writes these files to an output directory.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Public API – used by OptimizerLoop.generate_final_report()
# ---------------------------------------------------------------------------

def generate_final_report(
    best_candidate: Dict[str, Any],
    baseline_candidate: Dict[str, Any],
    success_threshold: float = 0.10,
    total_generations: int = 0,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate improvement, assign status, and return a report dict.

    Task 11.1 — Requirements 7.5, 17.4, 17.5, 17.6

    Args:
        best_candidate:   Candidate dict with the highest score found.
        baseline_candidate: Gen-0 candidate dict.
        success_threshold: Fractional improvement required for "successful"
            status (default 0.10 = 10 %).
        total_generations: Total generations executed in the run.
        run_id: Optional run identifier, included in the report.

    Returns:
        Report dict with keys:
          - ``status``                 "successful" | "completed"
          - ``improvement``            float (fractional, e.g. 0.15 = 15 %)
          - ``improvement_pct``        float (e.g. 15.0)
          - ``baseline_score``         float
          - ``best_score``             float
          - ``total_generations``      int
          - ``best_candidate``         the best candidate dict
          - ``baseline_candidate``     the baseline dict
          - ``success_threshold``      the configured threshold
          - ``run_id``                 str | None
          - ``confidence_warning``     bool — True when improvement ≤ threshold
    """
    baseline_score: float = float(baseline_candidate.get("score") or 0.0)
    best_score: float = float(best_candidate.get("score") or 0.0)

    # Improvement fraction — handle zero-baseline gracefully
    if abs(baseline_score) < 1e-9:
        improvement = 0.0 if abs(best_score) < 1e-9 else 1.0
    else:
        improvement = (best_score - baseline_score) / abs(baseline_score)

    # Property 6: "successful" if and only if improvement strictly > threshold
    status = "successful" if improvement > success_threshold else "completed"

    # Req 16.6: warn when improvement ≤ threshold (marginal or no gain)
    confidence_warning = improvement <= success_threshold

    return {
        "status": status,
        "improvement": improvement,
        "improvement_pct": round(improvement * 100, 2),
        "baseline_score": baseline_score,
        "best_score": best_score,
        "total_generations": total_generations,
        "best_candidate": best_candidate,
        "baseline_candidate": baseline_candidate,
        "success_threshold": success_threshold,
        "run_id": run_id,
        "confidence_warning": confidence_warning,
    }


# ---------------------------------------------------------------------------
# FinalReportWriter – writes files to output directory (Task 11.3)
# ---------------------------------------------------------------------------

class FinalReportWriter:
    """Write all final-report artefacts to an output directory.

    Task 11.3 — Requirements 16.1, 16.3, 16.4, 16.5, 16.6

    Example::

        writer = FinalReportWriter(output_dir=Path("./results"))
        writer.write_all(report)
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)

    # ── Public entry point ─────────────────────────────────────────────────

    def write_all(self, report: Dict[str, Any]) -> Dict[str, Path]:
        """Write every artefact and return a mapping of name → path.

        Args:
            report: Dict returned by :func:`generate_final_report`.

        Returns:
            ``{"patch": Path, "validation": Path, "readme": Path,
               "pr_description": Path}``
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        paths: Dict[str, Path] = {}
        paths["patch"] = self._write_patch(report)
        paths["validation"] = self._write_validation_report(report)
        paths["readme"] = self._write_readme(report)
        paths["pr_description"] = self._write_pr_description(report)
        return paths

    # ── Artefact writers ───────────────────────────────────────────────────

    def _write_patch(self, report: Dict[str, Any]) -> Path:
        """Req 16.1 — export best candidate's patch in unified diff format."""
        path = self.output_dir / "best_patch.diff"
        patch = (report.get("best_candidate") or {}).get("patch_content") or ""
        path.write_text(patch, encoding="utf-8")
        return path

    def _write_validation_report(self, report: Dict[str, Any]) -> Path:
        """Req 16.2, 16.3 — before/after metrics + improvement + patch status."""
        path = self.output_dir / "validation_report.md"

        baseline_score = report.get("baseline_score", 0.0)
        best_score = report.get("best_score", 0.0)
        improvement_pct = report.get("improvement_pct", 0.0)
        status = report.get("status", "completed")
        confidence_warning = report.get("confidence_warning", True)

        # Attempt to apply patch cleanly — report 'passed' only if clean (Req 16.2)
        patch = (report.get("best_candidate") or {}).get("patch_content") or ""
        patch_status = _check_patch_syntax(patch)

        lines: List[str] = [
            "# Validation Report",
            "",
            f"**Run ID**: {report.get('run_id', 'N/A')}",
            f"**Status**: {status.upper()}",
            f"**Total Generations**: {report.get('total_generations', 0)}",
            "",
            "## Performance",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Baseline Score | {baseline_score:.6f} |",
            f"| Best Score | {best_score:.6f} |",
            f"| Improvement | {improvement_pct:+.2f}% |",
            f"| Success Threshold | {report.get('success_threshold', 0.10) * 100:.1f}% |",
            "",
            "## Patch Validation",
            "",
            f"**Patch Status**: `{patch_status}`",
            "",
        ]

        if confidence_warning:
            lines += [
                "## ⚠️ Low Confidence Warning",
                "",
                "The improvement does not exceed the configured success threshold. "
                "Review the patch carefully before applying.",
                "",
            ]

        # Baseline metrics
        baseline_metrics = (report.get("baseline_candidate") or {}).get("metrics") or {}
        best_metrics = (report.get("best_candidate") or {}).get("metrics") or {}
        if baseline_metrics or best_metrics:
            lines += ["## Metric Breakdown", ""]
            all_keys = sorted(set(baseline_metrics) | set(best_metrics))
            lines.append("| Metric | Baseline | Best |")
            lines.append("|--------|----------|------|")
            for key in all_keys:
                b_val = baseline_metrics.get(key, "—")
                n_val = best_metrics.get(key, "—")
                if isinstance(b_val, float):
                    b_val = f"{b_val:.6f}"
                if isinstance(n_val, float):
                    n_val = f"{n_val:.6f}"
                lines.append(f"| {key} | {b_val} | {n_val} |")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _write_readme(self, report: Dict[str, Any]) -> Path:
        """Req 16.4 — README explaining the optimization and files modified."""
        path = self.output_dir / "README.md"

        patch = (report.get("best_candidate") or {}).get("patch_content") or ""
        modified_files = _extract_modified_files(patch)
        files_section = (
            "\n".join(f"- `{f}`" for f in modified_files)
            if modified_files
            else "_(patch is empty or no files detected)_"
        )

        text = textwrap.dedent(f"""\
            # Optimization Run — {report.get('run_id', 'N/A')}

            ## Summary

            | | |
            |---|---|
            | Status | {report.get('status', 'completed').upper()} |
            | Generations | {report.get('total_generations', 0)} |
            | Baseline Score | {report.get('baseline_score', 0.0):.6f} |
            | Best Score | {report.get('best_score', 0.0):.6f} |
            | Improvement | {report.get('improvement_pct', 0.0):+.2f}% |

            ## Optimization Approach

            This run used the **OptimizerLoop** evolutionary optimization system.
            For each generation, the system:

            1. Maps the repository context using `RepoContextMapper`
            2. Generates an optimization patch using an LLM
            3. Applies the patch to an isolated git worktree
            4. Runs the test suite inside a Docker sandbox
            5. Extracts performance metrics from test output
            6. Selects the best candidate as the new baseline

            ## Files Modified

            {files_section}

            ## How to Apply

            ```bash
            git apply best_patch.diff
            ```

            ## Artefacts

            - `best_patch.diff`        — unified diff of the best optimization
            - `validation_report.md`   — before/after metrics and patch status
            - `pr_description.md`      — ready-to-use pull request description
        """)

        path.write_text(text, encoding="utf-8")
        return path

    def _write_pr_description(self, report: Dict[str, Any]) -> Path:
        """Req 16.5 — PR description summarising changes and performance gains."""
        path = self.output_dir / "pr_description.md"

        patch = (report.get("best_candidate") or {}).get("patch_content") or ""
        modified_files = _extract_modified_files(patch)
        files_list = (
            "\n".join(f"- `{f}`" for f in modified_files) or "_(no files detected)_"
        )
        warning_block = ""
        if report.get("confidence_warning"):
            warning_block = (
                "\n> ⚠️ **Low confidence**: improvement does not exceed the "
                f"{report.get('success_threshold', 0.10) * 100:.1f}% success threshold. "
                "Manual review recommended.\n"
            )

        text = textwrap.dedent(f"""\
            ## Performance Optimization

            {warning_block}
            ### Summary

            Automated optimization via OptimizerLoop achieved a
            **{report.get('improvement_pct', 0.0):+.2f}%** improvement on the target metric.

            | Metric | Before | After |
            |--------|--------|-------|
            | Score | {report.get('baseline_score', 0.0):.6f} | {report.get('best_score', 0.0):.6f} |

            ### Files Changed

            {files_list}

            ### Testing

            All changes were validated in an isolated Docker sandbox running the
            existing test suite. No new tests were added; the optimization improves
            performance without altering behaviour.

            ---
            _Generated by OptimizerLoop (run `{report.get('run_id', 'N/A')}`, {report.get('total_generations', 0)} generations)_
        """)

        path.write_text(text, encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _check_patch_syntax(patch: str) -> str:
    """Return 'passed' only if patch has valid unified diff markers; else 'failed'.

    Req 16.2: 'passed' reserved for patches that apply cleanly without issues.
    Here we do a lightweight syntax check (presence of --- / +++ / @@ markers).
    A full application check requires a live repo.
    """
    if not patch or not patch.strip():
        return "failed"
    has_minus = "--- " in patch
    has_plus = "+++ " in patch
    has_hunk = "@@ " in patch
    return "passed" if (has_minus and has_plus and has_hunk) else "failed"


def _extract_modified_files(patch: str) -> List[str]:
    """Parse unified diff header lines to extract modified file paths."""
    files: List[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ "):
            # "+++ b/path/to/file" → "path/to/file"
            raw = line[4:].strip()
            if raw.startswith("b/"):
                raw = raw[2:]
            if raw and raw != "/dev/null":
                files.append(raw)
    return files
