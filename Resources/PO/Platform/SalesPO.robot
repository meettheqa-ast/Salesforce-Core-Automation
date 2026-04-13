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
    [Documentation]    Fills the New Lead modal. Optional ``first_name``, ``last_name``, ``company`` override ``SalesData`` defaults; suite variables are updated so downstream verification matches. Call with **no** args to use suite defaults only. **Lead Status:** if ``${leadStatusOption}`` is non-empty (after trim), uses ``Select Dropdown Option`` for PM-specified value; otherwise ``Select Random Valid Picklist Option`` (skips ``--None--`` and empty ``data-value``). Salutation and Lead Source use random combobox items via ``Open Dropdown And Select First Option``.
    [Arguments]    ${first_name}=${leadFirstName}    ${last_name}=${leadLastName}    ${company}=${leadCompany}
    Set Suite Variable    ${leadFirstName}    ${first_name}
    Set Suite Variable    ${leadLastName}    ${last_name}
    Set Suite Variable    ${leadCompany}    ${company}
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
    [Documentation]    Fills the New Opportunity modal. Picklist fields use org-safe fallback
    ...                (tries the configured value first, falls back to first valid option).
    Enter Into Search Field    Accounts    ${opportunityAccountName}
    Enter Text    Opportunity Name    ${opportunityName}
    Enter Text    Amount    ${opportunityAmount}
    Enter Date    Close Date    ${opportunityCloseDate}
    FOR    ${field}    ${value}    IN
    ...    Stage                ${opportunityStageOption}
    ...    Forecast Category    ${opportunityForecastCategoryOption}
    ...    Type                 ${opportunityType}
    ...    Lead Source           ${opportunityLeadSource}
        Open Dropdown    ${field}
        ${ok}=    Run Keyword And Return Status
        ...    Select Dropdown Option    ${field}    ${value}
        IF    not $ok
            Log    "${value}" not found for "${field}" — using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    END
    Enter Text    Next Step    ${opportunityNextStep}
    Enter Text    Description    ${opportunityDescription}
    Attempt Save And Auto-Heal Missing Fields

Verify Opportunity
    [Documentation]    Validates the Opportunity record was created with expected Name.
    Verify Redirection to Record Details Page    ${opportunityName}
    Verify Record Creation With Data    Opportunity    Name    ${opportunityName}

Convert Lead To Opportunity
    Reload Page
    Perform Action On Record Details Page Header    Lead    Convert
    Open Dropdown    Converted Status
    Select Random Dropdown Option In Modal    Converted Status
    Select Dialog Button    Convert
    Wait For Lightning Spinners Absent
    ${clicked}=    Set Variable    ${FALSE}
    FOR    ${label}    IN    Go to Leads    Go to Converted Lead    Go to Opportunity
        ${status}=    Run Keyword And Return Status
        ...    Select Dialog Button    ${label}
        IF    $status
            ${clicked}=    Set Variable    ${TRUE}
            BREAK
        END
    END
    IF    not $clicked
        Run Keyword And Ignore Error
        ...    Click Element    xpath://*[contains(@class,'modal-container')]//button[contains(@class,'slds-button')]
    END

Delete Lead
    Delete Current Record    Lead

Open New Opportunity From Sales App
    [Documentation]    Opens **Sales** → **Opportunities** → **New**.
    Launch App    ${salesAutomationAppName}
    Select App Tab    Opportunities
    Open New Dialog    Opportunity

Delete Opportunity
    Delete Current Record    Opportunity

Delete Converted Lead
    Select App Tab    Opportunities

Create A New Account
    [Documentation]    Fills the New Account modal. Picklist fields use org-safe fallback
    ...                (tries the configured value first, falls back to first valid option).
    Enter Text    Account Name    ${accountName}
    Enter Text    Phone    ${accountPhone}
    Enter Text    Website    ${accountWebsite}
    Enter Text    Employees    ${accountEmployees}
    FOR    ${field}    ${value}    IN
    ...    Type        ${accountType}
    ...    Industry    ${accountIndustry}
        Open Dropdown    ${field}
        ${ok}=    Run Keyword And Return Status
        ...    Select Dropdown Option    ${field}    ${value}
        IF    not $ok
            Log    "${value}" not found for "${field}" — using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    END
    Attempt Save And Auto-Heal Missing Fields

Open New Account From Sales App
    [Documentation]    Opens **Sales** → **Accounts** → **New**.
    Launch App    ${salesAutomationAppName}
    Select App Tab    Accounts
    Open New Dialog    Account

Verify Account Creation
    [Documentation]    Validates key Account fields on the record details page.
    Verify Record Creation With Data    Account    Name    ${accountName}
    Verify Record Creation With Data    Account    Phone    ${accountPhone}

Delete Account
    Delete Current Record    Account

Open New Contact From Sales App
    [Documentation]    Opens **Sales** → **Contacts** → **New**.
    Launch App    ${salesAutomationAppName}
    Select App Tab    Contacts
    Open New Dialog    Contact

Create A New Contact
    [Documentation]    Fills the New Contact modal. Links to an Account if ``${contactAccountName}`` is non-empty.
    Enter Text    First Name    ${contactFirstName}
    Enter Text    Last Name    ${contactLastName}
    Enter Text    Title    ${contactTitle}
    Enter Text    Email    ${contactEmail}
    Enter Text    Phone    ${contactPhone}
    ${acct_trim}=    Strip String    ${contactAccountName}
    ${acct_len}=    Get Length    ${acct_trim}
    IF    ${acct_len} > 0
        Enter Into Search Field    Account Name    ${acct_trim}
    END
    Attempt Save And Auto-Heal Missing Fields

Verify Contact Created Successfully
    [Documentation]    Confirms Contact save and validates key fields on the record page.
    Get Success Toast Message Related Record Creation ID
    Page Should Contain    ${contactFirstName}
    Page Should Contain    ${contactLastName}
    Verify Record Creation With Data    Contact    Title    ${contactTitle}
    Verify Record Creation With Data    Contact    Email    ${contactEmail}

Delete Contact
    Delete Current Record    Contact
