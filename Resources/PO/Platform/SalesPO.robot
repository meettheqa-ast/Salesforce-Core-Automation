*** Settings ***
Library     SeleniumLibrary
# Resource    ../../TestData/Platform/PlatformData.robot
Resource    ../../TestData/Platform/SalesData.robot
Resource    ../../Common/GlobalKeywords.robot


*** Keywords ***
Go To Accounts Tab For App
    [Documentation]    Launches the given app (as shown in App Launcher), opens Accounts, switches to List view.
    [Arguments]    ${app_display_name}=${salesAutomationAppName}
    Launch App    ${app_display_name}
    Select App Tab    Accounts
    Convert View From Intelligent To List

Fill New Account Dialog After Record Type Selected
    [Documentation]    After ``Open New Dialog    Account`` and ``Select Account Record Type`` (including Next): sets Account Name (random if blank), Customer Type, Email, random Phone, then for each extra label tries to open that picklist and take the first real Lightning option.
    [Arguments]    ${customer_type}    ${email}    ${account_name}=${EMPTY}    @{picklists_use_first_option}
    IF    '${account_name}' == '${EMPTY}'
        ${suffix}=    Evaluate    random.randint(10000, 99999)    modules=random
        ${account_name}=    Set Variable    Auto Acct ${suffix}
    END
    ${a}=    Evaluate    random.randint(100, 999)    modules=random
    ${b}=    Evaluate    random.randint(100, 999)    modules=random
    ${c}=    Evaluate    random.randint(1000, 9999)    modules=random
    ${phone}=    Set Variable    ${a}-${b}-${c}
    Enter Text    Account Name    ${account_name}
    Open Dropdown    Customer Type
    Select Dropdown Option    Customer Type    ${customer_type}
    Enter Text    Email    ${email}
    Enter Text    Phone    ${phone}
    FOR    ${label}    IN    @{picklists_use_first_option}
        ${exists}=    Run Keyword And Return Status    Page Should Contain Element
        ...    xpath://*[contains(@class,'modal-container')]//button[contains(@aria-label,'${label}')]
        IF    ${exists}
            Open Dropdown And Select First Option    ${label}
        END
    END

Create BC Commercial Account In App
    [Documentation]    End-to-end: Accounts tab → New Account → record type BC Commercial → fill LIC / email / random phone and optional first-option picklists (e.g. Industry). Pass app name for orgs that use a custom app (e.g. Mark Anthony Group).
    [Arguments]
    ...    ${app_display_name}=${salesAutomationAppName}
    ...    ${customer_type}=LIC
    ...    ${email}=m.sheth@astounddigital.com
    ...    ${record_type_label}=BC Commercial
    ...    @{picklists_use_first_option}
    Go To Accounts Tab For App    ${app_display_name}
    Open New Dialog    Account
    Select Account Record Type    ${record_type_label}
    Fill New Account Dialog After Record Type Selected    ${customer_type}    ${email}    ${EMPTY}    @{picklists_use_first_option}
    Select Dialog Button    Save

Open New Lead From Sales App
    [Documentation]    Opens **Sales** → **Leads** → **New** (no List/Intelligence toggle—``New`` uses Aura ``forceActionLink`` / LWC locators). Then call ``Create A New Lead``.
    Launch App    ${salesAutomationAppName}
    Select App Tab    Leads
    Open New Dialog    Lead

Create A New Lead
    [Documentation]    Fills the New Lead modal. **Lead Status:** if ``${leadStatusOption}`` is non-empty (after trim), uses ``Select Dropdown Option`` for PM-specified value; otherwise ``Select Random Valid Picklist Option`` (skips ``--None--`` and empty ``data-value``). Salutation and Lead Source use random combobox items via ``Open Dropdown And Select First Option``.
    Open Dropdown And Select First Option    Salutation
    Enter Text    Website    ${leadWebsite}
    Enter Text    First Name    ${leadFirstName}
    Enter Text    Last Name    ${leadLastName}
    Enter Text    Company    ${leadCompany}
    Enter Text    Phone    ${leadPhone}
    Enter Text    Title    ${leadTitle}
    Enter Text    Email    ${leadEmail}
    Open Dropdown And Select First Option    Lead Source
    Open Dropdown    Lead Status
    ${lead_status_trim}=    Strip String    ${leadStatusOption}
    ${lead_status_len}=    Get Length    ${lead_status_trim}
    IF    ${lead_status_len} > 0
        Select Dropdown Option    Lead Status    ${lead_status_trim}
    ELSE
        Select Random Valid Picklist Option
    END
    Attempt Save And Auto-Heal Missing Fields

Verify Lead Created Successfully
    [Documentation]    Confirms Lead save via record-details success toast, then validates key Lead fields on the page.
    Get Success Toast Message Related Record Creation ID
    Verify First Name, Company And Title On Lead Page

Verify First Name, Company And Title On Lead Page
    [Documentation]    Name field includes salutation from the picklist (unknown when using first option); assert first/last appear on the page and other fields exactly.
    Page Should Contain    ${leadFirstName}
    Page Should Contain    ${leadLastName}
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
    Attempt Save And Auto-Heal Missing Fields

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
