# Recording a Salesforce test (user guide)

This page walks through the recording-mode workflow for end users -- testers who want to author a Robot Framework test by clicking through Salesforce instead of writing code.

## When to use Recording mode

- You know the FLOW (click here, fill in this, save, verify) but don't know which Page Object keywords reproduce it.
- You want a baseline test for a brand-new Salesforce feature.
- You're iterating fast and don't want to stop and write Robot syntax.

When NOT to use it: data-driven tests (loops, parametrised inputs), API-only tests, or anything that needs explicit assertions partway through (the recorder doesn't capture asserts -- it captures actions).

## Prerequisites

- Your portal admin has enabled `PW_RECORDING=true`.
- You've configured a workspace login (sandbox URL + username + password).
- Your Salesforce user can log in without MFA prompts (the auto-login can't handle MFA).

## Step-by-step

1. Open `/generate/record`. You'll see a `Pick a workspace login above first` notice if no creds are loaded; pick one from the workspace dropdown at the top of the page.

2. Click **Start Recording**. A browser window opens on the server (or your laptop if running locally). The portal auto-fills your Salesforce login.

3. **Click through your test scenario** in that browser. Examples:
   - Open the Sales app, click Leads, click New, fill in First/Last name, click Save.
   - Open an existing Account, click Edit, change a phone number, click Save.

4. Watch the **Captured actions** panel on the page. Each click / fill / navigation appears as a row -- numbered chronologically. Use this to verify the recorder is seeing what you're doing.

5. When done, return to the portal and click **Stop Recording**. The browser closes.

6. The page transitions to the post-record view. You'll see:
   - The **generated Robot script** on the left, ready to edit.
   - The **action log** on the right, for reference.
   - **Validation pills**: green "Validated" if the script passes AST + dryrun, red "Validation failed (N)" otherwise.

7. If validation passed, click **Open in Generate** -- you'll land on `/generate` with the script loaded, ready to Run, save, or attach to a story.

8. If validation failed, the LLM will have made a best-effort but used a wrong keyword somewhere. Either:
   - Edit the script in-place (the editor is full Monaco).
   - Or click **Discard** and re-record with a tighter, simpler flow.

## Tips

- **Keep flows focused**. A good recording is 5-15 actions. 50+ actions usually mean you're recording multiple tests in one session; split them.
- **Avoid navigation that doesn't matter**. The recorder captures every click. Browsing the AppLauncher to find Leads doesn't need to be in the test -- click directly to the Leads tab.
- **The translator prefers Page Objects**. When you click `New` on the Leads list, the recorder sees `click on button "New"`. The translator will translate this to `SalesPO.Open New Lead From Sales App` -- which handles the Lightning record-type picker, app navigation, and Lightning slot quirks for you.
- **Recording captures CLICKS, not assertions**. If you want to assert "the saved record has the right name", the translator inserts a verify keyword AT THE END based on the last action. For mid-flow assertions, edit the script after recording.

## What if the auto-login fails?

If the recording browser opens but stays stuck on the SF login page, the auto-login form fill failed (Salesforce occasionally changes the form structure for SSO orgs). Click **Cancel**, log in manually inside the browser, then click **Start Recording** again -- the existing session will be reused.

## How is this different from MCP Stepwise?

**MCP Stepwise** = "I describe what I want; the LLM plans steps and executes them via RF-MCP, building the script as it goes."

**Recording** = "I click through SF; the recorder captures actions; the LLM translates them to Robot."

Stepwise is better when you can describe the test in English ("Verify lead routing for CA, TX, NY"). Recording is better when you can't easily describe it, but you can do it.
