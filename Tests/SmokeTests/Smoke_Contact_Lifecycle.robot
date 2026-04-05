*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/PO/Platform/SalesPO.robot
Resource            ../../Resources/PO/Platform/ContactPO.robot

Documentation       Smoke — Contact full lifecycle: Create → Verify → Edit Title → Delete.
...                 Override app name: robot -v smokeAppName:"My App"
...                 Set contactAccountName to link to an existing Account, or leave empty.
Test Setup          Begin Web Test
Test Teardown       End Web Test


*** Variables ***
${smokeAppName}     ${salesAutomationAppName}


*** Test Cases ***
Smoke Contact Full Lifecycle
    [Documentation]    Smoke test: Open the Contacts tab, create a new Contact,
    ...                verify it on the record page, edit the Title, then delete.
    [Tags]    smoke    contact    lifecycle
    # --- Login ---
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    # --- Navigate & Create ---
    Launch App    ${smokeAppName}
    Select App Tab    Contacts
    Open New Dialog    Contact
    ContactPO.Create A New Contact
    ContactPO.Verify Contact Created Successfully
    # --- Edit Title ---
    ContactPO.Edit Contact Title    Updated Smoke Title
    # --- Delete ---
    ContactPO.Delete Contact
