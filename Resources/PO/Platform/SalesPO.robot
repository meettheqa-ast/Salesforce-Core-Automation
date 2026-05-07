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
    # Opt out of the auto-default-record-type handler so the picker stays
    # open for Select Account Record Type to drive explicitly below.
    Open New Dialog    Account    auto_select_default_record_type=${FALSE}
    Select Account Record Type    ${record_type_label}
    Fill New Account Dialog After Record Type Selected    ${customer_type}    ${email}    ${EMPTY}    @{picklists_use_first_option}
    Select Dialog Button    Save

Open New Lead From Sales App
    [Documentation]    Opens the given app (default ``Sales``) → **Leads** → **New**. Pass a custom app name (e.g. ``Pentair Sales``, ``Mark Anthony``) when the user specifies one; omit for the default Sales app. ``Open New Dialog`` handles the "Choose Record Type" picker centrally -- when the Lead object has multiple record types and no user default, the pre-selected default is auto-confirmed. To pick a specific record type, use ``Open New Dialog    Lead    auto_select_default_record_type=${FALSE}`` and drive the picker yourself.
    [Arguments]    ${app_name}=${salesAutomationAppName}
    Launch App    ${app_name}
    Select App Tab    Leads
    Open New Dialog    Lead

Create A New Lead
    [Documentation]    Fills the New Lead modal AND saves it. **Once this keyword
    ...    returns, the modal is closed and the Lead exists** -- do NOT call
    ...    ``Open Dropdown`` / ``Select Dropdown Option`` afterwards (the
    ...    modal is gone). Drive any non-default field via the named args
    ...    on this keyword instead.
    ...
    ...    Identity overrides (``first_name`` / ``last_name`` / ``company``)
    ...    default to the FakerLibrary-backed values in ``SalesData.robot``.
    ...    Suite variables are kept in sync so verification keywords pick up
    ...    the same value.
    ...
    ...    Routing-relevant overrides:
    ...    * ``status`` -- explicit Lead Status (e.g. ``"Sales Lead"``,
    ...      ``"Working - Contacted"``). When omitted falls back to
    ...      ``${leadStatusOption}`` (PM-set), then to a random valid
    ...      picklist option.
    ...    * ``source`` -- explicit Lead Source (e.g. ``"Web"``).
    ...      Default: random first option.
    ...    * ``address`` -- a real address string fed through the Google
    ...      Places lookup (``Set Address Via Lookup``). Auto-populates
    ...      Street / City / State/Province / Country / Zip together.
    ...      Use this for any test that cares about a specific State, e.g.
    ...      ``address=18 King Street, San Francisco, CA``.
    [Arguments]    ${first_name}=${leadFirstName}    ${last_name}=${leadLastName}    ${company}=${leadCompany}    ${status}=${EMPTY}    ${source}=${EMPTY}    ${address}=${EMPTY}
    Set Suite Variable    ${leadFirstName}    ${first_name}
    Set Suite Variable    ${leadLastName}    ${last_name}
    Set Suite Variable    ${leadCompany}    ${company}
    Open Dropdown And Select First Option    Salutation
    Enter Text    First Name    ${leadFirstName}
    Enter Text    Last Name    ${leadLastName}
    Enter Text    Company    ${leadCompany}
    # Non-required fields use fallback — layout differs by org
    Enter Text With Fallback    Website    ${leadWebsite}
    Enter Text With Fallback    Phone    ${leadPhone}
    Enter Text With Fallback    Title    ${leadTitle}
    Enter Text With Fallback    Email    ${leadEmail}

    # Address: route through the Google Places lookup when the caller
    # supplies one. Auto-populates Street / City / State/Province /
    # Country / Zip in a single click. Skip when empty so a "default"
    # Lead create doesn't try to find a non-existent address field.
    IF    "${address}" != "${EMPTY}"
        Run Keyword And Ignore Error
        ...    Set Address Via Lookup    ${address}
    END

    # Lead Source: explicit > random first option.
    IF    "${source}" != "${EMPTY}"
        Open Dropdown    Lead Source
        ${source_set}=    Run Keyword And Return Status
        ...    Select Dropdown Option    Lead Source    ${source}
        IF    not ${source_set}
            Log    Lead Source "${source}" not found -- using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    ELSE
        Open Dropdown And Select First Option    Lead Source
    END

    # Lead Status: explicit arg > ${leadStatusOption} suite var > random.
    Open Dropdown    Lead Status
    ${status_explicit}=    Strip String    ${status}
    ${status_explicit_len}=    Get Length    ${status_explicit}
    ${status_pm}=    Strip String    ${leadStatusOption}
    ${status_pm_len}=    Get Length    ${status_pm}
    IF    ${status_explicit_len} > 0
        ${status_set}=    Run Keyword And Return Status
        ...    Select Dropdown Option    Lead Status    ${status_explicit}
        IF    not ${status_set}
            Log    Lead Status "${status_explicit}" not found -- using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    ELSE IF    ${status_pm_len} > 0
        Select Dropdown Option    Lead Status    ${status_pm}
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
    [Documentation]    Fills the New Opportunity modal. ``Opportunity Name``, ``Close Date``
    ...                and ``Stage`` are the standard required fields -- they use strict
    ...                ``Enter Text`` / ``Enter Date`` / ``Open Dropdown``. ``Amount``,
    ...                ``Next Step``, ``Description``, ``Forecast Category``, ``Type``,
    ...                ``Lead Source`` are layout-optional and use the fallback variants so
    ...                custom Pentair / per-org layouts that hide them don't fail the test.
    ...
    ...                Optional named overrides (``opp_name``, ``amount``, ``account_name``)
    ...                let callers pass values from the user prompt without redefining
    ...                ``${opportunityName}`` etc. in ``*** Variables ***``. Suite variables
    ...                are kept in sync. Call with ZERO args to use ``SalesData`` defaults.
    [Arguments]    ${opp_name}=${opportunityName}    ${amount}=${opportunityAmount}    ${account_name}=${opportunityAccountName}
    Set Suite Variable    ${opportunityName}    ${opp_name}
    Set Suite Variable    ${opportunityAmount}    ${amount}
    Set Suite Variable    ${opportunityAccountName}    ${account_name}
    Enter Into Search Field    Accounts    ${opportunityAccountName}
    Enter Text    Opportunity Name    ${opportunityName}
    # Amount is layout-optional in many orgs (e.g. when Forecast = Schedule line items)
    Enter Text With Fallback    Amount    ${opportunityAmount}
    Enter Date    Close Date    ${opportunityCloseDate}
    FOR    ${field}    ${value}    IN
    ...    Stage                ${opportunityStageOption}
    ...    Forecast Category    ${opportunityForecastCategoryOption}
    ...    Type                 ${opportunityType}
    ...    Lead Source           ${opportunityLeadSource}
        ${dropdown_open}=    Run Keyword And Return Status    Open Dropdown    ${field}
        IF    not ${dropdown_open}
            Log    Picklist "${field}" not on this Opportunity layout -- skipping.    WARN
            CONTINUE
        END
        ${ok}=    Run Keyword And Return Status
        ...    Select Dropdown Option    ${field}    ${value}
        IF    not $ok
            Log    "${value}" not found for "${field}" — using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    END
    Enter Text With Fallback    Next Step    ${opportunityNextStep}
    Enter Text With Fallback    Description    ${opportunityDescription}
    Attempt Save And Auto-Heal Missing Fields

Verify Opportunity
    [Documentation]    Validates the Opportunity record was created with expected Name.
    Verify Redirection to Record Details Page    ${opportunityName}
    Verify Record Creation With Data    Opportunity    Name    ${opportunityName}

Convert Lead To Opportunity
    Reload Page
    Perform Action On Record Details Page Header    Lead    Convert
    Open Dropdown    Converted Status
    # ``Select Random Dropdown Option In Modal`` takes no arguments -- the
    # currently-open dropdown is the implicit target. Passing a label here
    # tripped the ``robot --dryrun`` arity check; the value was silently
    # ignored at runtime so this fix is behaviour-neutral.
    Select Random Dropdown Option In Modal
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
    [Documentation]    Opens the given app (default ``Sales``) → **Opportunities** → **New**. Pass a custom app name when the user specifies one. ``Open New Dialog`` handles the "Choose Record Type" picker centrally -- pre-selected default is auto-confirmed when the picker appears.
    [Arguments]    ${app_name}=${salesAutomationAppName}
    Launch App    ${app_name}
    Select App Tab    Opportunities
    Open New Dialog    Opportunity

Delete Opportunity
    Delete Current Record    Opportunity

Delete Converted Lead
    Select App Tab    Opportunities

Create A New Account
    [Documentation]    Fills the New Account modal. Account Name is the only field
    ...                that is required across every standard Salesforce layout, so it
    ...                uses strict ``Enter Text``. Phone / Website / Employees vary by
    ...                org page layout (Pentair's Account form, for example, hides
    ...                Employees) -- those use ``Enter Text With Fallback`` so a missing
    ...                field logs a warning instead of failing the whole test. Picklist
    ...                fields use org-safe fallback (tries the configured value first,
    ...                falls back to first valid option).
    ...
    ...                Optional named overrides (``account_name``, ``phone``, ``website``)
    ...                let callers pass values from the user prompt without redefining
    ...                ``${accountName}`` etc. in ``*** Variables ***``. Suite variables
    ...                are kept in sync so downstream verification keywords pick up the
    ...                same value. Call with ZERO args to use ``SalesData.robot`` defaults.
    [Arguments]    ${account_name}=${accountName}    ${phone}=${accountPhone}    ${website}=${accountWebsite}
    Set Suite Variable    ${accountName}    ${account_name}
    Set Suite Variable    ${accountPhone}    ${phone}
    Set Suite Variable    ${accountWebsite}    ${website}
    Enter Text    Account Name    ${accountName}
    # Layout-optional fields -- skip silently if the org's page layout omits them.
    Enter Text With Fallback    Phone    ${accountPhone}
    Enter Text With Fallback    Website    ${accountWebsite}
    Enter Text With Fallback    Employees    ${accountEmployees}
    FOR    ${field}    ${value}    IN
    ...    Type        ${accountType}
    ...    Industry    ${accountIndustry}
        ${dropdown_open}=    Run Keyword And Return Status    Open Dropdown    ${field}
        IF    not ${dropdown_open}
            Log    Picklist "${field}" not on this Account layout -- skipping.    WARN
            CONTINUE
        END
        ${ok}=    Run Keyword And Return Status
        ...    Select Dropdown Option    ${field}    ${value}
        IF    not $ok
            Log    "${value}" not found for "${field}" — using first valid option.    WARN
            Select Random Valid Picklist Option
        END
    END
    Attempt Save And Auto-Heal Missing Fields

Open New Account From Sales App
    [Documentation]    Opens the given app (default ``Sales``) → **Accounts** → **New**. Pass a custom app name when the user specifies one. ``Open New Dialog`` handles the "Choose Record Type" picker centrally -- pre-selected default is auto-confirmed when the picker appears. To pick a SPECIFIC Account record type (e.g. ``BC Commercial``) use ``Open New Dialog    Account    auto_select_default_record_type=${FALSE}`` then ``Select Account Record Type    <name>`` instead.
    [Arguments]    ${app_name}=${salesAutomationAppName}
    Launch App    ${app_name}
    Select App Tab    Accounts
    Open New Dialog    Account

Verify Account Creation
    [Documentation]    Validates key Account fields on the record details page.
    Verify Record Creation With Data    Account    Name    ${accountName}
    Verify Record Creation With Data    Account    Phone    ${accountPhone}

Delete Account
    Delete Current Record    Account

Open New Contact From Sales App
    [Documentation]    Opens the given app (default ``Sales``) → **Contacts** → **New**. Pass a custom app name when the user specifies one. ``Open New Dialog`` handles the "Choose Record Type" picker centrally -- pre-selected default is auto-confirmed when the picker appears.
    [Arguments]    ${app_name}=${salesAutomationAppName}
    Launch App    ${app_name}
    Select App Tab    Contacts
    Open New Dialog    Contact

Create A New Contact
    [Documentation]    Fills the New Contact modal. Links to an Account if
    ...                ``${contactAccountName}`` is non-empty. ``Last Name`` is the only
    ...                Salesforce-required field on Contact -- everything else (First Name,
    ...                Title, Email, Phone) is layout-optional and uses
    ...                ``Enter Text With Fallback`` so missing fields don't abort the test.
    ...
    ...                Optional named overrides (``first_name``, ``last_name``, ``email``,
    ...                ``phone``, ``title``, ``account_name``) let callers pass values from
    ...                the user prompt without redefining suite variables. Call with ZERO
    ...                args to use ``SalesData`` defaults.
    ...
    ...                Note: ``ContactPO.Create A New Contact`` in
    ...                ``Resources/PO/Platform/ContactPO.robot`` is the preferred drop-in
    ...                for Contact creation -- it also adds a Salutation picker and tighter
    ...                verification. This SalesPO variant is kept for backwards compat with
    ...                older suites that already qualify with ``SalesPO.``.
    [Arguments]    ${first_name}=${contactFirstName}    ${last_name}=${contactLastName}    ${email}=${contactEmail}    ${phone}=${contactPhone}    ${title}=${contactTitle}    ${account_name}=${contactAccountName}
    Set Suite Variable    ${contactFirstName}    ${first_name}
    Set Suite Variable    ${contactLastName}    ${last_name}
    Set Suite Variable    ${contactEmail}    ${email}
    Set Suite Variable    ${contactPhone}    ${phone}
    Set Suite Variable    ${contactTitle}    ${title}
    Set Suite Variable    ${contactAccountName}    ${account_name}
    Enter Text With Fallback    First Name    ${contactFirstName}
    Enter Text    Last Name    ${contactLastName}
    Enter Text With Fallback    Title    ${contactTitle}
    Enter Text With Fallback    Email    ${contactEmail}
    Enter Text With Fallback    Phone    ${contactPhone}
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
