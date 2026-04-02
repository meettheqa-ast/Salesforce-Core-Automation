*** Settings ***
Library     SeleniumLibrary
# Resource    ../../TestData/Platform/PlatformData.robot
Resource    ../../TestData/Platform/SalesData.robot
Resource    ../../Common/GlobalKeywords.robot


*** Keywords ***
Create A New Lead
    Open Dropdown    Lead Status
    Select Dropdown Option    Lead Status    ${leadStatusOption}
    Open Dropdown    Salutation
    Select Dropdown Option    Salutation    ${salutationOption}
    Enter Text    Website    ${leadWebsite}
    Enter Text    First Name    ${leadFirstName}
    Enter Text    Last Name    ${leadLastName}
    Enter Text    Company    ${leadCompany}
    Enter Text    Phone    ${leadPhone}
    Enter Text    Title    ${leadTitle}
    Enter Text    Email    ${leadEmail}
    Open Dropdown    Lead Source
    Select Dropdown Option    Lead Source    ${leadSourceOption}
    Select Dialog Button    Save

Verify First Name, Company And Title On Lead Page
    Verify Record Creation With Data    Lead    Name    ${salutationOption} ${leadfirstname} ${leadLastName}
    Verify Record Creation With Data    Lead    Company    ${leadCompany}
    Verify Record Creation With Data    Lead    Title    ${leadTitle}

Create A New Opportunity
    Enter Into Search Field    Accounts    ${opportunityAccountName}
    Enter Text    Opportunity Name    ${opportunityName}
    Open Dropdown    Forecast Category
    Select Dropdown Option    Forecast Category    ${opportunityForecastCategoryOption}
    Enter Text    Next Step    ${opportunityNextStep}
    Enter Text    Amount    ${opportunityAmount}
    Enter Date    Close Date    ${opportunityCloseDate}
    Open Dropdown    Stage
    Select Dropdown Option    Stage    ${opportunityStageOption}
    Open Dropdown    Type
    Select Dropdown Option    Type    ${opportunityType}
    Open Dropdown    Lead Source
    Select Dropdown Option    Lead Source    ${opportunityLeadSource}
    Enter Text    Description    ${opportunityDescription}
    Select Dialog Button    Save

Verify Opportunity
    Verify Redirection to Record Details Page    ${opportunityName}
    Verify Record Creation With Data    Opportunity    Name    ${opportunityName}

Convert Lead To Opportunity
    Reload Page
    Perform Action On Record Details Page Header    Lead    Convert
    Open Dropdown    Converted Status
    Select Dropdown Option    Converted Status    ${leadConvertedStatusOption}
    Select Dialog Button    Convert
    Select Dialog Button    Go to Leads

Delete Lead
    Delete Current Record    Lead

Delete Opportunity
    Delete Current Record    Opportunity

Delete Converted Lead
    Select App Tab    Opportunities

Create A New Account
    Enter Text    Account Name    ${accountName}
    Open Dropdown    Type
    Select Dropdown Option    Type    ${accountType}
    Enter Text    Phone    ${accountPhone}
    Open Dropdown    Industry
    Select Dropdown Option    Industry    ${accountIndustry}
    Enter Text    Website    ${accountWebsite}
    Enter Text    Employees    ${accountEmployees}
    Select Dialog Button    Save

Verify Account Creation
    Verify Record Creation With Data    Account    Name    ${accountName}
    Verify Record Creation With Data    Account    Type    ${accountType}
    Verify Record Creation With Data    Account    Phone    ${accountPhone}
    Verify Record Creation With Data    Account    Industry    ${accountIndustry}
    Visit Dynamic Form Section    Additional Information
    Verify Record Creation With Data    Account    Website    ${accountWebsite}
    Verify Record Creation With Data    Account    NumberOfEmployees    ${accountEmployees}

Delete Account
    Delete Current Record    Account
