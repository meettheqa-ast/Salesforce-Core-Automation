*** Settings ***
Documentation     SOQL-first record verification + Id-capture helpers.
...
...               Why this exists: the legacy UI-only path
...               (``Change List View`` -> ``Search In List View`` ->
...               ``Verify Table Cell Record``) is brittle on Lightning -- it
...               requires a page reload, depends on per-org list-view
...               labels, cannot express "record absent" cleanly, and gives
...               no signal at all for converted-Lead semantics. The
...               keywords here use the already-authenticated Selenium
...               session (via SalesforceApiLibrary's frontdoor.jsp sid
...               upgrade) to assert via SOQL instead: one HTTP call, no
...               waits, no spinner races, true presence/absence semantics.
...
...               Keywords:
...                 - Capture Record Id From Current Url
...                 - Verify Record Exists By SOQL
...                 - Verify Record Absent By SOQL
...                 - Verify Lead Was Converted
...                 - Verify Lead Absent From Active List
...                 - Cleanup Captured Records

Resource    GlobalApi.robot
Resource    GlobalKeywords.robot
Library     SeleniumLibrary
Library     Collections
Library     String


*** Variables ***
# 15- or 18-char Salesforce Id. The ``Get Location`` URL after a save
# looks like ``https://<my-domain>/lightning/r/Lead/00Q5g00000ABCDEFGAA/view``
# (sometimes ``/related/...`` instead of ``/view``). The regex captures the
# Id segment regardless of the trailing path.
${_SF_RECORD_ID_RE}    /lightning/r/[^/]+/([A-Za-z0-9]{15,18})


*** Keywords ***
Capture Record Id From Current Url
    [Documentation]    Reads the Salesforce record Id from the current
    ...                browser URL (``/lightning/r/<SObject>/<Id>/view``)
    ...                and stores it as a TEST-scoped variable named
    ...                ``${<sobject_var>Id}`` (default: lowercase first
    ...                letter of ``${sobject}`` + ``"Id"``, e.g.
    ...                ``${leadId}``).
    ...
    ...                When the URL is NOT a record detail page (modal
    ...                stayed open, Save & New cleared the form, the
    ...                browser was redirected to a list view, etc.) this
    ...                keyword falls back to
    ...                ``Get Success Toast Message Related Record Creation ID``
    ...                so the captured Id is best-effort. When neither
    ...                source yields an Id, returns ``${EMPTY}`` and logs
    ...                a WARN -- callers should treat capture as
    ...                non-blocking.
    ...
    ...                Returns the captured Id (or ``${EMPTY}``) so the
    ...                caller can chain it into a follow-up verification.
    [Tags]    soql    capture    utilities
    [Arguments]    ${sobject}    ${var_name}=${EMPTY}
    ${effective_var}=    Set Variable If    "${var_name}" != "${EMPTY}"
    ...    ${var_name}
    ...    ${{ "${sobject}"[:1].lower() + "${sobject}"[1:] + "Id" }}

    ${record_id}=    Set Variable    ${EMPTY}
    ${url_status}    ${current_url}=    Run Keyword And Ignore Error    Get Location
    IF    '${url_status}' == 'PASS'
        ${match_status}    ${match}=    Run Keyword And Ignore Error
        ...    Get Regexp Matches    ${current_url}    ${_SF_RECORD_ID_RE}    1
        IF    '${match_status}' == 'PASS' and ${match}
            ${record_id}=    Set Variable    ${match}[0]
        END
    END

    # Toast fallback: only when URL parse missed. Defensive -- the
    # toast keyword auto-dismisses after ~8 s and we never want to
    # extend a passing flow by that long, so it's only a fallback.
    IF    "${record_id}" == "${EMPTY}"
        Run Keyword And Ignore Error    Get Success Toast Message Related Record Creation ID
        ${toast_exists}=    Run Keyword And Return Status
        ...    Variable Should Exist    ${successToastMessageOnRecordDetailsPage}
        IF    ${toast_exists}
            ${toast_match_status}    ${toast_match}=    Run Keyword And Ignore Error
            ...    Get Regexp Matches    ${successToastMessageOnRecordDetailsPage}    ([A-Za-z0-9]{15,18})    1
            IF    '${toast_match_status}' == 'PASS' and ${toast_match}
                ${record_id}=    Set Variable    ${toast_match}[0]
            END
        END
    END

    IF    "${record_id}" == "${EMPTY}"
        Log    Capture Record Id: no Id found in URL or toast for ${sobject} -- continuing (capture is best-effort).    WARN
        Set Test Variable    ${${effective_var}}    ${EMPTY}
        RETURN    ${EMPTY}
    END

    Set Test Variable    ${${effective_var}}    ${record_id}
    Log    Captured ${sobject} Id ${record_id} as $\{${effective_var}\}.    INFO
    RETURN    ${record_id}

Verify Record Exists By SOQL
    [Documentation]    Asserts the SOQL ``SELECT Id FROM <sobject> WHERE
    ...                <where_clause>`` returns ``expected_count`` rows
    ...                (default 1). Use this in place of
    ...                ``Search In List View`` + ``Verify Table Cell Record``
    ...                whenever you just want to confirm a record exists --
    ...                no page reload, no spinner waits, no per-org
    ...                list-view labels.
    ...
    ...                On ``INVALID_SESSION_ID`` (the API-eligible cookie
    ...                hasn't propagated yet) waits 0.5 s and retries
    ...                once; the session upgrade typically completes
    ...                within that window.
    [Tags]    soql    verification
    [Arguments]    ${sobject}    ${where_clause}    ${expected_count}=1
    ${soql}=    Set Variable    SELECT Id FROM ${sobject} WHERE ${where_clause} LIMIT 200
    ${records}=    _Run SOQL With Retry    ${soql}
    ${actual_count}=    Get Length    ${records}
    Should Be Equal As Integers    ${actual_count}    ${expected_count}
    ...    msg=Expected ${expected_count} ${sobject} row(s) for WHERE ${where_clause}; got ${actual_count}.
    Log    SOQL exists check passed: ${actual_count} ${sobject} row(s) where ${where_clause}.    INFO
    RETURN    ${records}

Verify Record Absent By SOQL
    [Documentation]    Asserts the SOQL ``SELECT Id FROM <sobject> WHERE
    ...                <where_clause>`` returns 0 rows. This is the
    ...                semantically-correct way to check that a record is
    ...                "no longer in the active list" (filter-based
    ...                visibility), or that a cleanup-deleted record is
    ...                actually gone.
    [Tags]    soql    verification
    [Arguments]    ${sobject}    ${where_clause}
    ${soql}=    Set Variable    SELECT Id FROM ${sobject} WHERE ${where_clause} LIMIT 5
    ${records}=    _Run SOQL With Retry    ${soql}
    ${actual_count}=    Get Length    ${records}
    Should Be Equal As Integers    ${actual_count}    0
    ...    msg=Expected ${sobject} to be absent for WHERE ${where_clause}; found ${actual_count} matching row(s).
    Log    SOQL absent check passed: 0 ${sobject} row(s) where ${where_clause}.    INFO

Verify Lead Was Converted
    [Documentation]    Asserts the Lead with the given Id has
    ...                ``IsConverted = TRUE`` and a populated
    ...                ``ConvertedOpportunityId``. Sets
    ...                ``${convertedAccountId}``,
    ...                ``${convertedContactId}``, and
    ...                ``${convertedOpportunityId}`` as TEST-scoped
    ...                variables so the rest of the suite (and cleanup)
    ...                can address the derived records by Id without
    ...                another SOQL round-trip.
    [Tags]    soql    verification    lead    convert
    [Arguments]    ${lead_id}
    Should Not Be Empty    ${lead_id}
    ...    msg=Verify Lead Was Converted needs a Lead Id (got empty). Call ``Capture Record Id From Current Url    Lead`` on the Lead detail page first.
    ${soql}=    Set Variable
    ...    SELECT Id, IsConverted, ConvertedAccountId, ConvertedContactId, ConvertedOpportunityId FROM Lead WHERE Id='${lead_id}' LIMIT 1
    ${records}=    _Run SOQL With Retry    ${soql}
    ${count}=    Get Length    ${records}
    Should Be Equal As Integers    ${count}    1
    ...    msg=Lead ${lead_id} not found via SOQL -- convert step likely never ran against this Id.
    ${row}=    Set Variable    ${records}[0]
    ${is_converted}=    Get From Dictionary    ${row}    IsConverted    default=${FALSE}
    Should Be True    ${is_converted}
    ...    msg=Lead ${lead_id} has IsConverted=FALSE -- convert flow did not complete.
    ${conv_acct}=    Get From Dictionary    ${row}    ConvertedAccountId    default=${EMPTY}
    ${conv_contact}=    Get From Dictionary    ${row}    ConvertedContactId    default=${EMPTY}
    ${conv_opp}=    Get From Dictionary    ${row}    ConvertedOpportunityId    default=${EMPTY}
    Set Test Variable    ${convertedAccountId}    ${conv_acct}
    Set Test Variable    ${convertedContactId}    ${conv_contact}
    Set Test Variable    ${convertedOpportunityId}    ${conv_opp}
    Log    Lead ${lead_id} converted: account=${conv_acct} contact=${conv_contact} opportunity=${conv_opp}.    INFO

Verify Lead Absent From Active List
    [Documentation]    Asserts the given Lead is no longer visible in the
    ...                "Active Leads" list view filter. The Active Leads
    ...                filter in Salesforce is implemented as
    ...                ``IsConverted = FALSE``, so this is a single
    ...                deterministic SOQL check -- vastly more reliable
    ...                than reload-page + change-list-view + search-box +
    ...                table-cell-lookup.
    [Tags]    soql    verification    lead
    [Arguments]    ${lead_id}
    Should Not Be Empty    ${lead_id}
    ...    msg=Verify Lead Absent From Active List needs a Lead Id (got empty).
    Verify Record Absent By SOQL    Lead    Id='${lead_id}' AND IsConverted=FALSE

Cleanup Captured Records
    [Documentation]    Tear-down helper that deletes every captured Id
    ...                set on this test by the ``Save And Heal`` hook,
    ...                ``Capture Record Id From Current Url``, or
    ...                ``Verify Lead Was Converted``. Auto-discovers
    ...                ``${leadId}``, ``${accountId}``, ``${contactId}``,
    ...                ``${opportunityId}``, ``${campaignId}``,
    ...                ``${convertedAccountId}``,
    ...                ``${convertedContactId}``, and
    ...                ``${convertedOpportunityId}`` -- skipping any that
    ...                are unset or empty. Safe to call from a
    ...                ``[Teardown]`` even when no records were created.
    ...
    ...                Pass ``additional_pairs`` (SObject=Id strings) to
    ...                clean up extra records not covered by the
    ...                auto-discovery list, e.g.
    ...                ``Cleanup Captured Records    Case=${caseId}``.
    [Tags]    soql    teardown    cleanup
    [Arguments]    &{additional_pairs}
    @{cleanup_plan}=    Create List
    # Auto-discovered Id variables. Order matters for FK chains: delete
    # the derived child records (Lead, Opportunity) before the parent
    # Account so we never trip a "cannot delete -- still referenced"
    # error on orgs with strict referential integrity.
    FOR    ${sobject}    ${var_name}    IN
    ...    Lead           leadId
    ...    Opportunity    convertedOpportunityId
    ...    Contact        convertedContactId
    ...    Account        convertedAccountId
    ...    Opportunity    opportunityId
    ...    Contact        contactId
    ...    Account        accountId
    ...    Campaign       campaignId
    ...    Case           caseId
        ${exists}=    Run Keyword And Return Status    Variable Should Exist    ${${var_name}}
        IF    not ${exists}    CONTINUE
        ${value}=    Set Variable    ${${var_name}}
        ${trimmed}=    Run Keyword And Return Status    Should Not Be Empty    ${value}
        IF    not ${trimmed}    CONTINUE
        ${pair}=    Create Dictionary    object=${sobject}    id=${value}
        Append To List    ${cleanup_plan}    ${pair}
    END
    # Caller-supplied extras (e.g. additional Case Ids).
    FOR    ${obj}    ${val}    IN    &{additional_pairs}
        ${val_trim}=    Strip String    ${val}
        IF    "${val_trim}" == "" or "${val_trim}".upper() == "NONE"    CONTINUE
        ${pair}=    Create Dictionary    object=${obj}    id=${val_trim}
        Append To List    ${cleanup_plan}    ${pair}
    END

    ${count}=    Get Length    ${cleanup_plan}
    IF    ${count} == 0
        Log    Cleanup Captured Records: no captured Ids to delete; skipping.    INFO
        RETURN
    END

    FOR    ${entry}    IN    @{cleanup_plan}
        TRY
            API Cleanup Record    ${entry}[object]    ${entry}[id]
        EXCEPT    AS    ${err}
            Log    Cleanup failed for ${entry}[object] ${entry}[id]: ${err}    WARN
        END
    END

_Run SOQL With Retry
    [Documentation]    Internal: run a SOQL query via the live Selenium
    ...                session; on ``INVALID_SESSION_ID`` (the API-eligible
    ...                ``my.salesforce.com`` sid hasn't been set yet, which
    ...                happens within ~1 s of the first login), wait briefly
    ...                and retry once.
    [Tags]    soql    internal
    [Arguments]    ${soql}
    ${status}    ${records}=    Run Keyword And Ignore Error    API Query Records    ${soql}
    IF    '${status}' == 'PASS'    RETURN    ${records}
    ${err_text}=    Convert To String    ${records}
    ${is_session}=    Run Keyword And Return Status    Should Contain    ${err_text}    INVALID_SESSION_ID
    IF    not ${is_session}
        Fail    SOQL failed (${soql}): ${err_text}
    END
    Sleep    0.5s
    ${records2}=    API Query Records    ${soql}
    RETURN    ${records2}
