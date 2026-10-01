from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger(__name__)


def _frontdoor_variables(org_alias: str | None = None) -> list[str]:
    """Mirror runs._frontdoor_variable_override for ScriptRunner subprocesses:
    resolve a CLI OAuth frontdoor.jsp session (bypasses MFA/SSO) via
    sf_session_bootstrap and inject it as ``--variable sandboxFrontdoorUrl:<url>``.
    Uses an explicit alias when supplied, else falls back to the
    SF_DX_ORG_ALIAS env var. Never fatal -- any missing alias / CLI / expired
    auth just skips the override.
    """
    alias = (org_alias or "").strip() or (os.getenv("SF_DX_ORG_ALIAS") or "").strip()
    if not alias:
        return []
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from sf_session_bootstrap import get_frontdoor_url
        url = get_frontdoor_url(alias)
    except Exception as exc:
        logger.warning("CLI OAuth frontdoor login unavailable for alias '%s': %s", alias, exc)
        return []
    return ["--variable", f"sandboxFrontdoorUrl:{url}"]


def _manual_login_variable(manual_login: bool) -> list[str]:
    """Injects ``--variable sandboxManualLogin:true`` so `Login To Sandbox`
    pauses for a human to complete login manually."""
    return ["--variable", "sandboxManualLogin:true"] if manual_login else []


class ScriptRunner:
    def __init__(self, output_dir: str = "./outputs"):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        test_path: str,
        username: str,
        password: str,
        login_url: str,
        *,
        headless: bool = True,
        org_alias: str | None = None,
        manual_login: bool = False,
    ) -> tuple[UUID, Path]:
        """Launch Robot Framework in a subprocess and return (run_id, log_path) immediately.

        The process runs in the background; the caller can monitor the log file.
        """
        run_id = uuid4()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self._output_dir / f"run_{ts}_{run_id.hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "execution.log"

        cmd = [
            sys.executable, "-m", "robot",
            "--outputdir", str(run_dir),
            "--variable", f"globalSandboxTestUrl:{login_url}",
            "--variable", f"sandboxUserNameInput:{username}",
            "--variable", f"sandboxPasswordInput:{password}",
        ]
        if headless:
            cmd.extend(["--variable", "headless:true"])
        cmd.extend(_frontdoor_variables(org_alias))
        cmd.extend(_manual_login_variable(manual_login))

        cmd.append(test_path)

        logger.info("Starting Robot run %s: %s", run_id, " ".join(cmd))

        with open(log_path, "w", encoding="utf-8") as log_file:
            subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(Path(test_path).resolve().parent.parent.parent),
            )

        return run_id, log_path
