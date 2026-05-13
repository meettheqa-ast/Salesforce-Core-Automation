# Playwright-MCP troubleshooting

## "Playwright is not installed"

**Symptom**: `ImportError` from `pw_mcp_bridge.start_pw_runtime()`.

**Fix**:
```bash
pip install playwright>=1.49.0
python -m playwright install chromium --with-deps
```

In Docker: rebuild with the latest Dockerfile (the install step is part of the build).

## "browser type chromium not found"

**Symptom**: `playwright._impl._errors.Error: Executable doesn't exist at /home/.cache/ms-playwright/chromium-...`.

**Fix**: chromium binary wasn't installed. Run:
```bash
python -m playwright install chromium
```

## Locator validation passes nothing / always says "all_live"

**Symptom**: every generation shows "Locators 0/0 live" or "all live" but customers still see runtime locator failures.

**Likely causes**:
1. The .robot file uses only `${VARIABLE}` references, no literal locators. Variables aren't validated by Phase 1; they're vouched for by AST. Working as designed.
2. Salesforce login is failing silently and Playwright is checking locators against the login page, where everything fails. Check `pw_metrics.json` -- if `pw_locator_validate_total{result="all_stale"}` is high, this is the bug.

## Locator validation always says "stale"

**Symptom**: every generation reports stale locators even when the script works at runtime.

**Likely causes**:
1. **Wrong sandbox URL**. The validator hits whatever `body.sandbox_url` says; if it's pointing at production while the runtime tests against sandbox, every selector misses.
2. **Logged-out**. storageState expired and re-login failed. Check the Playwright runtime log for "wait_for_selector timed out". Drop the storageState and try again:
   ```python
   import pw_mcp_bridge
   pw_mcp_bridge.invalidate_cached_session(drop_storage=True)
   ```
3. **Page didn't load fully**. Lightning's lazy-load sometimes leaves elements unrendered for several seconds. The validator's per-locator timeout (4s) may be too short for complex layouts. Override via `PW_MCP_OP_TIMEOUT_MS=30000`.

## "ENOENT: spawn xdg-open" or similar in Docker

**Symptom**: Recording mode tries to open a browser window in the container.

**Fix**: Recording mode requires a HEADED browser, which doesn't work in containers. Either:
- Run the backend on a host with a display (developer laptop).
- Disable recording in production: `PW_RECORDING=false`.

## Visual regression always reports drift

**Symptom**: every test gets `VISUAL_DRIFT` even when nothing changed.

**Likely causes**:
1. **Viewport mismatch**. Baselines were captured at a different viewport than current runs. The diff service auto-resizes but at a small visual cost. To eliminate: capture both at the same viewport (default 1440x900 in `pw_mcp_bridge`).
2. **Animations or live data**. Salesforce sidebar timestamps, "Last activity 5 min ago"-style text, etc. shift between runs. Bump the threshold:
   ```python
   from ai_qa_portal.backend.services.visual_regression import diff_against_baseline
   # Per-call override -- the default is 0.01 (1%); 0.05 tolerates 5% pixel drift.
   diff_against_baseline(..., threshold=0.05)
   ```
3. **Salesforce released a UI change**. Real drift -- promote the baseline via the "Update baseline" button on `/projects/[name]`.

## Recording session "Failed to start"

**Symptoms + fixes**:
- 503 "Playwright not installed" -- see top of doc.
- 403 "Recording mode is not enabled" -- set `PW_RECORDING=true` in env.
- Hangs on Start -- the Salesforce login flow is timing out. Check whether the auto-login form selectors (`#username`, `#password`, `#Login`) still match the org. If your customer has SSO, recording isn't supported.

## Memory grows over time

**Likely cause**: BrowserContexts not being evicted. Each cached session is ~50-80MB; 10 stale sessions = ~700MB.

**Fix**: ensure `PW_MCP_SESSION_TTL_S` is set (default 600s -- 10 min). Confirm via:
```python
import pw_mcp_bridge
pw_mcp_bridge.invalidate_cached_session()  # nuke all
```

## "Could not connect to Salesforce"

The validator + recording require a live Salesforce login. If the sandbox is down, all Playwright phases skip (soft-fail). Check `pw_metrics.json` -- you'll see counters like `pw_mcp_session_create_total{result="error"}` spike.
