*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/PO/Platform/SalesPO.robot

Documentation       Smoke — Lead full lifecycle: Create → Verify → Edit Status → Convert.
...                 Override app name: robot -v smokeAppName:"My App"
Test Setup          Begin Web Test
Test Teardown       End Web Test


*** Variables ***
# Override with -v smokeAppName:"Your App Launcher Name"
${smokeAppName}     ${salesAutomationAppName}
${updatedTitle}     Smoke Updated Title


*** Test Cases ***
Smoke Lead Full Lifecycle
    [Documentation]    Smoke test: Create a Lead, verify it, edit its Title and Lead Status,
    ...                then Convert it. Uses org-agnostic picklist selection throughout.
    [Tags]    smoke    lead    lifecycle
    # --- Login ---
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    # --- Create ---
    Launch App    ${smokeAppName}
    Select App Tab    Leads
    Open New Dialog    Lead
    SalesPO.Create A New Lead
    SalesPO.Verify Lead Created Successfully
    # --- Edit: update Title and Lead Status ---
    Perform Action On Record Details Page Header    Lead    Edit
    Enter Text With Fallback    Title    ${updatedTitle}
    Open Dropdown And Select First Option    Lead Status
    Select Dialog Button    Save
    Wait Until Element Is Visible    ${successToastMessageLocator}
    Wait Until Element Is Not Visible    ${successToastMessageLocator}
    # --- Convert ---
    SalesPO.Convert Lead To Opportunity
