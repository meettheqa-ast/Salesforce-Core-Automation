"""Phase 3: Visual regression tier.

Adds a new test verdict alongside PASS / FAIL / SKIP / EMPTY:
``VISUAL_DRIFT`` -- the test passed functionally, but the rendered page
looks meaningfully different from the stored baseline.

Storage layout (per project, on the same volume Robot results write to):

    Saved_Projects/<slug>/screenshots/
        baselines/
            <test_case_id>__<step_label>.png    <- canonical
        diffs/
            <run_folder>/<test_case_id>__<step_label>.diff.png
            <run_folder>/<test_case_id>__<step_label>.current.png

Why this layout: baselines are project-scoped (different orgs render
slightly differently); diffs are run-scoped so a customer can navigate
"this run had drift in test X step Y" without grep'ing through history.

Public surface:

  capture_screenshot(...)        -> saves a current-state PNG via Playwright
  diff_against_baseline(...)     -> compares + writes diff PNG, returns
                                    pixel-diff percent + verdict
  promote_current_to_baseline(...)-> "accept new baseline" UI flow

Soft-fail policy throughout: any infrastructure error during visual
regression -> verdict stays PASS, the test is not penalised. Visual
drift is a SAFETY NET, not a load-bearing check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services import playwright_metrics as metrics

logger = logging.getLogger("ai_qa_portal.visual_regression")

# Per-project default thresholds. Overridden via project config below.
DEFAULT_PIXEL_THRESHOLD = 0.01  # 1% of pixels can differ before we flag drift


@dataclass
class DiffResult:
    """Outcome of a single screenshot diff."""
    test_case_id: str
    step_label: str
    baseline_exists: bool
    pixel_diff_count: int
    total_pixels: int
    diff_percent: float
    threshold: float
    verdict: str  # "ok" | "drift" | "no_baseline" | "error"
    diff_path: Path | None = None
    current_path: Path | None = None
    baseline_path: Path | None = None
    error: str | None = None


def _project_screenshot_root(project_slug: str) -> Path:
    """Resolve <SAVED_PROJECTS>/<slug>/screenshots/. Created lazily."""
    base = Path(settings.saved_projects_dir) / project_slug / "screenshots"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_filename(test_case_id: str, step_label: str) -> str:
    """Stable, filesystem-safe screenshot name. Matches what
    ``capture_screenshot`` writes; ``diff_against_baseline`` reads it."""
    safe_step = "".join(c if c.isalnum() or c in "-_" else "_" for c in step_label or "default")
    return f"{test_case_id}__{safe_step[:80]}"


def capture_screenshot(
    project_slug: str,
    test_case_id: str,
    step_label: str,
    page_url: str,
    *,
    sandbox_url: str,
    username: str,
    password: str,
    persona_id: str | None = None,
) -> Path | None:
    """Open the page in Playwright and capture the current screenshot.

    Returns the path to the saved PNG, or None if anything went wrong
    (Playwright unavailable, navigation error, screenshot timeout). The
    None case lets callers ``or`` the result and skip diff comparison
    without raising.
    """
    if not getattr(settings, "playwright_enabled", True):
        return None
    if not getattr(settings, "pw_visual_regression", False):
        return None

    with metrics.measure("pw_visual_capture", labels={"project": project_slug}) as meas:
        try:
            import sys

            from ai_qa_portal.backend.services.playwright_session_manager import acquire_session
            repo_root = Path(__file__).resolve().parent.parent.parent.parent
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            import pw_mcp_bridge

            handle = acquire_session(sandbox_url, username, password, persona_id)
            pw_mcp_bridge.goto(handle.context, page_url, wait_until="load")
            root = _project_screenshot_root(project_slug)
            current_dir = root / "current"
            current_dir.mkdir(parents=True, exist_ok=True)
            target = current_dir / f"{_safe_filename(test_case_id, step_label)}.png"
            pw_mcp_bridge.screenshot(handle.context, target, full_page=True)
            meas.label("result", "ok")
            return target
        except ImportError as exc:
            logger.warning("Playwright not available for visual capture: %s", exc)
            meas.label("result", "unavailable")
            return None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("visual capture failed for tc=%s step=%s: %s", test_case_id, step_label, exc)
            meas.label("result", "error")
            return None


def diff_against_baseline(
    project_slug: str,
    test_case_id: str,
    step_label: str,
    current_path: Path,
    run_folder: str,
    *,
    threshold: float = DEFAULT_PIXEL_THRESHOLD,
) -> DiffResult:
    """Compare ``current_path`` against the stored baseline. Writes a
    diff overlay PNG when drift is detected.

    Outcomes:
    - ``no_baseline``: first time we've seen this test+step. The current
      screenshot is automatically promoted to baseline. Returns OK.
    - ``ok``: pixel diff under threshold. No diff PNG written.
    - ``drift``: pixel diff over threshold. Diff PNG written under
      ``screenshots/diffs/<run_folder>/`` for the UI to display.
    - ``error``: pixelmatch failed (e.g. images differ in dimensions).
    """
    safe = _safe_filename(test_case_id, step_label)
    root = _project_screenshot_root(project_slug)
    baseline_path = root / "baselines" / f"{safe}.png"

    # First-time capture: promote current to baseline; no drift to report.
    if not baseline_path.is_file():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import shutil
            shutil.copy2(current_path, baseline_path)
        except OSError as exc:
            logger.warning("failed to seed baseline: %s", exc)
            return DiffResult(
                test_case_id=test_case_id,
                step_label=step_label,
                baseline_exists=False,
                pixel_diff_count=0,
                total_pixels=0,
                diff_percent=0.0,
                threshold=threshold,
                verdict="error",
                error=str(exc),
            )
        metrics.increment(
            "pw_visual_diff_total",
            labels={"project": project_slug, "result": "first_baseline"},
        )
        return DiffResult(
            test_case_id=test_case_id,
            step_label=step_label,
            baseline_exists=False,
            pixel_diff_count=0,
            total_pixels=0,
            diff_percent=0.0,
            threshold=threshold,
            verdict="no_baseline",
            current_path=current_path,
            baseline_path=baseline_path,
        )

    # Compare via pixelmatch. The import is lazy + try/except because
    # ``pixelmatch`` is an OPTIONAL dependency (only needed when
    # ``PW_VISUAL_REGRESSION=true``). Pyright / Pylint can't resolve
    # it in deployments that don't install it; the ``ImportError``
    # branch is the runtime fallback for exactly that case.
    try:
        from PIL import Image  # type: ignore[import-not-found]  # pylint: disable=import-error
        from pixelmatch.contrib.PIL import (
            pixelmatch,  # type: ignore[import-not-found]  # pylint: disable=import-error
        )
    except ImportError as exc:
        logger.warning("pixelmatch not installed: %s", exc)
        return DiffResult(
            test_case_id=test_case_id,
            step_label=step_label,
            baseline_exists=True,
            pixel_diff_count=0,
            total_pixels=0,
            diff_percent=0.0,
            threshold=threshold,
            verdict="error",
            error=str(exc),
        )

    try:
        baseline_img = Image.open(baseline_path).convert("RGBA")
        current_img = Image.open(current_path).convert("RGBA")
    except OSError as exc:
        return DiffResult(
            test_case_id=test_case_id,
            step_label=step_label,
            baseline_exists=True,
            pixel_diff_count=0,
            total_pixels=0,
            diff_percent=0.0,
            threshold=threshold,
            verdict="error",
            error=f"image read failed: {exc}",
        )

    # Resize current to match baseline if dimensions differ -- common
    # when the test ran at a different viewport. Keep the diff
    # meaningful by resizing the SMALLER of the two up to the larger.
    if baseline_img.size != current_img.size:
        target_size = (
            max(baseline_img.size[0], current_img.size[0]),
            max(baseline_img.size[1], current_img.size[1]),
        )
        baseline_img = baseline_img.resize(target_size)
        current_img = current_img.resize(target_size)

    diff_img = Image.new("RGBA", baseline_img.size)
    try:
        diff_count = pixelmatch(
            baseline_img, current_img, diff_img,
            threshold=0.1,  # per-pixel sensitivity (algorithm-level)
            includeAA=False,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return DiffResult(
            test_case_id=test_case_id,
            step_label=step_label,
            baseline_exists=True,
            pixel_diff_count=0,
            total_pixels=baseline_img.size[0] * baseline_img.size[1],
            diff_percent=0.0,
            threshold=threshold,
            verdict="error",
            error=f"pixelmatch failed: {exc}",
        )

    total = baseline_img.size[0] * baseline_img.size[1]
    diff_percent = diff_count / max(total, 1)
    verdict = "drift" if diff_percent > threshold else "ok"

    diff_path: Path | None = None
    if verdict == "drift":
        diff_dir = root / "diffs" / run_folder
        diff_dir.mkdir(parents=True, exist_ok=True)
        diff_path = diff_dir / f"{safe}.diff.png"
        try:
            diff_img.save(diff_path)
        except OSError:
            diff_path = None

    metrics.increment(
        "pw_visual_diff_total",
        labels={"project": project_slug, "result": verdict},
    )
    metrics.record_duration(
        "pw_visual_diff_pct",
        diff_percent,
        labels={"project": project_slug},
    )

    return DiffResult(
        test_case_id=test_case_id,
        step_label=step_label,
        baseline_exists=True,
        pixel_diff_count=diff_count,
        total_pixels=total,
        diff_percent=diff_percent,
        threshold=threshold,
        verdict=verdict,
        diff_path=diff_path,
        current_path=current_path,
        baseline_path=baseline_path,
    )


def promote_current_to_baseline(
    project_slug: str,
    test_case_id: str,
    step_label: str,
) -> bool:
    """User clicked "Update baseline" -- copy the latest current/<file>
    into baselines/<file>. Returns True if the promotion succeeded.

    Idempotent: running it twice is a no-op the second time (mtime
    update only). If there's no current/<file> we return False so the
    UI can warn instead of silently doing nothing.
    """
    safe = _safe_filename(test_case_id, step_label)
    root = _project_screenshot_root(project_slug)
    current = root / "current" / f"{safe}.png"
    baseline = root / "baselines" / f"{safe}.png"
    if not current.is_file():
        return False
    baseline.parent.mkdir(parents=True, exist_ok=True)
    try:
        import shutil
        shutil.copy2(current, baseline)
        metrics.increment("pw_visual_baseline_promoted_total", labels={"project": project_slug})
        return True
    except OSError as exc:
        logger.warning("baseline promote failed: %s", exc)
        return False


def list_pending_baselines(project_slug: str) -> list[dict]:
    """Return a list of test_case+step pairs whose ``current/`` differs
    from ``baselines/`` -- the candidates the "Pending baselines" UI
    panel renders as accept/reject rows.

    Each entry is a small dict the frontend can render directly:

        {
          "test_case_id": "<uuid>",
          "step_label": "after_login",
          "baseline_path": "...png",
          "current_path": "...png",
          "diff_percent": 0.034,
          "is_new": false,
        }
    """
    root = _project_screenshot_root(project_slug)
    current_dir = root / "current"
    baseline_dir = root / "baselines"
    if not current_dir.is_dir():
        return []

    pending: list[dict] = []
    for current in sorted(current_dir.glob("*.png")):
        safe = current.stem
        # Decompose ``<id>__<step>`` -- best effort. If filename has no
        # __, treat the whole thing as id with default step.
        if "__" in safe:
            test_case_id, step_label = safe.split("__", 1)
        else:
            test_case_id, step_label = safe, "default"
        baseline = baseline_dir / current.name
        is_new = not baseline.is_file()
        diff_pct = 1.0 if is_new else _quick_diff(baseline, current)
        # Only list rows where there's actually drift; clean rows are
        # not "pending".
        if not is_new and diff_pct <= DEFAULT_PIXEL_THRESHOLD:
            continue
        pending.append({
            "test_case_id": test_case_id,
            "step_label": step_label,
            "baseline_path": str(baseline) if baseline.is_file() else None,
            "current_path": str(current),
            "diff_percent": round(diff_pct, 4),
            "is_new": is_new,
        })
    return pending


def _quick_diff(a: Path, b: Path) -> float:
    """Cheap pixel-diff percent. Used by the "pending baselines" listing
    so we don't run the full pixelmatch on every file. Returns 0.0 on
    error -- the listing will then drop the row, which is the safe
    default. ``pixelmatch`` is an optional dep; see the lazy-import
    note in ``capture_baseline`` for the same rationale."""
    try:
        from PIL import Image  # type: ignore[import-not-found]  # pylint: disable=import-error
        from pixelmatch.contrib.PIL import (
            pixelmatch,  # type: ignore[import-not-found]  # pylint: disable=import-error
        )

        img_a = Image.open(a).convert("RGBA")
        img_b = Image.open(b).convert("RGBA")
        if img_a.size != img_b.size:
            target = (max(img_a.size[0], img_b.size[0]), max(img_a.size[1], img_b.size[1]))
            img_a = img_a.resize(target)
            img_b = img_b.resize(target)
        diff = Image.new("RGBA", img_a.size)
        n = pixelmatch(img_a, img_b, diff, threshold=0.1, includeAA=False)
        total = img_a.size[0] * img_a.size[1]
        return n / max(total, 1)
    except Exception:  # pylint: disable=broad-exception-caught
        return 0.0
