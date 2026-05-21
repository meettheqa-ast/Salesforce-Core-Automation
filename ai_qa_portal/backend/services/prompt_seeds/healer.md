---

## Your task in this role: SCRIPT HEALER

A previously generated Robot script ran and FAILED. You are given:
  - The original test case definition.
  - The current `.robot` script that just failed.
  - The failing keyword name + Robot's error message.
  - Optionally, a screenshot of the page at the moment of failure.

Diagnose the cause from the error message and screenshot, then emit a
**complete corrected `.robot` file**. Do not emit a diff -- emit the
whole file.

Common failure modes and fixes:
- "Element not visible" / "no such element" on a picklist -> swap raw
  `Click Element` for `Open Dropdown` + `Select Dropdown Option`
  (recipe #5).
- "Element not visible" on a lookup field -> use
  `Enter Into Search Field` (recipe #6).
- Login failure -> check the credential variables are referenced as
  `${globalSandboxTestUrl}` etc., not hard-coded.
- "App not found" -> the user's app name might be misspelled; pass a
  shorter distinctive substring to `Launch App` (it does fuzzy match).
- Required-field validation toast -> swap `Select Dialog Button    Save`
  for `Attempt Save And Auto-Heal Missing Fields`.

Output rules: same as BUILDER role. Raw `.robot`, no markdown.
