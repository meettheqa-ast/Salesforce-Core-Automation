*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/PO/Platform/SalesPO.robot

Documentation       Smoke — Opportunity full lifecycle: Create → Verify → Update Stage → Delete.
...                 Requires an existing Account (set opportunityAccountName in SalesData or -v).
...                 Override app name: robot -v smokeAppName:"My App"
Test Setup          Begin Web Test
Test Teardown       End Web Test


*** Variables ***
${smokeAppName}     ${salesAutomationAppName}


*** Test Cases ***
Smoke Opportunity Full Lifecycle
    [Documentation]    Smoke test: Navigate to Opportunities, create a new Opportunity linked
    ...                to an existing Account, verify the record page, update the Stage via
    ...                the header Edit action, then delete the Opportunity.
    [Tags]    smoke    opportunity    lifecycle
    # --- Login ---
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    # --- Navigate ---
    Launch App    ${smokeAppName}
    Select App Tab    Opportunities
    Open New Dialog    Opportunity
    # --- Create ---
    SalesPO.Create A New Opportunity
    SalesPO.Verify Opportunity
    # --- Edit: update Stage ---
    Perform Action On Record Details Page Header    Opportunity    Edit
    Open Dropdown And Select First Option    Stage
    Select Dialog Button    Save
    Wait Until Element Is Visible    ${successToastMessageLocator}
    Wait Until Element Is Not Visible    ${successToastMessageLocator}
    # --- Delete ---
    SalesPO.Delete Opportunity
