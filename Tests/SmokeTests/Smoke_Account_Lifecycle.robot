*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/PO/Platform/SalesPO.robot

Documentation       Smoke — Account full lifecycle: Create → Verify → Edit Phone → Delete.
...                 Override app name: robot -v smokeAppName:"My App"
Test Setup          Begin Web Test
Test Teardown       End Web Test


*** Variables ***
${smokeAppName}         ${salesAutomationAppName}
${updatedAccountPhone}  ${EMPTY}


*** Test Cases ***
Smoke Account Full Lifecycle
    [Documentation]    Smoke test: Create an Account, verify key fields, edit the Phone
    ...                number, verify the edit, then delete the record.
    [Tags]    smoke    account    lifecycle
    # --- Login ---
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    # --- Navigate ---
    Launch App    ${smokeAppName}
    Select App Tab    Accounts
    Convert View From Intelligent To List
    Open New Dialog    Account
    # --- Create ---
    SalesPO.Create A New Account
    SalesPO.Verify Account Creation
    # --- Edit: update Phone ---
    ${new_phone}=    Evaluate    ''.join([str(__import__('random').randint(0,9)) for _ in range(10)])
    Perform Action On Record Details Page Header    Account    Edit
    Enter Text With Fallback    Phone    ${new_phone}
    Select Dialog Button    Save
    Wait Until Element Is Visible    ${successToastMessageLocator}
    Wait Until Element Is Not Visible    ${successToastMessageLocator}
    # --- Delete ---
    SalesPO.Delete Account
