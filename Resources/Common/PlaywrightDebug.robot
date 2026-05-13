*** Settings ***
Documentation       Phase 4 -- optional Playwright trace capture for Robot suites.
...
...                 This resource file does NOT replace SeleniumLibrary as the runtime
...                 browser engine for the existing test suites. Selenium remains the
...                 default everywhere; everything in ``Resources/PO/`` keeps working
...                 byte-for-byte.
...
...                 What this file ADDS: an opt-in pre/post-suite hook that uses the
...                 robotframework-browser library (Playwright) to capture a
...                 ``trace.zip`` next to ``output.xml``. The trace is the
...                 industry-standard Playwright trace format -- viewable at
...                 https://trace.playwright.dev/ by uploading or pasting the URL.
...
...                 Activation: pass ``--variable USE_PLAYWRIGHT_TRACE:1`` on the robot
...                 command line. When the variable is unset (the default in the AI QA
...                 Portal's bulk runs), every keyword in this file becomes a no-op so
...                 there is zero runtime impact for the 99% case.
...
...                 Use case: a customer reports "the test fails sometimes". Run
...                 their suite once with the flag on, hand them the trace.zip, and
...                 they can scrub through every action / network call / DOM
...                 mutation in a browser. Far better debug story than parsing
...                 ``log.html``.
...
...                 Why this is its own resource file (not bundled into
...                 ``GlobalKeywords.robot``): the ``Browser`` library has its own
...                 setup cost (downloads its own playwright runtime) and we don't
...                 want to pay it for tests that aren't tracing. Importing this
...                 file is the opt-in.
Library             OperatingSystem
Library             String


*** Variables ***
# When this is "0" (default), every keyword below short-circuits. Set to
# "1" via ``--variable USE_PLAYWRIGHT_TRACE:1`` to activate.
${USE_PLAYWRIGHT_TRACE}=    0
# Where to drop trace.zip. Defaults to the runner's output dir so it's
# bundled with output.xml automatically. Override per-run if you want
# multiple traces in one results folder (use distinct names).
${PW_TRACE_FILENAME}=       trace.zip


*** Keywords ***
Start Playwright Trace If Enabled
    [Documentation]    Pre-suite hook. When ``${USE_PLAYWRIGHT_TRACE}`` is ``1``, imports
    ...                the Browser library and starts a tracing session that captures
    ...                screenshots + DOM snapshots + network. Otherwise no-op.
    ...
    ...                Robot suites that want tracing should call this from
    ...                ``Suite Setup`` (or piggyback the project's existing setup).
    [Tags]    debug    playwright    optional
    IF    '${USE_PLAYWRIGHT_TRACE}' != '1'
        Log    Playwright tracing disabled (set USE_PLAYWRIGHT_TRACE=1 to enable).    DEBUG
        RETURN
    END

    # Lazy import so projects that never enable tracing don't pay the
    # Browser library's startup cost.
    Import Library    Browser    AS    PWBrowser
    # New context with tracing on. ``trace_dir`` MUST be set BEFORE the
    # first context opens for the trace to capture; we pin it under
    # ``${OUTPUT_DIR}`` so the file ends up in the same folder Robot
    # already writes output.xml + log.html to.
    Run Keyword And Ignore Error    PWBrowser.New Browser    headless=${True}
    Run Keyword And Ignore Error    PWBrowser.New Context    tracing=${True}
    Log    Playwright tracing started (trace dir = ${OUTPUT_DIR}).    INFO

Stop Playwright Trace If Enabled
    [Documentation]    Post-suite hook. Counterpart to the start keyword. Stops the
    ...                tracing session, packages the events into ``trace.zip``, and
    ...                writes it under ``${OUTPUT_DIR}``. The runner's bundle
    ...                builder picks it up automatically.
    [Tags]    debug    playwright    optional
    IF    '${USE_PLAYWRIGHT_TRACE}' != '1'
        RETURN
    END

    # ``Stop Trace`` is the Browser library's keyword. We Run Keyword And
    # Ignore Error around the whole shutdown because if Browser failed
    # to import (e.g. the host doesn't have node), we still want the
    # rest of the teardown to run.
    Run Keyword And Ignore Error
    ...    PWBrowser.Stop Tracing    path=${OUTPUT_DIR}${/}${PW_TRACE_FILENAME}
    Run Keyword And Ignore Error    PWBrowser.Close Browser
    Log    Playwright trace saved to ${OUTPUT_DIR}${/}${PW_TRACE_FILENAME}.    INFO
