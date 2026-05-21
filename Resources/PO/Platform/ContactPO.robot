*** Settings ***
Library     SeleniumLibrary
Resource    ../../TestData/Platform/SalesData.robot
Resource    ../../Common/GlobalKeywords.robot
Resource    ../../Common/HealKeywords.robot


*** Keywords ***
Open New Contact From Sales App
    [Documentation]    Launches the given app (default ``Sales``) → **Contacts** → **New**. Pass a custom app name (e.g. ``Pentair Sales``) when the user specifies one; omit for the default Sales app. ``Open New Dialog`` handles the "Choose Record Type" picker centrally -- pre-selected default is auto-confirmed when the picker appears.
    [Tags]    navigation    contact
    [Arguments]    ${app_name}=${salesAutomationAppName}
    Launch App    ${app_name}
    Select App Tab    Contacts
    Open New Dialog    Contact

Create A New Contact
    [Documentation]    Fills the New Contact modal using SalesData contact variables. Uses Enter Text With Fallback for all text fields so custom orgs are handled gracefully. Salutation and other picklists use Open Dropdown And Select First Option for org-agnostic selection. Calls Save And Heal to run runtime healing with a legacy fallback path.
    ...
    ...    Optional named overrides (``first_name``, ``last_name``, ``email``, ``phone``, ``title``, ``account_name``) let callers pass values from the user prompt without redefining suite variables. Suite variables are kept in sync so verification picks up the same values. Call with ZERO args to use ``SalesData`` defaults.
    [Tags]    interaction    contact    modal
    [Arguments]    ${first_name}=${contactFirstName}    ${last_name}=${contactLastName}    ${email}=${contactEmail}    ${phone}=${contactPhone}    ${title}=${contactTitle}    ${account_name}=${contactAccountName}
    Set Suite Variable    ${contactFirstName}    ${first_name}
    Set Suite Variable    ${contactLastName}    ${last_name}
    Set Suite Variable    ${contactEmail}    ${email}
    Set Suite Variable    ${contactPhone}    ${phone}
    Set Suite Variable    ${contactTitle}    ${title}
    Set Suite Variable    ${contactAccountName}    ${account_name}
    Open Dropdown And Select First Option    Salutation
    Enter Text With Fallback    First Name    ${contactFirstName}
    Enter Text With Fallback    Last Name     ${contactLastName}
    Enter Text With Fallback    Title         ${contactTitle}
    Enter Text With Fallback    Email         ${contactEmail}
    Enter Text With Fallback    Phone         ${contactPhone}
    ${account_set}=    Run Keyword And Return Status    Should Not Be Empty    ${contactAccountName}
    IF    ${account_set}
        Enter Into Search Field    Account Name    ${contactAccountName}
    END
    Save And Heal    sobject=Contact

Verify Contact Created Successfully
    [Documentation]    Confirms the Contact was saved via the success toast, then verifies Last Name and Title are visible on the record page.
    [Tags]    verification    contact
    Get Success Toast Message Related Record Creation ID
    Page Should Contain    ${contactLastName}
    Verify Record Creation With Data    Contact    Title    ${contactTitle}

Edit Contact Title
    [Documentation]    Opens Edit on the current Contact record, updates the Title field, saves, and verifies the toast.
    [Tags]    interaction    contact
    [Arguments]    ${new_title}=Updated Smoke Title
    Perform Action On Record Details Page Header    Contact    Edit
    Enter Text With Fallback    Title    ${new_title}
    Select Dialog Button    Save
    Wait Until Element Is Visible    ${successToastMessageLocator}
    Wait Until Element Is Not Visible    ${successToastMessageLocator}

Delete Contact
    [Documentation]    Deletes the currently open Contact record and confirms the redirect.
    [Tags]    interaction    contact    delete
    Delete Current Record    Contact
