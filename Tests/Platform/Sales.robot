*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/PO/Platform/SalesPO.robot

Test Setup          Begin Web Test
Test Teardown       End Web Test
# Run the Script
# robot --timestampoutputs -d Results/Platform/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Tests/Platform/Sales.robot


*** Test Cases ***
Create a Lead and verify that Lead is created properly
    [Tags]    sales    smoke    regression
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    GlobalKeywords.Launch App    Sales
    GlobalKeywords.Select App Tab    Leads
    GlobalKeywords.Convert View From Intelligent To List
    GlobalKeywords.Open New Dialog    Lead
    SalesPO.Create A New Lead
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
    SalesPO.Verify First Name, Company And Title On Lead Page
    SalesPO.Delete Lead

Convert Lead To Opportunity and verify lead is convert to opportunity
    [Tags]    sales    smoke    regression
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    GlobalKeywords.Launch App    Sales
    GlobalKeywords.Select App Tab    Leads
    GlobalKeywords.Convert View From Intelligent To List
    GlobalKeywords.Open New Dialog    Lead
    SalesPO.Create A New Lead
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
    SalesPO.Convert Lead To Opportunity
    SalesPO.Delete Converted Lead

Create An Account
    [Tags]    sales    smoke    regression
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    GlobalKeywords.Launch App    Sales
    GlobalKeywords.Select App Tab    Accounts
    GlobalKeywords.Open New Dialog    Account
    GlobalKeywords.Select Account Record Type    PM End Customer
    SalesPO.Create A New Account
    SalesPO.Verify Account Creation
    SalesPO.Delete Account

Create An Opportunity For The Account
    [Tags]    sales    smoke    regression
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    GlobalKeywords.Launch App    Sales
    GlobalKeywords.Select App Tab    Opportunities
    GlobalKeywords.Open New Dialog    Opportunity
    SalesPO.Create A New Opportunity
    SalesPO.Verify Opportunity
    SalesPO.Delete Opportunity
