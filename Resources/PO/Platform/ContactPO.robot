*** Settings ***
Library     SeleniumLibrary
Resource    ../../TestData/Platform/SalesData.robot
Resource    ../../Common/GlobalKeywords.robot


*** Keywords ***
Open New Contact From Sales App
    [Documentation]    Launches the configured Sales app, navigates to the Contacts tab, and opens the New Contact dialog.
    [Tags]    navigation    contact
    Launch App    ${salesAutomationAppName}
    Select App Tab    Contacts
    Open New Dialog    Contact

Create A New Contact
    [Documentation]    Fills the New Contact modal using SalesData contact variables. Uses Enter Text With Fallback for all text fields so custom orgs are handled gracefully. Salutation and other picklists use Open Dropdown And Select First Option for org-agnostic selection. Calls Attempt Save And Auto-Heal Missing Fields to recover from custom required-field validation errors.
    [Tags]    interaction    contact    modal
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
    Attempt Save And Auto-Heal Missing Fields

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
