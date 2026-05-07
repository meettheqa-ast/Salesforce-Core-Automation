*** Settings ***
Documentation       Page-object keywords for the Salesforce Campaign UI.
...
...                 Mirrors the structure of ``SalesPO.robot``: a navigation entry
...                 point (``Open New Campaign From App``), a single-keyword form
...                 filler (``Create A New Campaign``), a verification keyword,
...                 and a delete keyword. All defaults come from
...                 ``Resources/TestData/Platform/SalesData.robot`` so callers can
...                 invoke each keyword with zero arguments and still get
...                 reproducible random data.
...
...                 If you only need a Campaign as a *prerequisite* (not the
...                 thing under test), prefer ``GlobalApi.API Seed Campaign`` --
...                 it is ~10x faster and avoids UI flakiness.

Library             SeleniumLibrary
Resource            ../../TestData/Platform/SalesData.robot
Resource            ../../Common/GlobalKeywords.robot


*** Keywords ***
Open New Campaign From App
    [Documentation]    Opens the given app (default ``Sales``) → **Campaigns** → **New**.
    ...                Pass a custom app name when the user specifies one (e.g. ``Pentair Sales``).
    ...                ``Open New Dialog`` handles any "Choose Record Type" picker centrally --
    ...                the pre-selected default is auto-confirmed when the picker appears.
    [Tags]    campaign    navigation
    [Arguments]    ${app_name}=${salesAutomationAppName}
    Launch App    ${app_name}
    Select App Tab    Campaigns
    Open New Dialog    Campaign

Create A New Campaign
    [Documentation]    Fills the New Campaign modal and saves. Campaign Name is the only
    ...                field that is universally required, so it uses strict ``Enter Text``.
    ...                Type and Status are picklists with org-default fallback. Active is
    ...                a checkbox that defaults to checked (Campaigns are useless when
    ...                inactive).
    ...
    ...                Optional named overrides (``name``, ``type``, ``status``) let callers
    ...                pass values from the user prompt without redefining ``${campaignName}``
    ...                etc. in ``*** Variables ***``. Suite variables are kept in sync so
    ...                downstream verification keywords pick up the same value. Call with
    ...                **zero args** to use ``SalesData.robot`` defaults.
    [Tags]    campaign    create
    [Arguments]    ${name}=${campaignName}    ${type}=${campaignType}    ${status}=${campaignStatus}
    Set Suite Variable    ${campaignName}    ${name}
    Set Suite Variable    ${campaignType}    ${type}
    Set Suite Variable    ${campaignStatus}    ${status}
    Enter Text    Campaign Name    ${campaignName}
    ${type_open}=    Run Keyword And Return Status    Open Dropdown    Type
    IF    ${type_open}
        ${ok}=    Run Keyword And Return Status    Select Dropdown Option    Type    ${campaignType}
        IF    not ${ok}
            Log    "${campaignType}" not in Type picklist on this org -- using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    END
    ${status_open}=    Run Keyword And Return Status    Open Dropdown    Status
    IF    ${status_open}
        ${ok}=    Run Keyword And Return Status    Select Dropdown Option    Status    ${campaignStatus}
        IF    not ${ok}
            Log    "${campaignStatus}" not in Status picklist on this org -- using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    END
    Attempt Save And Auto-Heal Missing Fields

Verify Campaign Created Successfully
    [Documentation]    Confirms the Campaign was created via the success toast on the
    ...                record-details page, then validates the Campaign Name on screen.
    [Tags]    campaign    verification
    [Arguments]    ${name}=${campaignName}
    Get Success Toast Message Related Record Creation ID
    Verify Redirection to Record Details Page    ${name}
    Verify Record Creation With Data    Campaign    Name    ${name}

Delete Campaign
    [Documentation]    Deletes the currently-open Campaign record via the standard
    ...                record-details "Delete" header action. The ``${name}`` argument
    ...                is informational -- it is NOT used to look up the record; the
    ...                keyword always deletes whatever Campaign page is currently open.
    [Tags]    campaign    teardown
    [Arguments]    ${name}=${campaignName}
    Delete Current Record    Campaign
