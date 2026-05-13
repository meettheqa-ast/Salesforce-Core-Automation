# Scheduled runs

Each schedule binds **a target** (single test case, every case in a
story, every story in a sprint, or everything tagged X) to **a cron
expression** and **a runner** (where execution happens).

## Two runners

| Runner           | Where it runs                                              | Persistence on restart |
|------------------|------------------------------------------------------------|------------------------|
| `local`          | APScheduler inside the FastAPI process. Reuses the existing `run_test.build_robot_run` pipeline so behaviour matches a manual "Run". | Yes (jobstore on Postgres). Missed firings during downtime are NOT replayed (no infinite `misfire_grace_time`). |
| `github_actions` | The connected repo's workflow YAML. The portal regenerates the `schedule:` block every time you create/edit such a schedule. | Yes (GitHub schedules survive even if the portal is down). |

Pick `github_actions` for anything that absolutely must fire even when
the portal is being deployed. Pick `local` for the dev-friendly path
that doesn't require a GitHub repo connection.

## Lifecycle

1. **Create**: `POST /projects/{slug}/schedules`. Cron is validated
   with `croniter`. `github_actions` schedules require
   `github_repo_id` to be set.
2. **Save side-effect**: if the runner is `local`, APScheduler
   registers a cron trigger that calls `schedule_runner.execute`. If
   `github_actions`, the workflow YAML is rewritten in the linked repo
   so its `schedule:` block reflects the DB.
3. **Tick**: at each cron tick, `schedule_runner.execute` opens its own
   DB session, materialises the target into `.robot` files, resolves
   the persona, runs `robot` via subprocess, and writes the result
   into `runs` + `schedule_runs`.
4. **Run now**: same path as a cron tick; immediate.
5. **History**: `GET /projects/{slug}/schedules/{id}/runs` returns the
   most recent `schedule_runs` rows.

## GitHub Actions specifics

When the runner is `github_actions`:

* The portal triggers runs via `workflow_dispatch` with three inputs:
  `suite_path`, `persona_username`, and `schedule_run_id`. The first
  two map to the deterministic paths that `github_sync.push_project_suites`
  uses; the third is echoed back in `workflow_run` events so the
  webhook handler can update the right `schedule_runs` row.
* The workflow resolves the persona password from a repo secret named
  `PERSONA_<USER>_PASSWORD` (uppercased + non-alphanumeric collapsed
  to `_`). **Salesforce passwords therefore live in GitHub repo
  secrets when the GitHub Actions path is used** — this is the only
  channel where credentials leave the portal. Document this in your
  security review.
* The webhook handler validates HMAC-SHA256 against every stored
  connection's secret in constant time, then maps the `conclusion`
  field onto `schedule_runs.status`.

## Operational notes

* APScheduler in-process means a deploy restarts the scheduler. Jobs
  persist (Postgres jobstore), but any firing that would have happened
  during the restart window is skipped, not replayed.
* The thread-pool executor caps concurrency at
  `SCHEDULER_MAX_WORKERS` (default 4). Long Robot runs serialise per
  worker; multiply your max-runtime by 4 to size the pool.
* `SCHEDULER_ENABLED=false` disables the scheduler entirely — useful in
  worker-less / read-only deployments. `github_actions` schedules
  still execute on GitHub because they don't depend on the in-process
  scheduler.
