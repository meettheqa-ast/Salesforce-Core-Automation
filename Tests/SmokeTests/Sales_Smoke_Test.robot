*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/PO/Platform/SalesPO.robot

Documentation       Smoke coverage for core Sales Cloud Lead creation (login → Sales → Leads → new Lead → verify).
Test Setup          Begin Web Test
Test Teardown       End Web Test


*** Test Cases ***
Smoke Sales Create Lead And Verify Record
    [Documentation]    End-to-end check that a Lead can be created and validated on the record page using shared keywords only.
    [Tags]    smoke    sales
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    GlobalKeywords.Launch App    Sales
    GlobalKeywords.Select App Tab    Leads
    GlobalKeywords.Convert View From Intelligent To List
    GlobalKeywords.Open New Dialog    Lead
    SalesPO.Create A New Lead
    SalesPO.Verify Lead Created Successfully
    SalesPO.Delete Lead
