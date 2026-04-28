*** Settings ***
Library     SeleniumLibrary
Library     String
Library     Dialogs
Library     Collections
Library     FakerLibrary
# EnvData must load before GlobalVariables / PlatformData so runtime URL & login
# (written by run_test.py / Streamlit) win over repo defaults. Robot keeps first scalar definition.
Resource    ../../Resources/TestData/EnvData.robot
Resource    GlobalVariables.robot
Resource    GlobalLocators.robot
Resource    ../../Resources/TestData/Platform/PlatformData.robot
# libdoc Resources/Common/GlobalKeywords.robot SF-Core-Keywords-Library.html

*** Variables ***
# When true, login waits for you to finish MFA/OTP in the browser (Watch mode). Overridden by robot -v.
${MFA_PAUSE_FOR_MANUAL_COMPLETION}=    ${FALSE}

*** Keywords ***
Begin Web Test
    [Documentation]    The Begin Web Test setup initializes the testing environment by opening a blank page in the Chrome browser, maximizing the browser window for better visibility, and configuring Selenium with a 10-second timeout and implicit wait to ensure proper handling of element loading and interactions. Pass variable headless=true (e.g. robot -v headless:true) to run Chrome in headless mode for CI or background runs. When run inside the backend Docker image (BACKEND_IN_CONTAINER=1), runs.py forces headless=true and injects CONTAINER_BROWSER_BINARY=/usr/bin/chromium so Selenium can find the Debian chromium binary.
    [Tags]    setup
    ${headless_raw}=    Get Variable Value    ${headless}    false
    ${headless_lc}=    Convert To Lower Case    ${headless_raw}
    ${headless_lc}=    Strip String    ${headless_lc}
    ${browser_bin}=    Get Variable Value    ${CONTAINER_BROWSER_BINARY}    ${EMPTY}
    IF    '${headless_lc}' == 'true'
        IF    '${browser_bin}' != '${EMPTY}'
            # Containerized run: point Selenium at the Debian chromium binary
            # and add --no-sandbox / --disable-dev-shm-usage. Without these,
            # Chrome inside the container immediately crashes with
            # "session not created: Chrome instance exited".
            Open Browser    about:blank    chrome    options=binary_location=r'${browser_bin}';add_argument("--headless=new");add_argument("--no-sandbox");add_argument("--disable-dev-shm-usage");add_argument("--disable-gpu");add_argument("--window-size=1920,1080");add_argument("--disable-notifications")
        ELSE
            Open Browser    about:blank    chrome    options=add_argument("--headless=new");add_argument("--disable-gpu");add_argument("--window-size=1920,1080");add_argument("--disable-notifications")
        END
        Set Window Size    1920    1080
    ELSE
        Open Browser    about:blank    chrome    options=add_argument("--disable-notifications")
        Maximize Browser Window
    END
#    set window position    x=0    y=0
#    set window size    width=1265    height=675
    Set Selenium Timeout    5s
    Set Selenium Implicit Wait    0s

End Web Test
    [Documentation]    The End Web Test step concludes the testing session by closing all browser instances, ensuring proper cleanup of the testing environment.
    [Tags]    teardown
    Close All Browsers

Login To Sandbox
    [Documentation]    Logs into the sandbox, automatically dismissing any post-login interstitial prompts (phone registration, email verification, "Remind Me Later", etc.) before waiting for the Lightning app shell. If the org enforces MFA/OTP, a password-only flow never reaches the Lightning header until verification finishes. Set suite variable MFA_PAUSE_FOR_MANUAL_COMPLETION to ${TRUE} (Streamlit Watch mode does this automatically) to show a dialog: complete OTP in the browser, then click OK; the keyword then waits up to 10 minutes for the app shell. Headless/CI runs keep MFA_PAUSE false and fail fast if MFA is required—use Trusted IP, a policy exception, or a non-MFA test user instead.
    [Tags]    login
    [Arguments]    ${instanceURL}    ${instanceUsername}    ${instancePassword}    ${allow_mfa_manual_pause}=${MFA_PAUSE_FOR_MANUAL_COMPLETION}
    Go To    ${instanceURL}
    Wait Until Element Is Visible    ${sandboxUserName}    timeout=15s
    Input Text    ${sandboxUserName}    ${instanceUsername}
    Input Text    ${sandboxPassword}    ${instancePassword}
    Click Element    ${sandboxLoginButton}
    Dismiss Post-Login Prompts
    ${ok}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${sandboxLaunch360Logo}    30s
    IF    not $ok and $allow_mfa_manual_pause
        Pause Execution    Complete MFA / OTP in the browser window, then click OK here to continue the test.
        Wait Until Element Is Visible    ${sandboxLaunch360Logo}    10 minutes
        Dismiss Post-Login Prompts
    ELSE IF    not $ok
        Fail    Login did not reach Salesforce (often MFA/OTP still pending or wrong credentials). Options: (1) Run in Watch mode so the app can pause for manual OTP. (2) Ask your admin for a Trusted IP range for your network so MFA is not prompted. (3) Use a sandbox integration user exempt from MFA if policy allows. (4) For TOTP secrets, extend automation to submit the verification code—see your security team.
    END

Dismiss Post-Login Prompts
    [Documentation]    Handles Salesforce interstitial screens that appear between login and the Lightning home page. Covers phone registration ("I Don't Want to Register My Phone"), email verification reminders, and "Remind Me Later" / "Skip" prompts. Checks up to 3 times with a short pause between attempts to catch pages that render after a redirect. Safe to call when no prompt is present — exits silently.
    [Tags]    login    utilities
    FOR    ${_}    IN RANGE    3
        ${prompt_visible}=    Run Keyword And Return Status
        ...    Wait Until Element Is Visible    ${dismissPhoneRegistration}    timeout=3s
        IF    ${prompt_visible}
            ${el}=    Get Webelement    ${dismissPhoneRegistration}
            Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${el}
            Sleep    1s
        ELSE
            Exit For Loop
        END
    END

Launch App
    [Documentation]    Opens an app from the App Launcher. Uses exact title match when possible. If the menu item text does not exactly match (typos, "Mark Anthony" vs "Mark Anthony Group"), still types the given name into search and clicks the **first** visible result—Salesforce search is usually fuzzy, so this tolerates approximate names from prompts.
    [Tags]    navigation
    [Arguments]    ${appName}
    ${activeApp}=    Replace String    ${activeAppLocator}    <app-name>    ${appName}
    ${booleanStatus}=    Run Keyword And Return Status    Element Should Be Visible    ${activeApp}
    IF  not ${booleanStatus}
        Click Element    ${appLauncher}
        Wait Until Element Is Visible    ${searchAppLauncher}    timeout=5s
        Clear Element Text    ${searchAppLauncher}
        Input Text    ${searchAppLauncher}    ${appName}
        ${appInLauncher}=    Replace String    ${appInLauncherLocator}    <app-name>    ${appName}
        ${exactHit}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${appInLauncher}    5s
        IF    ${exactHit}
            Click Element    ${appInLauncher}
        ELSE
            ${firstHit}=    Set Variable    xpath:(//one-app-launcher-menu-item)[1]
            Wait Until Element Is Visible    ${firstHit}    15s
            Click Element    ${firstHit}
        END
        Wait Until Element Is Visible    ${sandboxlaunch360logo}    timeout=15s
        ${verified}=    Run Keyword And Return Status    Page Should Contain Element    ${activeApp}
        IF    not ${verified}
            Log    Opened first App Launcher search result for "${appName}"; active header may not match the string exactly (typos / alternate app title).    WARN
        END
    END
    Wait Until Element Is Visible    ${activeApp}    timeout=5s

Select App Tab
    [Documentation]    Selects a tab and verifies activation. Uses the expanded ``activeTabLocator`` (breadcrumbs, page header, aria-selected). If the strict locator check fails, falls back to verifying the tab link's ``aria-current`` or that the page URL contains the object name.
    [Tags]    navigation
    [Arguments]    ${tabName}
    ${tabInApp}=    Replace String    ${tabInAppLocator}    <tab-name>    ${tabName}
    Wait Until Element Is Visible    ${tabInApp}    timeout=10s
    Click Element    ${tabInApp}
    ${activeTab}=    Replace String    ${activeTabLocator}    <tab-name>    ${tabName}
    ${verified}=    Run Keyword And Return Status    Wait Until Page Contains Element    ${activeTab}    timeout=8s
    IF    not ${verified}
        # Fallback: check if the nav bar link got aria-current="page" after click
        ${navLink}=    Set Variable    xpath://one-app-nav-bar-item-root//a[@title='${tabName}']
        ${nav_ok}=    Run Keyword And Return Status    Page Should Contain Element    ${navLink}
        IF    ${nav_ok}
            Log    Tab '${tabName}' selected (nav-bar link present; breadcrumb locator did not match).    WARN
        ELSE
            Fail    Tab '${tabName}' could not be verified as active after clicking.
        END
    END

Open New Dialog
    [Documentation]    Clicks **New** then the dialog title row. Resolves the New button via **tiered locators** (CSS ``title+role`` → LWC ``lightning-button`` → XPath fallback) per §1.1 of the locator ruleset. Dismisses overlays, scrolls New into view, then uses a normal click with **JavaScript click** fallback when another layer intercepts the pointer.
    [Tags]    modal    navigation
    [Arguments]    ${dialogName}
    ${newBtn}=    Resolve Tiered Locator    ${newRecordTier1}    ${newRecordTier2}    ${newRecord}
    Wait Until Element Is Visible    ${newBtn}    timeout=15s
    Wait For Lightning Spinners Absent    timeout=8s
    # Dismiss any stale overlay (record-type picker) in one pass
    ${overlay}=    Run Keyword And Return Status    Page Should Contain Element    ${sfRecordTypeOverlay}
    IF    ${overlay}
        Run Keyword And Ignore Error    Press Keys    xpath://body    ESCAPE
        Wait For Record Type Overlay Cleared    timeout=5s
    END
    Scroll Element Into View With Fallback    ${newBtn}
    ${clicked}=    Run Keyword And Return Status    Click Element    ${newBtn}
    IF    not ${clicked}
        ${nr}=    Get Webelement    ${newBtn}
        Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${nr}
    END
    ${newRecordDialogTitle}=    Replace String    ${newRecordDialogTitleLocator}    <record-name>    ${dialogName}
    Wait Until Element Is Visible    ${newRecordDialogTitle}    timeout=15s
    Scroll Element Into View With Fallback    ${newRecordDialogTitle}
    ${titleClick}=    Run Keyword And Return Status    Click Element    ${newRecordDialogTitle}
    IF    not ${titleClick}
        ${h2}=    Get Webelement    ${newRecordDialogTitle}
        Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${h2}
    END

Open Item
    [Documentation]    Opens a specific item within the app by searching for it in the app launcher. It then clicks the app launcher, enters the item name into the search field, and waits for the item to appear. It then clicks on the item to open it and waits for the item's logo to become visible, indicating the item has been successfully launched. A short pause is added at the end to ensure the item has fully loaded.
    [Tags]    navigation
    [Arguments]    ${itemName}
    Click Element    ${appLauncher}
    Input Text    ${searchAppLauncher}    ${itemName}
    ${itemInLauncher}=    Replace String    ${itemInLauncherLocator}    <item-name>    ${itemName}
    Wait Until Element Is Visible    ${itemInLauncher}    timeout=10s
    Click Element    ${itemInLauncher}
    Wait Until Element Is Visible    ${sandboxlaunch360logo}    timeout=15s

Enter Into Search Field
    [Documentation]    Use this keyword to enter a value into the Input Search Field. The test first checks if the field name is provided; if not, it dynamically identifies the search input field in the dialog. It waits for the search field to be visible, scrolls it into view, and then enters the specified search term if provided. If the search term is not empty, the test waits for the search suggestion to appear, scrolls it into view, and clicks on the appropriate suggestion.
    [Tags]    interaction    search input field
    [Arguments]    ${fieldName}=${EMPTY}    ${searchTerm}=${EMPTY}    ${searchPos}=1
    IF    '${fieldName}' == '${EMPTY}'
        ${searchInputFieldDialogLocator}=    Replace String
        ...    ${searchInputFieldDialogLocator}
        ...    Search <search-input-field>
        ...    Search<search-input-field>
    END
    ${searchInputField}=    Replace String    ${searchInputFieldDialogLocator}    <search-input-field>    ${fieldName}
    Wait Until Page Contains Element    ${searchInputField}    timeout=10s
    Scroll Element Into View    ${searchInputField}
    IF    '${searchTerm}' != '${EMPTY}'
        Input Text    ${searchInputField}    ${searchTerm}
        ${searchSuggestionTerm}=    Replace String
        ...    ${searchSuggestionTermDialogLocator}
        ...    <search-term>
        ...    ${searchTerm}
        ${searchSuggestionTerm}=    Replace String    ${searchSuggestionTerm}    <pos>    ${searchPos}
        Wait Until Element Is Visible    ${searchSuggestionTerm}    timeout=10s
        Scroll Element Into View    ${searchSuggestionTerm}
        Wait Until Element Is Enabled    ${searchSuggestionTerm}    timeout=5s
        ${searchSuggestionTerm}=    Get Webelement    ${searchSuggestionTerm}
        Click Element    ${searchSuggestionTerm}
    END

Select Dialog Button
    [Documentation]    Selects a button within a Dialog. Eg. Save, Cancel, Save & New
    [Tags]    modal    interaction
    [Arguments]    ${buttonActionArg}
    ${buttonAction}=    Replace String    ${dialogAction}    <btn-action>    ${buttonActionArg}
    Wait Until Element Is Visible    ${buttonAction}    timeout=5s
    Click Button    ${buttonAction}

Attempt Save And Auto-Heal Missing Fields
    [Documentation]    Clicks **Save** in the active modal up to **3** times. After each click waits 2s; if Salesforce shows the snag / ``errorsList`` panel, reads linked field names and runs ``Heal Missing Modal Field By Label`` for each, then retries. Exits early when validation UI is gone (save succeeded). Fails if save never clears after 3 attempts.
    [Tags]    modal    interaction    self-heal
    ${saved}=    Set Variable    ${FALSE}
    FOR    ${_}    IN RANGE    3
        Select Dialog Button    Save
        ${snag}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${salesforceModalValidationErrorLocator}    5s
        IF    not ${snag}
            ${saved}=    Set Variable    ${TRUE}
            Exit For Loop
        END
        @{links}=    Get WebElements    ${snagErrorFieldLinksLocator}
        ${n}=    Get Length    ${links}
        IF    ${n} == 0
            Log    Validation UI visible but no field links under errorsList / fieldLevelErrors; cannot auto-heal.    WARN
            Exit For Loop
        END
        FOR    ${el}    IN    @{links}
            ${label}=    Get Text    ${el}
            ${label}=    Strip String    ${label}
            IF    '${label}' != '${EMPTY}'
                Heal Missing Modal Field By Label    ${label}
            END
        END
    END
    IF    not ${saved}
        Fail    Save did not complete successfully after up to 3 attempts (Salesforce validation may still be visible or field links were not found).
    END

Heal Missing Modal Field By Label
    [Documentation]    Tries **Open Dropdown With Fallback** + ``Select Random Valid Picklist Option`` for ``${field_label}`` (custom fields tolerated).
    ...    If the label looks like a compound **Address** snag (e.g. validation says ``Address`` but
    ...    the layout uses Street/City/Zip/Country), fills those sub-fields with **FakerLibrary** and
    ...    ``Enter Text With Fallback`` then **RETURN**s.
    ...    Otherwise if not a picklist, fills text via ``Enter Text With Fallback``
    ...    using ``Word`` / ``Numerify`` (Phone/Fax). Custom field labels are handled by the
    ...    fallback strategies in both keywords.
    [Tags]    modal    interaction    self-heal
    [Arguments]    ${field_label}
    ${fl}=    Convert To Lower Case    ${field_label}
    ${is_address}=    Evaluate    'address' in '''${fl}'''
    IF    ${is_address}
        ${v_street}=    Street Address
        ${v_city}=    City
        ${v_zip}=    Zipcode
        ${v_country}=    Country
        ${a}=    Enter Text With Fallback    Street    ${v_street}
        IF    not ${a}
            Log    Address heal: Street not filled (missing or non-text on layout).    WARN
        END
        ${b}=    Enter Text With Fallback    City    ${v_city}
        IF    not ${b}
            Log    Address heal: City not filled (missing or non-text on layout).    WARN
        END
        ${c}=    Enter Text With Fallback    Zip/Postal Code    ${v_zip}
        IF    not ${c}
            Log    Address heal: Zip/Postal Code not filled (missing or non-text on layout).    WARN
        END
        ${d}=    Enter Text With Fallback    Country    ${v_country}
        IF    not ${d}
            Log    Address heal: Country not filled (missing or non-text on layout).    WARN
        END
        RETURN
    END
    ${opened}=    Open Dropdown With Fallback    ${field_label}
    IF    ${opened}
        ${picked}=    Run Keyword And Return Status    Select Random Valid Picklist Option
        IF    not ${picked}
            Log    Could not pick a valid option for "${field_label}" after opening dropdown.    WARN
        END
        RETURN
    END
    ${is_numish}=    Evaluate    'phone' in '''${fl}''' or 'fax' in '''${fl}'''
    IF    ${is_numish}
        ${fill}=    Numerify    ##########
    ELSE
        ${fill}=    Word
    END
    ${typed}=    Enter Text With Fallback    ${field_label}    ${fill}
    IF    not ${typed}
        Log    Could not fill text for "${field_label}" (field may be absent, read-only, non-text, or a custom field not found by any strategy).    WARN
    END

Scroll Element Into View With Fallback
    [Documentation]    Uses Selenium scroll; if it fails (common for zero-size SLDS labels/spans), scrolls via JavaScript scrollIntoView.
    [Tags]    utilities
    [Arguments]    ${locator}
    ${ok}=    Run Keyword And Return Status    Scroll Element Into View    ${locator}
    IF    not ${ok}
        ${el}=    Get Webelement    ${locator}
        Execute Javascript    arguments[0].scrollIntoView({block: 'center'});    ARGUMENTS    ${el}
    END

Open Dropdown
    [Documentation]    Opens a picklist/combobox in the modal. Tries ``aria-label`` primary locator first (faster, resilient per §1.1), then falls back to the full XPath union. Scrolls into view via JS, checks disabled state, then JS-clicks to open the list.
    [Tags]    interaction    pick list
    [Arguments]    ${dropdownFieldArg}
    ${primaryLoc}=    Replace String    ${dropdownDialogAriaLabel}    <dropdown-field>    ${dropdownFieldArg}
    ${ok_primary}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${primaryLoc}    timeout=2s
    IF    ${ok_primary}
        ${dropdownField}=    Set Variable    ${primaryLoc}
    ELSE
        ${dropdownField}=    Replace String    ${dropdownDialogLocator}    <dropdown-field>    ${dropdownFieldArg}
    END
    Wait Until Element Is Visible    ${dropdownField}    timeout=5s
    ${dropdownFieldJS}=    Get Webelement    ${dropdownField}
    Execute Javascript    arguments[0].scrollIntoView({block:'center', inline:'nearest'});    ARGUMENTS    ${dropdownFieldJS}
    ${aria_dis}=    Get Element Attribute    ${dropdownField}    aria-disabled
    IF    '${aria_dis}' == 'true'
        Fail    The dropdown ${dropdownFieldArg} is disabled. This is likely a dependent picklist waiting for a controlling field.
    END
    ${enabled}=    Run Keyword And Return Status    Element Should Be Enabled    ${dropdownField}
    IF    not ${enabled}
        Fail    The dropdown ${dropdownFieldArg} is disabled. This is likely a dependent picklist waiting for a controlling field.
    END
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${dropdownFieldJS}

Open Dropdown With Fallback
    [Documentation]    Resilient variant of ``Open Dropdown`` for custom / non-standard Salesforce picklists.
    ...    Tries three strategies in order:
    ...    1. The full ``dropdownDialogLocator`` (exact aria-label + vendor-specific selectors).
    ...    2. A broad ``contains(aria-label)`` combobox search that tolerates partial label matches.
    ...    3. A ``contains(@class,'slds-combobox')`` parent scope search by label text.
    ...    Returns ``${TRUE}`` on success, ``${FALSE}`` (with WARN log) if all strategies fail.
    [Tags]    interaction    pick list    self-heal
    [Arguments]    ${dropdownFieldArg}
    # Strategy 1 — existing full locator (exact aria-label)
    ${loc1}=    Replace String    ${dropdownDialogLocator}    <dropdown-field>    ${dropdownFieldArg}
    ${ok1}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc1}    timeout=2s
    IF    ${ok1}
        Scroll Element Into View With Fallback    ${loc1}
        ${aria_dis}=    Get Element Attribute    ${loc1}    aria-disabled
        ${enabled}=    Run Keyword And Return Status    Element Should Be Enabled    ${loc1}
        IF    '${aria_dis}' == 'true' or not ${enabled}
            Log    Open Dropdown With Fallback: "${dropdownFieldArg}" is disabled (dependent picklist?). Skipping.    WARN
            RETURN    ${FALSE}
        END
        ${el1}=    Get Webelement    ${loc1}
        Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${el1}
        RETURN    ${TRUE}
    END
    # Strategy 2 — contains(aria-label) for custom picklists whose label text is a partial match
    ${loc2}=    Set Variable    xpath://*[contains(@class,'modal-container')]//*[self::button or self::input][contains(@class,'slds-combobox__input')][contains(@aria-label,'${dropdownFieldArg}')]
    ${ok2}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc2}    timeout=2s
    IF    ${ok2}
        Scroll Element Into View With Fallback    ${loc2}
        ${aria_dis}=    Get Element Attribute    ${loc2}    aria-disabled
        ${enabled}=    Run Keyword And Return Status    Element Should Be Enabled    ${loc2}
        IF    '${aria_dis}' == 'true' or not ${enabled}
            Log    Open Dropdown With Fallback: "${dropdownFieldArg}" (contains match) is disabled. Skipping.    WARN
            RETURN    ${FALSE}
        END
        ${el2}=    Get Webelement    ${loc2}
        Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${el2}
        RETURN    ${TRUE}
    END
    # Strategy 3 — parent slds-form-element scope by label contains text
    ${loc3}=    Set Variable    xpath:(//*[contains(@class,'modal-container')]//div[contains(@class,'slds-form-element')][.//*[self::label or self::span][contains(normalize-space(),'${dropdownFieldArg}')]]//*[self::button or self::input][contains(@class,'combobox') or contains(@class,'slds-combobox__input')])[1]
    ${ok3}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc3}    timeout=2s
    IF    ${ok3}
        Scroll Element Into View With Fallback    ${loc3}
        ${aria_dis}=    Get Element Attribute    ${loc3}    aria-disabled
        ${enabled}=    Run Keyword And Return Status    Element Should Be Enabled    ${loc3}
        IF    '${aria_dis}' == 'true' or not ${enabled}
            Log    Open Dropdown With Fallback: "${dropdownFieldArg}" (scope match) is disabled. Skipping.    WARN
            RETURN    ${FALSE}
        END
        ${el3}=    Get Webelement    ${loc3}
        Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${el3}
        RETURN    ${TRUE}
    END
    Log    Open Dropdown With Fallback: could not find combobox for "${dropdownFieldArg}" with any strategy. Field may be absent on layout or not a picklist.    WARN
    RETURN    ${FALSE}

Select Dropdown Option
    [Documentation]    Selects an option in the expanded dropdown. Tier 1: direct ``data-value`` CSS on ``lightning-base-combobox-item``. Tier 2: scoped XPath union (Classic + LWC). Tier 3: JS ``querySelectorAll`` by ``data-value`` or ``title`` attribute. If all three tiers fail, logs the value and falls back to ``Select Random Dropdown Option In Modal`` so the test can continue rather than hard-failing on an org-specific picklist value.
    [Tags]    interaction    pick list
    [Arguments]    ${dropdownNameArg}    ${dropdownOptionArg}
    # Tier 1 — fast data-value CSS
    ${dvLoc}=    Replace String    ${dropdownOptionByDataValue}    <dropdown-value>    ${dropdownOptionArg}
    ${ok_dv}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${dvLoc}    timeout=3s
    IF    ${ok_dv}
        Scroll Element Into View With Fallback    ${dvLoc}
        Click Element    ${dvLoc}
        RETURN
    END
    # Tier 2 — scoped XPath union (Classic + LWC)
    ${dropdownOptionsDialogLocator}=    Replace String
    ...    ${dropdownOptionsDialogLocator}
    ...    <dropdown-field>
    ...    ${dropdownNameArg}
    ${dropdownOption}=    Replace String    ${dropdownOptionsDialogLocator}    <dropdown-value>    ${dropdownOptionArg}
    ${ok_xpath}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${dropdownOption}    timeout=3s
    IF    ${ok_xpath}
        Scroll Element Into View With Fallback    ${dropdownOption}
        Click Element    ${dropdownOption}
        RETURN
    END
    # Tier 3 — JS querySelectorAll by data-value or title
    ${js_clicked}=    Execute Javascript
    ...    var val = arguments[0];
    ...    var sel = 'lightning-base-combobox-item[data-value=\"' + val + '\"]';
    ...    var el = document.querySelector(sel);
    ...    if (!el) { sel = 'lightning-base-combobox-item[title=\"' + val + '\"]'; el = document.querySelector(sel); }
    ...    if (!el) { var all = document.querySelectorAll('lightning-base-combobox-item[role=\"option\"]'); for (var i=0;i<all.length;i++){if(all[i].textContent.trim()===val){el=all[i];break;}} }
    ...    if (el) { el.scrollIntoView({block:'center'}); el.click(); return true; }
    ...    return false;
    ...    ARGUMENTS    ${dropdownOptionArg}
    IF    ${js_clicked}
        RETURN
    END
    # All tiers exhausted — value likely doesn't exist in this org; pick a random valid option instead
    Log    Select Dropdown Option: "${dropdownOptionArg}" not found for "${dropdownNameArg}" via any tier. Falling back to random valid option.    WARN
    Select Random Dropdown Option In Modal

Select Multiselect Option
    [Documentation]    Dual-list / dueling-list multiselect in the **modal**: for each value in ``@{selected_values}``, finds the row in the **first** ``slds-dueling-list__options`` list (Available), clicks it, then clicks a **Move to Chosen**-style control scoped under the field. ``${fieldLabel}`` should match visible label/legend text (substring match). No-op if the value list is empty. Requires light-DOM list items (standard SLDS); closed shadow roots need different tooling.
    [Tags]    interaction    multiselect
    [Arguments]    ${fieldLabel}    @{selected_values}
    ${n}=    Get Length    ${selected_values}
    Return From Keyword If    ${n} == 0
    ${scope}=    Replace String    ${multiselectScopeDialogLocator}    <field-label>    ${fieldLabel}
    Wait Until Element Is Visible    ${scope}    timeout=15s
    Scroll Element Into View With Fallback    ${scope}
    FOR    ${val}    IN    @{selected_values}
        ${item}=    Set Variable    ${scope}//div[contains(@class,'slds-dueling-list')]//ul[contains(@class,'slds-dueling-list__options')][1]//li[.//span[normalize-space()='${val}']]
        ${ok_item}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${item}    timeout=5s
        IF    not ${ok_item}
            ${item}=    Set Variable    ${scope}//div[contains(@class,'slds-dueling-list')]//ul[contains(@class,'slds-dueling-list__options')][1]//*[@role='option'][.//span[normalize-space()='${val}']]
            Wait Until Element Is Visible    ${item}    timeout=10s
        END
        Scroll Element Into View With Fallback    ${item}
        Click Element    ${item}
        @{move_x}=    Create List
        ...    ${scope}//button[contains(@title,'Move selection to Chosen')]
        ...    ${scope}//button[contains(translate(@title,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'chosen')]
        ...    ${scope}//button[contains(@aria-label,'Chosen')]
        ...    ${scope}//button[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'chosen')]
        ${moved}=    Set Variable    ${FALSE}
        FOR    ${mb}    IN    @{move_x}
            ${click_ok}=    Run Keyword And Return Status    Click Element    ${mb}
            IF    ${click_ok}
                ${moved}=    Set Variable    ${TRUE}
                Exit For Loop
            END
        END
        IF    not ${moved}
            Fail    Multiselect "${fieldLabel}": could not click a "Move to Chosen" control after selecting "${val}".
        END
    END

Select Random Dropdown Option In Modal
    [Documentation]    After ``Open Dropdown``, picks a **random** visible ``lightning-base-combobox-item``. Fast 2 s CSS wait; falls through to JS ``querySelectorAll`` immediately when the animation hasn't resolved, avoiding long Selenium waits.
    [Tags]    interaction    pick list
    ${css_ok}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${comboboxOption}    timeout=2s
    IF    not ${css_ok}
        Run Keyword And Return Status    Wait Until Element Is Visible    xpath://lightning-base-combobox-item[@role='option']    timeout=2s
    END
    @{scopes}=    Create List
    ...    css:.modal-container lightning-base-combobox-item[role='option']
    ...    css:.slds-dropdown lightning-base-combobox-item[role='option']
    ...    css:.slds-listbox lightning-base-combobox-item[role='option']
    ...    css:lightning-base-combobox-item[role='option']
    ...    xpath://*[contains(@class,'modal-container')]//lightning-base-combobox-item[@role='option']
    ...    xpath://lightning-base-combobox-item[@role='option']
    @{pick_list}=    Create List
    FOR    ${scope}    IN    @{scopes}
        ${batch}=    Get WebElements    ${scope}
        ${len}=    Get Length    ${batch}
        IF    ${len} > 0
            @{pick_list}=    Copy List    ${batch}
            Exit For Loop
        END
    END
    ${n}=    Get Length    ${pick_list}
    IF    ${n} == 0
        ${js_count}=    Execute Javascript    return document.querySelectorAll('lightning-base-combobox-item[role="option"]').length;
        IF    ${js_count} > 0
            Execute Javascript
            ...    var items = document.querySelectorAll('lightning-base-combobox-item[role="option"]');
            ...    var pick = items[Math.floor(Math.random() * items.length)];
            ...    pick.scrollIntoView({block:'center'}); pick.click();
            RETURN
        END
        Fail    No combobox options found (Selenium scopes + JS querySelectorAll). Dropdown may not have opened or items are in a closed shadow root.
    END
    ${r}=    Evaluate    random.randint(0, int(${n}) - 1)    modules=random
    ${el}=    Get From List    ${pick_list}    ${r}
    Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${el}

Select Random Valid Picklist Option
    [Documentation]    After ``Open Dropdown``, scans combobox items using **CSS-first** locators (§1.1). Fast 2 s CSS wait; falls through to Selenium scopes + JS immediately when animation hasn't resolved. Keeps only options with **non-empty** ``data-value`` and text not equal to ``--None--``. Picks one at random.
    [Tags]    interaction    pick list
    ${css_ok}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${comboboxOption}    timeout=2s
    IF    not ${css_ok}
        Run Keyword And Return Status    Wait Until Element Is Visible    xpath://lightning-base-combobox-item[@role='option']    timeout=2s
    END
    @{scopes}=    Create List
    ...    css:.modal-container lightning-base-combobox-item[role='option']
    ...    css:.slds-dropdown lightning-base-combobox-item[role='option']
    ...    css:.slds-listbox lightning-base-combobox-item[role='option']
    ...    css:lightning-base-combobox-item[role='option']
    ...    xpath://*[contains(@class,'modal-container')]//lightning-base-combobox-item[@role='option']
    ...    xpath://lightning-base-combobox-item[@role='option']
    @{valid}=    Create List
    FOR    ${scope}    IN    @{scopes}
        ${batch}=    Get WebElements    ${scope}
        ${len}=    Get Length    ${batch}
        IF    ${len} == 0
            Continue For Loop
        END
        @{valid}=    Create List
        FOR    ${el}    IN    @{batch}
            ${dv}=    Get Element Attribute    ${el}    data-value
            ${dv}=    Strip String    ${dv}
            ${dv_len}=    Get Length    ${dv}
            ${txt}=    Get Text    ${el}
            ${txt}=    Strip String    ${txt}
            ${txt_l}=    Convert To Lower Case    ${txt}
            ${is_none_label}=    Set Variable If    '${txt_l}' == '--none--'    ${TRUE}    ${FALSE}
            IF    ${dv_len} > 0 and not ${is_none_label}
                Append To List    ${valid}    ${el}
            END
        END
        ${vc}=    Get Length    ${valid}
        IF    ${vc} > 0
            Exit For Loop
        END
    END
    ${vc}=    Get Length    ${valid}
    Should Be True    ${vc} > 0    No valid picklist options after filtering (need non-empty data-value; exclude --None--).
    ${r}=    Evaluate    random.randint(0, int(${vc}) - 1)    modules=random
    ${pick}=    Get From List    ${valid}    ${r}
    Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${pick}

Select First Lightning Dropdown Option In Modal
    [Documentation]    Deprecated name: now selects a **random** visible option (same as ``Select Random Dropdown Option In Modal``) for orgs where ``data-value`` or portal placement broke the old first-item xpath.
    [Tags]    interaction    pick list
    Select Random Dropdown Option In Modal

Open Dropdown And Select First Option
    [Documentation]    Opens the picklist and selects a **random** visible Lightning option (stable when exact labels or DOM differ). For an exact label use ``Open Dropdown`` + ``Select Dropdown Option``.
    [Tags]    interaction    pick list
    [Arguments]    ${dropdownFieldArg}
    Open Dropdown    ${dropdownFieldArg}
    Select Random Dropdown Option In Modal

Enter Text
    [Documentation]    Use this keyword to enter a value into the Input Field. It first identifies the input field using the provided label name, waits for the field to become visible, and scrolls it into view. It then clicks on the input field using JavaScript to ensure it is focused, and finally enters the specified text value into the field.
    [Tags]    interaction    input field
    [Arguments]    ${labelName}    ${textValue}
    ${inputField}=    Replace String    ${inputFieldDialogLocator}    <field-name>    ${labelName}
    Wait Until Element Is Visible    ${inputField}    timeout=5s
    Scroll Element Into View With Fallback    ${inputField}
    ${inputTextFieldJS}=    Get Webelement    ${inputField}
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${inputTextFieldJS}
    Input Text    ${inputField}    ${textValue}

Enter Text With Fallback
    [Documentation]    Resilient variant of ``Enter Text`` for custom / non-standard Salesforce fields.
    ...    Tries four strategies in order:
    ...    1. Exact label match (``inputFieldDialogLocator``).
    ...    2. Broad label-contains match (``customInputFieldDialogLocator``).
    ...    3. Direct ``aria-label`` attribute on the input/textarea itself.
    ...    4. ``data-field-name`` attribute on surrounding form element's input.
    ...    Logs a WARN and returns ${FALSE} if all four fail; never blocks the test.
    [Tags]    interaction    input field    self-heal
    [Arguments]    ${labelName}    ${textValue}
    # Strategy 1 — exact label
    ${loc1}=    Replace String    ${inputFieldDialogLocator}    <field-name>    ${labelName}
    ${ok1}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc1}    timeout=4s
    IF    ${ok1}
        Scroll Element Into View With Fallback    ${loc1}
        ${el1}=    Get Webelement    ${loc1}
        Execute Javascript    arguments[0].click();    ARGUMENTS    ${el1}
        Input Text    ${loc1}    ${textValue}
        RETURN    ${TRUE}
    END
    # Strategy 2 — broad label-contains (custom fields with extra markup)
    ${loc2}=    Replace String    ${customInputFieldDialogLocator}    <field-name>    ${labelName}
    ${ok2}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc2}    timeout=4s
    IF    ${ok2}
        Scroll Element Into View With Fallback    ${loc2}
        ${el2}=    Get Webelement    ${loc2}
        Execute Javascript    arguments[0].click();    ARGUMENTS    ${el2}
        Input Text    ${loc2}    ${textValue}
        RETURN    ${TRUE}
    END
    # Strategy 3 — aria-label directly on the input/textarea
    ${loc3}=    Set Variable    xpath://*[contains(@class,'modal-container')]//*[(self::input or self::textarea)][@aria-label='${labelName}']
    ${ok3}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc3}    timeout=4s
    IF    ${ok3}
        Scroll Element Into View With Fallback    ${loc3}
        ${el3}=    Get Webelement    ${loc3}
        Execute Javascript    arguments[0].click();    ARGUMENTS    ${el3}
        Input Text    ${loc3}    ${textValue}
        RETURN    ${TRUE}
    END
    # Strategy 4 — data-field-name attribute (LWC custom fields rendered with API name)
    ${loc4}=    Set Variable    xpath://*[contains(@class,'modal-container')]//*[self::input or self::textarea][contains(@data-field-name,'${labelName}') or contains(@name,'${labelName}')]
    ${ok4}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc4}    timeout=4s
    IF    ${ok4}
        Scroll Element Into View With Fallback    ${loc4}
        ${el4}=    Get Webelement    ${loc4}
        Execute Javascript    arguments[0].click();    ARGUMENTS    ${el4}
        Input Text    ${loc4}    ${textValue}
        RETURN    ${TRUE}
    END
    Log    Enter Text With Fallback: could not locate field "${labelName}" with any strategy (exact label, contains label, aria-label, data-field-name). Field may be read-only, absent on layout, or hidden.    WARN
    RETURN    ${FALSE}

Enter Date
    [Documentation]    Enters a date value into a date input field. It first identifies the date field using the provided field name, waits for the field to become visible, and scrolls it into view. It then enters the specified date value into the field.
    [Tags]    interaction    date
    [Arguments]    ${dateNameArg}    ${dateValue}
    ${dateField}=    Replace String    ${dateFieldDialogLocator}    <date-field-name>    ${dateNameArg}
    Wait Until Element Is Visible    ${dateField}    timeout=5s
    Scroll Element Into View    ${dateField}
    Input Text    ${dateField}    ${dateValue}

Enter Time
    [Documentation]    Enters a time value into a time input field. It first identifies the time field using the provided field name, waits for the field to become visible, and scrolls it into view. The keyword clears any existing value by selecting the text and using the BACKSPACE key before entering the specified time value into the field.
    [Tags]    interaction    time
    [Arguments]    ${timeNameArg}    ${timeValue}
    ${timeField}=    Replace String    ${timeFieldDialogLocator}    <time-field-name>    ${timeNameArg}
    Wait Until Element Is Visible    ${timeField}    timeout=5s
    Scroll Element Into View    ${timeField}
    Press Keys    ${timeField}    CTRL+a    BACKSPACE
    Input Text    ${timeField}    ${timeValue}
    Press Key    ${timeField}    \\27

Click Checkbox
    [Documentation]    Check or uncheck a Salesforce checkbox. It identifies the checkbox element using the provided checkbox name, waits for the checkbox to become visible, scrolls it into view, and then clicks the checkbox to select or deselect it.
    [Tags]    interaction    checkbox
    [Arguments]    ${checkboxNameArg}
    ${checkboxField}=    Replace String    ${checkboxDialogLocator}    <checkbox-field>    ${checkboxNameArg}
    Wait Until Page Contains Element    ${checkboxField}    timeout=5s
    Scroll Element Into View    ${checkboxField}
    ${checkboxJS}=    Get Webelement    ${checkboxField}
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${checkboxJS}

# Verifies redirection to the record details page on successful record creation

Verify Redirection to Record Details Page
    [Documentation]    Verifies the redirection to a record details page after a specific action, generally used after new record creation. It first identifies the record details element using the provided entity name, waits for it to become visible, and confirms its presence on the page. The test then waits for the success toast message to appear, ensuring the action was successful, and verifies that the toast message is no longer visible.
    [Tags]    verification    records
    [Arguments]    ${entityNameArg}
    ${entityName}=    Replace String    ${entityNameLocator}    <entity-name>    ${entityNameArg}
    Set Test Variable    ${entityName}    ${entityName}
    Wait Until Element Is Visible    ${entityName}    timeout=20s
    Element Should Be Visible    ${entityName}
    Wait Until Element Is Visible    ${successtoastmessagelocator}    timeout=15s
    Wait Until Element Is Not Visible    ${successtoastmessagelocator}    timeout=15s

Delete Current Record
    [Documentation]    Deletes the current record by performing an action on the record details page header. It invokes the "Delete" action on the record details page header and selects the "Delete" button in the dialog. After the deletion, it waits for the appropriate redirection tab to become visible, ensuring the user is redirected correctly. Finally, the test verifies the appearance and disappearance of the success toast message, confirming that the deletion action was completed successfully.
    [Tags]    interaction    delete    records
    [Arguments]    ${recordTypeArg}    ${recordActionArg}=Delete
    # Remove spaces from the provided record type argument
    Perform Action On Record Details Page Header    ${recordTypeArg}    ${recordActionArg}
    Select Dialog Button    Delete
    ${pluralRecordType}=    Get Plural Form    ${recordTypeArg}
    ${redirectTab}=    Replace String    ${activeTabLocator}    <tab-name>    ${pluralRecordType}
    Wait Until Element Is Visible    ${redirectTab}    timeout=40s
    Page Should Contain Element    ${redirectTab}
    Wait Until Element Is Visible    ${successToastMessageLocator}    timeout=15s
    Wait Until Element Is Not Visible    ${successToastMessageLocator}    timeout=15s

Get Plural Form
    [Documentation]    This keyword returns the plural form of a given record type string. It first checks if the word ends with "y" and the preceding character is a consonant. If this condition is true, it replaces the "y" with "ies" to form the plural. Otherwise, it appends an "s" to the word to form the plural. This approach is designed to handle typical English pluralization rules.
    [Tags]    utilities
    [Arguments]    ${recordTypeArg}
    # Check if the word ends with "y" and the preceding character is a consonant
    ${lastChar}=    Evaluate    '${recordTypeArg}'[-1]
    ${secondLastChar}=    Evaluate    '${recordTypeArg}'[-2] if len('${recordTypeArg}') > 1 else ''
    ${isConsonant}=    Evaluate
    ...    '${secondLastChar}'.lower() not in ['a', 'e', 'i', 'o', 'u'] if '${secondLastChar}' else False
    IF    '${lastChar}' == 'y' and '${isConsonant}'
        ${pluralForm}=    Evaluate    '${recordTypeArg}'[:-1] + 'ies'
    ELSE
        ${pluralForm}=    Evaluate    '${recordTypeArg}' + 's'
    END
    RETURN    ${pluralForm}

Verify Record Creation With Data
    [Documentation]    Verifies the creation of a record by checking if the specified data is correctly displayed in the appropriate field. Record type and field names are normalized (spaces removed) for ``data-target-selection-name`` locators. For fields whose API-style name is ``Phone``, ends with ``Phone`` (e.g. ``MobilePhone``), or is ``Fax``, compares **digits only**: UI text and expected value are passed through ``Replace String Using Regexp`` with ``\\D+`` removed so ``(123) 456-7890`` matches ``1234567890``. All other fields still use ``contains(text(),'<actual-data>')`` in the xpath. Use ``Checkbox-Check`` and ``Checkbox-Uncheck`` for checkboxes.
    [Tags]    records    verification
    [Arguments]    ${recordDataTypeArg}    ${recordDataFieldArg}    ${recordDataArg}
    ${recordDataTypeArg}=    Replace String    ${recordDataTypeArg}    ${SPACE}    ${EMPTY}
    ${recordDataFieldArg}=    Replace String    ${recordDataFieldArg}    ${SPACE}    ${EMPTY}
    ${fk_l}=    Convert To Lower Case    ${recordDataFieldArg}
    ${is_phoneish}=    Evaluate    '''${fk_l}''' == 'phone' or '''${fk_l}'''.endswith('phone') or '''${fk_l}''' == 'fax'
    ${recordDataType}=    Replace String    ${recordDataLocator}    <record-type>    ${recordDataTypeArg}
    ${recordDataField}=    Replace String    ${recordDataType}    <field-name>    ${recordDataFieldArg}
    IF    ${is_phoneish} and '${recordDataArg}' != 'Checkbox-Check' and '${recordDataArg}' != 'Checkbox-Uncheck'
        ${block}=    Replace String    ${recordFieldBlockLocator}    <record-type>    ${recordDataTypeArg}
        ${block}=    Replace String    ${block}    <field-name>    ${recordDataFieldArg}
    Wait Until Element Is Visible    ${block}    timeout=10s
    Scroll Element Into View    ${block}
    Element Should Be Visible    ${block}
    ${ui_text}=    Get Text    ${block}
        ${ui_digits}=    Replace String Using Regexp    ${ui_text}    \\D+    ${EMPTY}
        ${exp_digits}=    Replace String Using Regexp    ${recordDataArg}    \\D+    ${EMPTY}
        Should Be Equal As Strings    ${ui_digits}    ${exp_digits}
        RETURN
    END
    ${recordActualDataLocator}=    Replace String    ${recordDataField}    <actual-data>    ${recordDataArg}
    IF    '${recordDataArg}' == 'Checkbox-Check'
        ${recordActualDataLocator}=    Set Variable    ${recordActualDataLocator}\[@checked]
    ELSE IF    '${recordDataArg}' == 'Checkbox-Uncheck'
        ${recordActualDataLocator}=    Set Variable    ${recordActualDataLocator}\[not(@checked)]
    END
    Wait Until Page Contains Element    ${recordActualDataLocator}    timeout=10s
    Scroll Element Into View    ${recordActualDataLocator}
    Element Should Be Visible    ${recordActualDataLocator}

Open Related Record Dropdown
    [Documentation]    This keyword is used to open a related record dialog by interacting with a dropdown on the record details page. It first identifies the dropdown element using the provided related record type, waits for it to become visible, and scrolls it into view. After clicking on the dropdown, the keyword locates the specified dropdown option, waits for it to be visible, and clicks on it to open the related record dialog. Finally, it ensures that the dialog is visible.
    [Tags]    interaction    records    related record
    [Arguments]    ${relatedRecordDropdownArg}    ${relatedRecordDropdownOptionArg}
    ${relatedRecordDropdownName}=    Replace String
    ...    ${relatedRecordDropdownNameLocator}
    ...    <record-type>
    ...    ${relatedRecordDropdownArg}
    Wait Until Element Is Visible    ${relatedRecordDropdownName}    timeout=10s
    Scroll Element Into View    ${relatedRecordDropdownName}
    Wait Until Element Is Enabled    ${relatedRecordDropdownName}    timeout=5s
    ${relatedRecordDropdown}=    Replace String
    ...    ${relatedRecordDropdownLocator}
    ...    <record-type>
    ...    ${relatedRecordDropdownArg}
    Wait Until Element Is Visible    ${relatedRecordDropdown}    timeout=10s
    Scroll Element Into View    ${relatedRecordDropdown}
    ${relatedRecordDropdownJS}=    Get WebElement    ${relatedRecordDropdown}
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${relatedRecordDropdownJS}
    ${relatedRecordDropdownOption}=    Replace String
    ...    ${relatedRecordDropdownOptionLocator}
    ...    <dropdown-option>
    ...    ${relatedRecordDropdownOptionArg}
    Wait Until Element Is Visible    ${relatedRecordDropdownOption}    timeout=20s
    Scroll Element Into View    ${relatedRecordDropdownOption}
    Click Element    ${relatedRecordDropdownOption}
    Wait Until Element Is Visible    ${dialogLocator}    timeout=15s

Get Success Toast Message Related Record Creation ID
    [Documentation]    This keyword is used to fetch the record ID from the success toast message that appears on the record details page when related records are created. It waits for the toast message to become visible, retrieves its text, and stores it in a test variable. After the record ID is captured, it waits for the success toast message to disappear, confirming that the record creation process has been completed. This is typically used to capture the record ID generated from the success toast message after creating related records on the details page.
    [Tags]    records    related record    utilities
    Wait Until Element Is Visible    ${successToastMessageOnRecordDetailsPageLocator}    timeout=120s
    ${successToastMessageOnRecordDetailsPage}=    Get Text    ${successToastMessageOnRecordDetailsPageLocator}
    Set Test Variable    ${successToastMessageOnRecordDetailsPage}    ${successToastMessageOnRecordDetailsPage}
    Wait Until Element Is Not Visible    ${successToastMessageOnRecordDetailsPageLocator}    timeout=25s

Verify Related Records Creation
    [Documentation]    Verifies the creation of related records by opening the Related Records List View from the Record Details Page. It clicks on the "View All" button in the related records section, waits for the list view to become visible, and then verifies the presence of the newly created related record. Specifically, it verifies the presence of the record ID in the related records list view, using the Verify Table Cell Record keyword.
    [Tags]    records    verification    related record
    [Arguments]    ${relatedRecordNameArg}
    ${relatedRecordsViewAllLocator}=    Replace String
    ...    ${relatedRecordsViewAllLocator}
    ...    <record-type>
    ...    ${relatedRecordNameArg}
    Wait Until Element Is Visible    ${relatedRecordsViewAllLocator}    timeout=10s
    ${relatedRecordsViewAllLocatorJS}=    Get Webelement    ${relatedRecordsViewAllLocator}
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${relatedRecordsViewAllLocatorJS}
    ${realtedRecordListViewTitleLocator}=    Replace String
    ...    ${realtedRecordListViewTitleLocator}
    ...    <record-type>
    ...    ${relatedRecordNameArg}
    Wait Until Element Is Visible    ${realtedRecordListViewTitleLocator}    timeout=15s
    Verify Table Cell Record    ${successToastMessageOnRecordDetailsPage}

Verify Table Cell Record
    [Documentation]    Verify the presence of a record ID in a table cell on a page. It checks if the specified record ID appears within a table. This keyword is useful when verifying the presence of newly created records or ensuring that a record exists in a table based on its ID.
    [Tags]    verification    records    utilities
    [Arguments]    ${recordIdArg}    ${recordIdPos}=1
    ${tableCellLocator}=    Replace String    ${tableCellLocator}    <record-id>    ${recordIdArg}
    ${tableCellLocator}=    Replace String    ${tableCellLocator}    <pos>    ${recordIdPos}
    Wait Until Page Contains Element    ${tableCellLocator}    timeout=10s
    Page Should Contain Element    ${tableCellLocator}

Return Back To Parent
    [Documentation]    Navigates back to the parent record from a related record view. It reloads the page, waits for the breadcrumb (indicating the parent record) to become visible, and clicks on it to return to the parent record's details page. After navigating back, it ensures the parent record is visible and confirms successful navigation.
    [Tags]    navigation    back
    Reload Page
    Wait Until Element Is Visible    ${relatedRecordParentBreadcrumbLocator}    timeout=10s
    Click Element    ${relatedRecordParentBreadcrumbLocator}
    Wait Until Element Is Visible    ${entityNameLocator}    timeout=15s
    Element Should Be Visible    ${entityNameLocator}
#    wait until page does not contain element    ${spinnerLoadingWOLocator}    timeout=20s

Return To Previous Page
    [Documentation]    Navigates back to the previous page in the browser history. It simulates the "Back" action, typically used to return to the previous screen or page from the current one.
    [Tags]    navigation    back
    Go Back

Convert View From Intelligent To List
    [Documentation]    Switches to List view when Salesforce shows Intelligence/List toggles. If **no** toggle appears (already in List view, split view, or org-specific layout), exits without failing—this is the usual fix for "List View button not found". Otherwise clicks List View using exact then flexible locators.
    [Tags]    utilities    view
    ${hasIntel}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible    ${intelligentListButton}    timeout=4s
    ${listExact}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible    ${listViewButton}    timeout=5s
    ${listFlex}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible    ${listViewButtonFlexible}    timeout=3s
    IF    not ${hasIntel} and not ${listExact} and not ${listFlex}
        Log    No List/Intelligence view toggle found—likely already in List view; continuing.    INFO
        RETURN
    END
    IF    ${listExact}
        Scroll Element Into View With Fallback    ${listViewButton}
        ${ok}=    Run Keyword And Return Status    Click Element    ${listViewButton}
        IF    ${ok}
            Wait Until Element Is Not Visible    ${listViewSearchSpinner}    timeout=10s
            RETURN
        END
    END
    IF    ${listFlex}
        Scroll Element Into View With Fallback    ${listViewButtonFlexible}
        ${ok2}=    Run Keyword And Return Status    Click Element    ${listViewButtonFlexible}
        IF    ${ok2}
            Wait Until Element Is Not Visible    ${listViewSearchSpinner}    timeout=10s
            RETURN
        END
    END
    Log    Could not click a List View control; page may still work for New / list actions.    WARN

Prepare Quick Action Header Locator
    [Documentation]    This keyword is used to prepare the locator for the quick action header based on the provided record type and optional record action. If a record action is provided, it constructs the locator for the quick action header related to that action. If no action is specified, it generates the locator for the quick action dropdown.
    [Tags]    utilities
    [Arguments]    ${recordTypeArg}    ${recordActionArg}=${EMPTY}
    ${recordTypeArgStrip}=    Replace String    ${recordTypeArg}    ${SPACE}    ${EMPTY}
    IF    '${recordActionArg}' != '${EMPTY}'
        ${quickActionHeaderLocator}=    Replace String
        ...    ${actionRecordTypeLocator}
        ...    <record-type>
        ...    ${recordTypeArgStrip}
        ${quickActionHeaderLocator}=    Replace String
        ...    ${quickActionHeaderLocator}
        ...    <record-action>
        ...    ${recordActionArg}
        RETURN    ${quickActionHeaderLocator}
    ELSE
        ${headerQuickActionDropdownLocator}=    Replace String
        ...    ${headerQuickActionDropdownLocator}
        ...    <record-type>
        ...    ${recordTypeArgStrip}
        RETURN    ${headerQuickActionDropdownLocator}
    END

Perform Action On Record Details Page Header
    [Documentation]    This keyword performs an action (such as clicking) on the record details page header. It first checks if the action button is visible, and if it exists, clicks it. If the button doesn't exist, the keyword will first click on the dropdown to make the action button visible and then perform the action.
    [Tags]    utilities
    [Arguments]    ${recordTypeArg}    ${recordActionArg}=${EMPTY}
    ${recordType}=    Prepare Quick Action Header Locator    ${recordTypeArg}    ${recordActionArg}
    ${quickActionButtonExists}=    Run Keyword and Return Status
    ...    Wait Until Element Is Visible
    ...    ${recordType}
    ...    timeout=5s
    IF    '${quickActionButtonExists}' == 'True'
        Click Element    ${recordType}
    ELSE
        ${recordTypeDropdownLocator}=    Prepare Quick Action Header Locator    ${recordTypeArg}
        Wait Until Element Is Visible    ${recordTypeDropdownLocator}    timeout=5s
        Click Element    ${recordTypeDropdownLocator}
        Wait Until Element Is Visible    ${recordType}    timeout=5s
        Click Element    ${recordType}
    END

Select Account Record Type
    [Documentation]    This keyword is used to select a specific account record type on the Account Record Type Dialog selection page. It waits for the account record type to become visible, scrolls the element into view, and clicks on it. After selecting the record type, it navigates to the next step by clicking the "Next" button in the dialog and verifies that the dialog title for the new account record type creation page is visible.
    [Tags]    interaction    radio button
    [Arguments]    ${accountRecordTypeArg}
    ${accountRecordType}=    Replace String
    ...    ${accountRecordTypeLocator}
    ...    <account-record-type>
    ...    ${accountRecordTypeArg}
    Wait Until Element Is Visible    ${accountRecordType}    timeout=10s
    Scroll Element Into View With Fallback    ${accountRecordType}
    Click Element    ${accountRecordType}
    Select Dialog Button    Next
    ${newRecordDialogTitle}=    Replace String    ${newRecordDialogTitleLocator}    <record-name>    Account
    Wait Until Element Is Visible    ${newRecordDialogTitle}    timeout=15s

Visit Dynamic Form Section
    [Documentation]    Scrolls to a dynamic form section by section title. Targets the section ``h3`` (not the inner span) and uses a JS scroll fallback so SLDS does not throw "element has no size and location".
    [Tags]    navigation    records
    [Arguments]    ${dynamicFormInformationSectionArg}
    ${dynamicFormInformationSection}=    Replace String
    ...    ${dynamicFormInformationSectionLocator}
    ...    <title-name>
    ...    ${dynamicFormInformationSectionArg}
    Wait Until Page Contains Element    ${dynamicFormInformationSection}    timeout=10s
    Scroll Element Into View With Fallback    ${dynamicFormInformationSection}

Change List View
    [Documentation]    Changes the view type in the list view.
    [Tags]    pick list    interaction
    [Arguments]    ${listViewRecordTypeArg}    ${listViewDropdownOptionArg}
    Reload Page
    ${listViewRecordType}=    Replace String
    ...    ${listViewDropdownLocator}
    ...    <record-type>
    ...    ${listViewRecordTypeArg}
    ${listViewDropdownOption}=    Replace String
    ...    ${listViewDropdownOptionLocator}
    ...    <dropdown-value>
    ...    ${listViewDropdownOptionArg}
    Wait Until Element Is Visible    ${listViewRecordType}    timeout=10s
    Click Element    ${listViewRecordType}
    Wait Until Element Is Visible    ${listViewDropdownOption}    timeout=10s
    Click Element    ${listViewDropdownOption}
    Wait Until Element Is Not Visible    ${listViewSearchSpinner}    timeout=15s

Search In List View
    [Documentation]    Searches for a specific record in the list view. Inputs the record ID into the search box, performs the search, and verifies the presence of the record in the resulting list.
    [Tags]    search in list view    interaction
    [Arguments]    ${recordIdArg}
    ${listViewSearchInput}=    Replace String    ${searchInputFieldLocator}    <search-input-field>    this list
    Wait Until Element Is Visible    ${listViewSearchInput}    timeout=10s
    Input Text    ${listViewSearchInput}    ${recordIdArg}
    Press Key    ${listViewSearchInput}    \\13    # ASCII code for enter key
    Wait Until Element Is Not Visible    ${listViewSearchSpinner}    timeout=15s
    ${emptyContainerExists}=    Run Keyword and Return Status
    ...    Wait Until Element Is Visible
    ...    ${emptyContainerListViewLocator}
    ...    timeout=5s
    IF    '${emptyContainerExists}' == 'False'
        Verify Table Cell Record    ${recordIdArg}
    ELSE
        Fail    No records found matching the criteria.
    END

Get Lead Convert Dialog New Fields
    [Documentation]    This keyword retrieves the prefilled value for a "Create New Field" within the Lead Conversion dialog. It constructs the appropriate locator by replacing a placeholder with the provided field name argument, waits until the element is present on the page, extracts the value from the element’s title attribute, and returns that value.
    [Tags]    records    utilities    modal
    [Arguments]    ${fieldNameArg}
    ${fieldName}=    Replace String    ${leadConvertFieldDialogLocator}    <field-name>    ${fieldNameArg}
    Wait Until Page Contains Element    ${fieldName}    timeout=10s
    ${fieldValue}=    Get Element Attribute    ${fieldName}    title
    RETURN    ${fieldValue}

Open Record From Table View
    [Documentation]    Opens a record from a table view by clicking on the record ID within a table cell on the page. This keyword dynamically constructs the locator for the table cell using the provided record identifier and optional search position, then clicks the element to open the record. It waits for the record’s detail page to load and verifies that the record is displayed by checking for the corresponding element on the page.
    [Tags]    interaction    records    utilities
    [Arguments]    ${fullRecordId}    ${cleanRecordId}=None    ${searchPosition}=1
    IF    '${cleanRecordId}' == 'None'
        ${cleanRecordId}=    Set Variable    ${fullRecordId}
    END
    ${recordCellLocator}=    Replace String    ${tableCellLocator}    <record-id>    ${cleanRecordId}
    ${recordCellLocator}=    Replace String    ${recordCellLocator}    <pos>    ${searchPosition}
    Click Element    ${recordCellLocator}
    ${recordName}=    Replace String    ${entityNameLocator}    <entity-name>    ${fullRecordId}
    Wait Until Page Contains Element    ${recordName}    timeout=20s
    Page Should Contain Element    ${recordName}

Change Opportunity Record Status
    [Documentation]    Updates the status of an Opportunity record in Salesforce. It locates the relevant status option, interacts with it, and submits the change. If necessary, it handles additional stage selections for closed statuses. The process includes validation steps to ensure the update is successful and visible on the interface.
    [Tags]    interaction    records    utilities
    [Arguments]    ${statusOptionArg}    ${statusStage}=None
    ${statusOption}=    Replace String    ${pathOption}    <path-option>    ${statusOptionArg}
    IF    '${statusStage}' != 'None'
        ${activeStatusOption}=    Replace String    ${activePathOption}    <path-option>    ${statusStage}
    ELSE
        ${activeStatusOption}=    Replace String    ${activePathOption}    <path-option>    ${statusOptionArg}
    END
    Wait Until Element Is Visible    ${statusOption}    timeout=10s
    Scroll Element Into View    ${statusOption}
    Mouse Down    ${statusOption}
    Mouse Up    ${statusOption}
    Wait Until Element Is Visible    ${submitPathStep}    timeout=5s
    Mouse Down    ${submitPathStep}
    Mouse Up    ${submitPathStep}
    IF    '${statusOptionArg}' != 'Closed' and '${statusStage}' == 'None'
        Wait Until Element Is Visible    ${successToastMessageLocator}    timeout=15s
        Wait Until Element Is Not Visible    ${successToastMessageLocator}    timeout=15s
        Wait Until Element Is Visible    ${activeStatusOption}    timeout=10s
        Element Should Be Visible    ${activeStatusOption}
    ELSE IF    '${statusOptionArg}' == 'Closed' and '${statusStage}' != 'None'
        Select From List By Value    ${closeStageSelectDialog}    ${statusStage}
        Select Dialog Button    Save
        Wait Until Element Is Visible    ${successToastMessageLocator}    timeout=15s
        Wait Until Element Is Not Visible    ${successToastMessageLocator}    timeout=15s
        Wait Until Element Is Visible    ${activeStatusOption}    timeout=10s
        Element Should Be Visible    ${activeStatusOption}
    END

Verify Error Message For Field
    [Documentation]    Verifies the error message for a specific field, accepting three arguments: page type (Dialog/Page), field name, and expected error message.
    [Tags]    records    verification    modal
    [Arguments]    ${pageType}    ${reqFieldName}    ${expectedErrorMessage}
    IF    '${pageType}' == 'Dialog'
        ${reqFieldName}=    Replace String    ${dialogFieldRequired}    <field-name>    ${reqFieldName}
    ELSE IF    '${pageType}' == 'Page'
        ${reqFieldName}=    Replace String    ${fieldRequired}    <field-name>    ${reqFieldName}
    ELSE
        Fail    Invalid Page Type: ${pageType}. Takes values as "Dialog" or "Page"
    END
    Wait Until Element Is Visible    ${reqFieldName}    timeout=10s
    Scroll Element Into View    ${reqFieldName}
    ${parentElement}=    Set Variable
    ...    document.evaluate('${reqFieldName}', document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
    # Extract the error message text
    ${errorTextJS}=    Set Variable
    ...    [].reduce.call(${parentElement}.childNodes, function(a, b) { return a + (b.nodeType === 3 ? b.textContent : ''); }, '');
    ${actualErrorMessage}=    Execute JavaScript    return ${errorTextJS}
    Should Be Equal As Strings    ${expectedErrorMessage}    ${actualErrorMessage}

Verify Field Present In Error Snag
    [Documentation]    Verifies that the mandatory field is present in the Error Snag Popup.
    [Tags]    records    verification    modal
    [Arguments]    ${reqSnagFieldName}
    ${reqSnagFieldName}=    Replace String    ${snagFieldRequired}    <snag-field-name>    ${reqSnagFieldName}
    Wait Until Element Is Visible    ${reqSnagFieldName}    timeout=5s
    Element Should Be Visible    ${reqSnagFieldName}

Resolve Tiered Locator
    [Documentation]    Tries each locator in order and returns the first one where the element is present on the page. Uses a short 0.5s probe per tier (CSS/aria locators resolve near-instantly when present). Falls back to the **last** locator so the caller always gets a usable value for subsequent ``Wait Until`` patterns.
    [Tags]    utilities    locators
    [Arguments]    @{locators}
    FOR    ${loc}    IN    @{locators}
        ${ok}=    Run Keyword And Return Status    Wait Until Page Contains Element    ${loc}    timeout=0.5s
        IF    ${ok}
            RETURN    ${loc}
        END
    END
    RETURN    ${locators}[-1]

# ---------------------------------------------------------------------------
# Infrastructure: Lightning waits, Shadow DOM, iFrame utilities
# ---------------------------------------------------------------------------

Wait For Lightning Spinners Absent
    [Documentation]    Waits until standard Salesforce Lightning spinners are no longer visible. Uses a single combined CSS selector to check all spinner variants in one pass instead of sequential waits. Falls back to individual checks only if the combined selector is not supported.
    [Tags]    utilities    wait    lightning
    [Arguments]    ${timeout}=15s
    ${combined}=    Set Variable    css:div.slds-spinner_container:not([style*='display: none']), div.slds-spinner:not([style*='display: none']), lightning-spinner
    ${has_any}=    Run Keyword And Return Status    Page Should Contain Element    ${combined}
    IF    ${has_any}
        Run Keyword And Ignore Error    Wait Until Element Is Not Visible    ${combined}    timeout=${timeout}
    END

Wait For Record Type Overlay Cleared
    [Documentation]    Waits until the Salesforce ``forceChangeRecordType`` overlay (record-type picker) is no longer visible. Uses a combined selector for speed.
    [Tags]    utilities    wait    lightning
    [Arguments]    ${timeout}=8s
    ${combined}=    Set Variable    css:div.forceChangeRecordType, section.forceChangeRecordType
    ${present}=    Run Keyword And Return Status    Page Should Contain Element    ${combined}
    IF    ${present}
        Run Keyword And Ignore Error    Wait Until Element Is Not Visible    ${combined}    timeout=${timeout}
    END

Click In Shadow Root
    [Documentation]    Clicks an element inside a **shadow root** that standard Selenium locators cannot reach. ``${host_css_selector}`` is a **CSS selector** for the light-DOM host element whose ``shadowRoot`` contains the target. ``${inner_css_selector}`` is resolved inside ``host.shadowRoot.querySelector``. Centralizes shadow-pierce JS so individual tests never contain ad-hoc shadow scripts.
    [Tags]    utilities    shadow dom
    [Arguments]    ${host_css_selector}    ${inner_css_selector}
    Execute Javascript
    ...    var host = document.querySelector(arguments[0]);
    ...    if (!host) { throw new Error('Shadow host not found: ' + arguments[0]); }
    ...    var root = host.shadowRoot;
    ...    if (!root) { throw new Error('No shadowRoot on host: ' + arguments[0]); }
    ...    var el = root.querySelector(arguments[1]);
    ...    if (!el) { throw new Error('Inner element not found in shadow: ' + arguments[1]); }
    ...    el.scrollIntoView({block:'center'});
    ...    el.click();
    ...    ARGUMENTS    ${host_css_selector}    ${inner_css_selector}

Run Keyword In Salesforce Iframe
    [Documentation]    Switches into the given iframe, runs ``${keyword_name}`` with ``@{args}``, and **always** switches back to the default content via ``TRY / FINALLY``. Use for Classic console tabs, Visualforce embeds, or any Salesforce page that renders inside an ``<iframe>``. ``${iframe_locator}`` should be a stable locator (``css:iframe[name='...']``, ``id``, ``title``).
    [Tags]    utilities    iframe
    [Arguments]    ${iframe_locator}    ${keyword_name}    @{args}
    Select Frame    ${iframe_locator}
    TRY
        Run Keyword    ${keyword_name}    @{args}
    FINALLY
        Unselect Frame
    END

Select State Or Province
    [Documentation]    Selects a US state in the Salesforce address State/Province field. Accepts either
    ...    abbreviations (CA, TX, NY) or full names (California, Texas, New York). Automatically
    ...    maps common abbreviations to full state names. Handles both picklist/dropdown and
    ...    text-input variants of the field: tries ``Open Dropdown`` first; if the field is a
    ...    text input instead, falls back to ``Enter Text With Fallback``.
    [Tags]    interaction    address    utilities
    [Arguments]    ${state_value}
    ${upper}=    Convert To Upper Case    ${state_value}
    ${upper}=    Strip String    ${upper}
    ${full_name}=    Evaluate    {"AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California","CO":"Colorado","CT":"Connecticut","DE":"Delaware","DC":"District of Columbia","FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming"}.get("${upper}", "${state_value}")
    # Try dropdown first (Salesforce renders State/Province as a picklist in most orgs)
    ${is_dropdown}=    Run Keyword And Return Status    Open Dropdown With Fallback    State/Province
    IF    ${is_dropdown}
        ${picked}=    Run Keyword And Return Status    Select Dropdown Option    State/Province    ${full_name}
        IF    not ${picked}
            Log    "${full_name}" not found in State/Province dropdown — trying abbreviation "${upper}".    WARN
            ${picked2}=    Run Keyword And Return Status    Select Dropdown Option    State/Province    ${upper}
            IF    not ${picked2}
                Select Random Valid Picklist Option
                Log    Neither "${full_name}" nor "${upper}" found — selected random state option.    WARN
            END
        END
    ELSE
        ${typed}=    Enter Text With Fallback    State/Province    ${full_name}
        IF    not ${typed}
            Log    State/Province field not found as dropdown or text input.    WARN
        END
    END

# ===========================================================================
# High-leverage AI-friendly composite keywords (Phase 2 of the AI brain plan).
# These wrap existing primitives with explicit signatures so the LLM can
# reach for them by name instead of inventing raw Click Element chains.
# Each one composes keywords already in this file and is safe to call from
# any Salesforce flow.
# ===========================================================================

Set Picklist Field
    [Documentation]    Sets a Salesforce Lightning picklist (combobox) field to an EXACT value.
    ...                Wraps ``Open Dropdown`` then ``Select Dropdown Option``. Use this for
    ...                "Set Lead Source to Web" / "Set Status to Working" -- never raw Click
    ...                Element on the combobox arrow. Tolerates whitespace and case differences
    ...                via the same tiered selectors used by ``Select Dropdown Option`` itself,
    ...                with a final fallback to a random valid option (logs a warning) so flaky
    ...                tests don't dead-stop on org-specific picklist values.
    [Tags]    interaction    pick list
    [Arguments]    ${field_label}    ${value}
    Open Dropdown    ${field_label}
    Select Dropdown Option    ${field_label}    ${value}

Set Lookup Field
    [Documentation]    Resolves a Salesforce lookup field (e.g. Account Name, Contact, Owner)
    ...                by typing ``${search_term}`` into the lookup input and clicking the
    ...                first matching suggestion in the popover. Use this for "Set Account
    ...                Name to Acme" / "Choose Contact John Doe". Wraps ``Enter Into Search
    ...                Field`` -- the existing keyword that already drives the
    ...                uiInput--lookup pattern. ``${exact_match}`` is reserved for a future
    ...                stricter mode; today the keyword always picks the first suggestion to
    ...                match how Salesforce's autocomplete behaves.
    [Tags]    interaction    lookup
    [Arguments]    ${field_label}    ${search_term}    ${exact_match}=${TRUE}
    Enter Into Search Field    ${field_label}    ${search_term}

Click Custom Header Button
    [Documentation]    Clicks a named action on the record detail page header. Handles both
    ...                top-level header buttons (visible directly on the page) and ones tucked
    ...                under the overflow ``▼`` menu. Wraps ``Perform Action On Record Details
    ...                Page Header`` so the AI doesn't need to remember the object label as a
    ...                separate argument -- pass the button label only and the underlying
    ...                keyword discovers the object from the breadcrumb. Use for "Click the
    ...                Approve button" / "Click custom button XYZ".
    [Tags]    interaction    custom-button    record-detail
    [Arguments]    ${button_label}    ${object_label}=${EMPTY}
    ${obj}=    Run Keyword If    '${object_label}' == '${EMPTY}'
    ...    Get Text    //span[@class='breadcrumbDetail slds-truncate']/..//ancestor::nav//li[1]//span
    ...    ELSE
    ...    Set Variable    ${object_label}
    Perform Action On Record Details Page Header    ${obj}    ${button_label}

Click List View Row Action
    [Documentation]    Opens the row-level action dropdown for a record on a list view (the
    ...                small ``▼`` at the end of each row) and clicks the named action. Use
    ...                for "From the Leads list, click the Edit action on lead XYZ" without
    ...                having to navigate into the record first. ``${record_identifier}`` is
    ...                matched against the visible cell text of the row.
    [Tags]    interaction    list-view    row-action
    [Arguments]    ${record_identifier}    ${action_label}
    ${row}=    Set Variable    //tr[.//a[normalize-space()='${record_identifier}'] or .//td[normalize-space()='${record_identifier}']]
    Wait Until Element Is Visible    ${row}    timeout=10s
    Scroll Element Into View With Fallback    ${row}
    Click Element    ${row}//button[contains(@class,'slds-button_icon-border-filled') or contains(@title,'Show More') or contains(@aria-haspopup,'true')]
    Wait Until Element Is Visible    //div[contains(@class,'slds-popover')]//*[normalize-space()='${action_label}']    timeout=8s
    Click Element    //div[contains(@class,'slds-popover')]//*[normalize-space()='${action_label}']

Open Quick Action
    [Documentation]    Clicks a Salesforce Quick Action on the record detail page (Log a Call,
    ...                New Note, Send Email, etc.). Looks for the action by visible label both
    ...                in the top-level row and in the overflow menu so it works regardless of
    ...                how many actions the layout exposes. Use for "Log a Call" / "Add a Note"
    ...                / any custom Quick Action.
    [Tags]    interaction    quick-action
    [Arguments]    ${action_label}
    ${top}=    Set Variable    //ul[contains(@class,'slds-button-group-list')]//*[normalize-space()='${action_label}']
    ${overflow}=    Set Variable    //div[contains(@class,'slds-popover')]//*[normalize-space()='${action_label}']
    ${visible_top}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${top}    timeout=4s
    IF    ${visible_top}
        Scroll Element Into View With Fallback    ${top}
        Click Element    ${top}
        RETURN
    END
    # Open the overflow ("More") menu and try again.
    Click Element    //button[contains(@title,'More Actions') or contains(@title,'Show more actions')]
    Wait Until Element Is Visible    ${overflow}    timeout=8s
    Click Element    ${overflow}

Verify Field Value On Detail Page
    [Documentation]    Asserts that ``${field_label}`` on the current record detail page shows
    ...                ``${expected_value}``. Tolerates output cell variants (read-only span vs
    ...                lightning-formatted-text vs anchor for lookup references). Use for
    ...                "Verify Lead Source is Web on the detail page". Comparison is exact
    ...                after trimming surrounding whitespace; pass the value as it appears in
    ...                the UI.
    [Tags]    verification    detail-page
    [Arguments]    ${field_label}    ${expected_value}
    @{candidates}=    Create List
    ...    //records-record-layout-item[.//*[normalize-space()='${field_label}']]//*[contains(@class,'slds-form-element__static') or self::lightning-formatted-text or self::a]
    ...    //div[contains(@class,'slds-form-element')][.//span[normalize-space()='${field_label}']]//*[contains(@class,'slds-form-element__static') or self::lightning-formatted-text or self::a]
    ...    //*[contains(@class,'test-id__field-label')][normalize-space()='${field_label}']/following::*[contains(@class,'test-id__field-value')][1]
    ${found}=    Set Variable    ${FALSE}
    FOR    ${loc}    IN    @{candidates}
        ${ok}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${loc}    timeout=4s
        IF    ${ok}
            ${actual}=    Get Text    ${loc}
            ${actual}=    Strip String    ${actual}
            Should Be Equal As Strings    ${actual}    ${expected_value}
            ...    msg=Field "${field_label}" expected "${expected_value}" but got "${actual}"
            ${found}=    Set Variable    ${TRUE}
            Exit For Loop
        END
    END
    IF    not ${found}
        Fail    Could not locate "${field_label}" output cell on the detail page.
    END

# Use Modal
#    [Arguments]    ${state}
#    IF    '${state}' == 'on'
#    ${dialog}=    set variable    ${dialogLocator}
#    ELSE IF    '${state}' == 'off'
#    ${dialog}=    set variable    ${EMPTY}
#    END
#    Set Test Variable    ${dialog}    ${dialog}
#    ${dialogAction}=    replace variables    ${dialogAction}
#    Set Test Variable    ${dialogAction}    ${dialogAction}
#    ${searchInputFieldLocator}=    replace variables    ${searchInputFieldLocator}
#    Set Test Variable    ${searchInputFieldLocator}    ${searchInputFieldLocator}
#    ${searchSuggestionTermLocator}=    replace variables    ${searchSuggestionTermLocator}
#    ${dropdownLocator}=    replace variables    ${dropdownLocator}
#    ${dropdownOptionsLocator}=    replace variables    ${dropdownOptionsLocator}
#    ${inputFieldLocator}=    replace variables    ${inputFieldLocator}
#    ${dateFieldLocator}=    replace variables    ${dateFieldLocator}
#    ${timeFieldLocator}=    replace variables    ${timeFieldLocator}
#    ${checkboxLocator}=    replace variables    ${checkboxLocator}
