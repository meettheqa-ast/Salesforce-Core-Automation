"""Robot bridge library for a small pool of persistent Chrome profile dirs.

Why this exists: Salesforce's adaptive/risk-based MFA treats a Selenium
browser launched with a fresh throwaway profile as a brand-new, unrecognized
device on every single run -- so even a successful CLI OAuth frontdoor
session (see sf_session_bootstrap.py) can still get hit with a step-up
"choose a passkey" / Windows Hello challenge, because that's a native OS
dialog Selenium cannot click through no matter how the session was
established. Giving Chrome a *persistent* --user-data-dir lets Salesforce's
"remember this device" cookie survive across runs: a human approves the
passkey prompt once per profile, and that profile stops being challenged.

A single shared profile can't be reused by concurrent Chrome processes
(Chrome refuses to start a second instance against a profile dir that's
already locked by another instance), and this repo's bulk runner executes
multiple tests in parallel. So instead of one persistent profile, this pool
hands out one of a small, fixed number of persistent profile directories per
browser launch, each independently lockable, sized to the runner's actual
parallelism. Each slot still only needs the passkey approved once, ever
(barring Salesforce expiring device trust).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from robot.api import logger
from robot.api.deco import keyword

# Matches the bulk runner's default max-parallelism (see runs.py). Override
# with CHROME_PROFILE_POOL_SIZE if that default ever changes.
_DEFAULT_POOL_SIZE = 3

# A lock older than this is assumed to belong to a run that crashed without
# releasing its slot (e.g. killed process, machine restart) rather than one
# still legitimately in progress -- robot runs here are bounded well under
# this (900s execute timeout in runs.py), so 2 hours is a safe margin.
_STALE_LOCK_SECONDS = 2 * 60 * 60

_BASE_DIR = Path(
    os.environ.get("CHROME_PROFILE_POOL_DIR")
    or (Path.home() / ".sf-core-automation" / "chrome-profiles")
)


class ChromeProfilePoolLibrary:
    ROBOT_LIBRARY_SCOPE = "TEST SUITE"

    @keyword("Acquire Chrome Profile Dir")
    def acquire_chrome_profile_dir(self, pool_size: int = _DEFAULT_POOL_SIZE) -> str:
        """Claim one of a small pool of persistent Chrome profile dirs.

        Returns the directory's absolute path (to pass as --user-data-dir).
        Call `Release Chrome Profile Dir` with the same path in a teardown
        so the slot is freed for the next run, even on failure.
        """
        size = int(os.environ.get("CHROME_PROFILE_POOL_SIZE", pool_size) or pool_size)
        _BASE_DIR.mkdir(parents=True, exist_ok=True)

        for _pass in range(2):  # second pass: stale locks get a chance to be reclaimed
            for slot in range(size):
                slot_dir = _BASE_DIR / f"slot-{slot}"
                lock_file = slot_dir / ".lock"
                slot_dir.mkdir(parents=True, exist_ok=True)
                try:
                    # O_EXCL: atomically fails if another process already
                    # holds this slot, instead of racing on a check-then-act.
                    fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode("ascii"))
                    os.close(fd)
                    logger.info(f"Chrome profile pool: acquired {slot_dir}")
                    return str(slot_dir)
                except FileExistsError:
                    if _pass == 0 and self._is_stale(lock_file):
                        try:
                            lock_file.unlink()
                        except OSError:
                            pass
                    continue
        raise RuntimeError(
            f"Chrome profile pool: all {size} slot(s) under {_BASE_DIR} are locked by "
            "other in-progress runs. Increase CHROME_PROFILE_POOL_SIZE if you run more "
            "than that many browsers in parallel."
        )

    @keyword("Release Chrome Profile Dir")
    def release_chrome_profile_dir(self, profile_dir: str) -> None:
        """Free a slot acquired via `Acquire Chrome Profile Dir`. Safe to call
        even if acquire never succeeded (e.g. teardown after a setup failure).
        """
        if not profile_dir:
            return
        lock_file = Path(profile_dir) / ".lock"
        try:
            lock_file.unlink()
            logger.info(f"Chrome profile pool: released {profile_dir}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warn(f"Chrome profile pool: could not release {profile_dir}: {exc}")

    @staticmethod
    def _is_stale(lock_file: Path) -> bool:
        try:
            return (time.time() - lock_file.stat().st_mtime) > _STALE_LOCK_SECONDS
        except OSError:
            return False
