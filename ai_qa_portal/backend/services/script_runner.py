from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


def _login_mode_variables() -> list[str]:
    """Mirror runs._login_mode_overrides for ScriptRunner subprocesses."""
    mode = (os.getenv("ROBOT_LOGIN_MODE") or "auto").strip().lower() or "auto"
    if mode not in ("auto", "frontdoor", "ui"):
        mode = "auto"
    extras = ["--variable", f"LOGIN_MODE:{mode}"]
    alias = (os.getenv("SF_DX_ORG_ALIAS") or "").strip()
    if alias:
        extras.extend(["--variable", f"SF_DX_ORG_ALIAS:{alias}"])
    return extras


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
        cmd.extend(_login_mode_variables())

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
