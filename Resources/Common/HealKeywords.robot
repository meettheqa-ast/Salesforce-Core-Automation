*** Settings ***
Library     ../../Libraries/HealBridgeLibrary.py
Resource    GlobalKeywords.robot
Resource    SoqlVerify.robot
Library     Collections


*** Keywords ***
Save And Heal
    [Documentation]    Click Save and run runtime form-healing when available.
    ...                If the bridge cannot run (e.g. no MCP session id in
    ...                standalone Robot replay), falls back to the legacy
    ...                Attempt Save And Auto-Heal Missing Fields keyword.
    ...
    ...                On a successful save this keyword ALSO calls
    ...                ``Capture Record Id From Current Url    ${sobject}``
    ...                so the rest of the test (verification, cleanup) can
    ...                address the new record by Id without re-finding it
    ...                in the UI. The capture is best-effort: when the
    ...                page didn't navigate to a record detail URL (e.g.
    ...                Save & New, modal stayed open) it logs WARN and
    ...                continues without setting a variable.
    [Arguments]    ${sobject}=Lead    ${max_attempts}=3    ${duplicate_strategy}=regenerate    ${session_id}=${EMPTY}    ${save_action}=Save
    ${status}    ${result}=    Run Keyword And Ignore Error
    ...    Heal Bridge Save And Heal
    ...    sobject=${sobject}
    ...    max_attempts=${max_attempts}
    ...    duplicate_strategy=${duplicate_strategy}
    ...    session_id=${session_id}
    ...    save_action=${save_action}
    IF    '${status}' != 'PASS'
        Log    Heal bridge call failed (${result}); using legacy fallback.    WARN
        Attempt Save And Auto-Heal Missing Fields
        Run Keyword And Ignore Error    Capture Record Id From Current Url    ${sobject}
        RETURN
    END
    ${outcome}=    Get From Dictionary    ${result}    outcome    default=skipped
    ${reason}=    Get From Dictionary    ${result}    reason    default=${EMPTY}
    IF    '${outcome}' == 'passed'
        Run Keyword And Ignore Error    Capture Record Id From Current Url    ${sobject}
        RETURN
    END
    IF    '${outcome}' == 'skipped'
        Log    Heal bridge skipped (${reason}); using legacy fallback.    WARN
        Attempt Save And Auto-Heal Missing Fields
        Run Keyword And Ignore Error    Capture Record Id From Current Url    ${sobject}
        RETURN
    END
    Fail    Save And Heal failed: ${outcome} (${reason})
