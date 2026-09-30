# Salesforce Login Automation — CLI OAuth + Frontdoor Session Bootstrap

Automated test runs used to get stuck at the Salesforce login form whenever
the org challenged with MFA/OTP or SSO — neither the Robot/Selenium login
keyword nor the Playwright login path can complete that challenge on their
own. This feature removes that blocker: you complete MFA/SSO **once**,
interactively, via the Salesforce CLI, and every automated run afterward
bootstraps its browser session directly from that CLI session — no login
form, no MFA prompt.

This is fully opt-in. If you don't configure an org alias, nothing changes:
tests keep using username/password login exactly as before.

## How it works

1. You run `sf org login web` once. This opens a real browser, you log in
   and complete MFA/SSO yourself. The CLI stores an OAuth refresh token
   locally on your machine.
2. On every automated run, the automation asks the CLI for a fresh access
   token (`sf org display --json --target-org <alias>`) and navigates the
   browser straight to:
   ```
   <instanceUrl>/secur/frontdoor.jsp?sid=<accessToken>
   ```
   Salesforce already trusts that token, so this logs the browser in
   without ever showing the login form or an MFA prompt.
3. If the CLI has no valid session for that alias (never authenticated, or
   the refresh token expired/was revoked), automation **falls back
   automatically** to the existing username/password login. You'll see a
   warning telling you to re-run `sf org login web`.

## Prerequisites

- [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli)
  (`sf`) installed and on your `PATH`. Verify with:
  ```bash
  sf --version
  ```

## One-time setup

Authenticate against your sandbox (replace the alias and instance URL with
your own):

```bash
sf org login web --alias qa-sandbox --instance-url https://yourorg--sbx.sandbox.my.salesforce.com
```

This opens a browser window. Log in and complete MFA/SSO as you normally
would. Once it succeeds, the CLI has a locally stored refresh token for the
`qa-sandbox` alias.

Verify it worked:

```bash
sf org display --target-org qa-sandbox
```

You should see the org's instance URL and your username. No further action
is needed until this session itself expires or is revoked (see below).

## Enabling it in the app

In the credentials panel (workspace header or a project's Credentials
section), fill in the new **"SF CLI org alias (optional — bypasses login
form + MFA/SSO)"** field with the alias you used above (e.g. `qa-sandbox`),
then click **Save Credentials**.

Use **🔌 Test CLI Session** to confirm the app can read a valid session for
that alias before running any tests — it reports the instance URL on
success, or a clear message telling you to re-run `sf org login web` if the
CLI session isn't valid.

Leave the field blank to keep using username/password login only.

## What happens when the CLI session expires

Refresh tokens can expire or be revoked (by org policy, an admin, or you
running `sf org logout`). When that happens:

- Automated runs **do not fail** — they detect the invalid CLI session and
  fall back to username/password login automatically.
- You'll see a warning in the run output naming the alias and the exact
  `sf org login web ...` command to run again.
- Re-run the one-time setup command above to restore the CLI OAuth path.

## Don't want to use the CLI? Log in manually instead

If you'd rather not set up the Salesforce CLI at all, there's a simpler
opt-in: **Manual login**. Enable the **"Manual login (I'll log in myself —
any credentials, MFA, SSO)"** checkbox next to the CLI org alias field in the
credentials panel, then Save Credentials.

With this on, automated runs skip both the CLI OAuth session and
username/password autofill entirely. The browser just opens the plain
Salesforce login page and pauses with an on-screen prompt — you log in
yourself, exactly as you would by hand (username/password, MFA, SSO,
whatever your org requires) — and the run continues automatically once
Salesforce finishes loading.

Trade-offs versus the CLI OAuth approach above:

- **No CLI setup required** — nothing to install, nothing to re-authenticate
  when a refresh token expires.
- **Requires a human at the keyboard** for every run — it does not let you
  run unattended or in CI, since something has to click through the login
  page each time.
- **Requires a non-headless run** — a headless browser has no window for you
  to interact with, and the pause dialog itself needs a visible desktop.

Manual login takes precedence over both the CLI OAuth session and
username/password login when enabled — turn it off to go back to either of
those.

## Security note

The Salesforce CLI's local authentication store is itself a bearer
credential — anyone with access to it can act as you in that org, the same
as a saved password. Protect the machine it lives on accordingly, and run
`sf org logout --target-org <alias>` when you no longer need CLI access to
that org (e.g. offboarding, shared/temporary machines).

The automation itself never logs or persists the access token or the full
frontdoor URL anywhere beyond the already-gitignored, ephemeral files it
already used for credentials (`Resources/TestData/EnvData.robot`,
`_local_data/pw_storage/*.json`).
