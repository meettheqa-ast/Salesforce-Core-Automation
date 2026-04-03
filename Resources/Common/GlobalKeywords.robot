*** Settings ***
Library     SeleniumLibrary
Library     String
Library     Dialogs
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
    [Documentation]    The Begin Web Test setup initializes the testing environment by opening a blank page in the Chrome browser, maximizing the browser window for better visibility, and configuring Selenium with a 10-second timeout and implicit wait to ensure proper handling of element loading and interactions. Pass variable headless=true (e.g. robot -v headless:true) to run Chrome in headless mode for CI or background runs.
    [Tags]    setup
    ${headless_raw}=    Get Variable Value    ${headless}    false
    ${headless_lc}=    Convert To Lower Case    ${headless_raw}
    ${headless_lc}=    Strip String    ${headless_lc}
    IF    '${headless_lc}' == 'true'
        Open Browser    about:blank    chrome    options=add_argument("--headless=new");add_argument("--disable-gpu");add_argument("--window-size=1920,1080")
        Set Window Size    1920    1080
    ELSE
        Open Browser    about:blank    chrome
        Maximize Browser Window
    END
#    set window position    x=0    y=0
#    set window size    width=1265    height=675
    Set Selenium Timeout    10s
    Set Selenium Implicit Wait    10s

End Web Test
    [Documentation]    The End Web Test step concludes the testing session by closing all browser instances, ensuring proper cleanup of the testing environment.
    [Tags]    teardown
    Close All Browsers

Login To Sandbox
    [Documentation]    Logs into the sandbox. If the org enforces MFA/OTP, a password-only flow never reaches the Lightning header until verification finishes. Set suite variable MFA_PAUSE_FOR_MANUAL_COMPLETION to ${TRUE} (Streamlit Watch mode does this automatically) to show a dialog: complete OTP in the browser, then click OK; the keyword then waits up to 10 minutes for the app shell. Headless/CI runs keep MFA_PAUSE false and fail fast if MFA is required—use Trusted IP, a policy exception, or a non-MFA test user instead.
    [Tags]    login
    [Arguments]    ${instanceURL}    ${instanceUsername}    ${instancePassword}    ${allow_mfa_manual_pause}=${MFA_PAUSE_FOR_MANUAL_COMPLETION}
    Go To    ${instanceURL}
    Input Text    ${sandboxUserName}    ${instanceUsername}
    Input Text    ${sandboxPassword}    ${instancePassword}
    Click Element    ${sandboxLoginButton}
    ${ok}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${sandboxLaunch360Logo}    30s
    IF    not ${ok} and ${allow_mfa_manual_pause}
        Pause Execution    Complete MFA / OTP in the browser window, then click OK here to continue the test.
        Wait Until Element Is Visible    ${sandboxLaunch360Logo}    10 minutes
    ELSE IF    not ${ok}
        Fail    Login did not reach Salesforce (often MFA/OTP still pending or wrong credentials). Options: (1) Run in Watch mode so the app can pause for manual OTP. (2) Ask your admin for a Trusted IP range for your network so MFA is not prompted. (3) Use a sandbox integration user exempt from MFA if policy allows. (4) For TOTP secrets, extend automation to submit the verification code—see your security team.
    END

Launch App
    [Documentation]    Opens an app from the App Launcher. Uses exact title match when possible. If the menu item text does not exactly match (typos, "Mark Anthony" vs "Mark Anthony Group"), still types the given name into search and clicks the **first** visible result—Salesforce search is usually fuzzy, so this tolerates approximate names from prompts.
    [Tags]    navigation
    [Arguments]    ${appName}
    ${activeApp}=    Replace String    ${activeAppLocator}    <app-name>    ${appName}
    ${booleanStatus}=    Run Keyword And Return Status    Element Should Be Visible    ${activeApp}
    IF  not ${booleanStatus}
        Click Element    ${appLauncher}
        Wait Until Element Is Visible    ${searchAppLauncher}
        Clear Element Text    ${searchAppLauncher}
        Input Text    ${searchAppLauncher}    ${appName}
        Sleep    0.5s
        ${appInLauncher}=    Replace String    ${appInLauncherLocator}    <app-name>    ${appName}
        ${exactHit}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${appInLauncher}    5s
        IF    ${exactHit}
            Click Element    ${appInLauncher}
        ELSE
            ${firstHit}=    Set Variable    xpath:(//one-app-launcher-menu-item)[1]
            Wait Until Element Is Visible    ${firstHit}    15s
            Click Element    ${firstHit}
        END
        Wait Until Element Is Visible    ${sandboxlaunch360logo}
        ${verified}=    Run Keyword And Return Status    Page Should Contain Element    ${activeApp}
        IF    not ${verified}
            Log    Opened first App Launcher search result for "${appName}"; active header may not match the string exactly (typos / alternate app title).    WARN
        END
    END
    Sleep    3s

Select App Tab
    [Documentation]    Selects a specific tab within an app that includes dropdown options. It dynamically locates the desired tab by its name, waits for it to become visible, and then clicks on it. After selecting the tab, it verifies that the tab is active by checking for the presence of the corresponding element on the page.
    [Tags]    navigation
    [Arguments]    ${tabName}
    ${tabInApp}=    Replace String    ${tabInAppLocator}    <tab-name>    ${tabName}
    Wait Until Element Is Visible    ${tabInApp}
    Click Element    ${tabInApp}
    ${activeTab}=    Replace String    ${activeTabLocator}    <tab-name>    ${tabName}
    Wait Until Page Contains Element    ${activeTab}
    Page Should Contain Element    ${activeTab}

Open New Dialog
    [Documentation]    Clicks **New** then the dialog title row. Waits out list spinners, tries to clear a blocking ``forceChangeRecordType`` overlay (ESC + wait), scrolls New into view, then uses a normal click with **JavaScript click** fallback when another layer intercepts the pointer (ElementClickInterceptedException).
    [Tags]    modal    navigation
    [Arguments]    ${dialogName}
    Wait Until Element Is Visible    ${newRecord}    timeout=20s
    Run Keyword And Ignore Error    Wait Until Element Is Not Visible    ${listViewSearchSpinner}    timeout=15s
    Run Keyword And Ignore Error    Press Keys    xpath://body    ESCAPE
    Sleep    0.3s
    ${overlay}=    Run Keyword And Return Status    Element Should Be Visible    ${sfRecordTypeOverlay}    2s
    IF    ${overlay}
        Run Keyword And Ignore Error    Press Keys    xpath://body    ESCAPE
        Sleep    0.5s
        Run Keyword And Ignore Error    Wait Until Element Is Not Visible    ${sfRecordTypeOverlay}    timeout=10s
    END
    Scroll Element Into View With Fallback    ${newRecord}
    Sleep    0.5s
    ${clicked}=    Run Keyword And Return Status    Click Element    ${newRecord}
    IF    not ${clicked}
        ${nr}=    Get Webelement    ${newRecord}
        Execute Javascript    arguments[0].scrollIntoView({block:'center'}); arguments[0].click();    ARGUMENTS    ${nr}
    END
    ${newRecordDialogTitle}=    Replace String    ${newRecordDialogTitleLocator}    <record-name>    ${dialogName}
    Wait Until Element Is Visible    ${newRecordDialogTitle}    timeout=20s
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
    Wait Until Element Is Visible    ${itemInLauncher}
    Click Element    ${itemInLauncher}
    Wait Until Element Is Visible    ${sandboxlaunch360logo}
    Sleep    2s

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
    Wait Until Page Contains Element    ${searchInputField}
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
        Sleep    2s
        ${searchSuggestionTerm}=    Get Webelement    ${searchSuggestionTerm}
        Click Element    ${searchSuggestionTerm}
    END

Select Dialog Button
    [Documentation]    Selects a button within a Dialog. Eg. Save, Cancel, Save & New
    [Tags]    modal    interaction
    [Arguments]    ${buttonActionArg}
    ${buttonAction}=    Replace String    ${dialogAction}    <btn-action>    ${buttonActionArg}
    Wait Until Element Is Visible    ${buttonAction}
    Click Button    ${buttonAction}

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
    [Documentation]    Opens up the dropdown field. It first identifies the dropdown field using the provided argument, waits for it to become visible, and scrolls it into view. After a short pause, it uses JavaScript to simulate a click event on the dropdown field, thereby opening it for further interactions.
    [Tags]    interaction    pick list
    [Arguments]    ${dropdownFieldArg}
    ${dropdownField}=    Replace String    ${dropdownDialogLocator}    <dropdown-field>    ${dropdownFieldArg}
    Wait Until Element Is Visible    ${dropdownField}
    Scroll Element Into View With Fallback    ${dropdownField}
    Sleep    1s
    ${dropdownFieldJS}=    Get Webelement    ${dropdownField}
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${dropdownFieldJS}

Select Dropdown Option
    [Documentation]    Selects the option present in the expanded dropdown that was opened using the Open Dropdown keyword. It identifies the specified dropdown field and option, waits for the option to become visible, scrolls it into view, and clicks on it to make the selection.
    [Tags]    interaction    pick list
    [Arguments]    ${dropdownNameArg}    ${dropdownOptionArg}
    ${dropdownOptionsDialogLocator}=    Replace String
    ...    ${dropdownOptionsDialogLocator}
    ...    <dropdown-field>
    ...    ${dropdownNameArg}
    ${dropdownOption}=    Replace String    ${dropdownOptionsDialogLocator}    <dropdown-value>    ${dropdownOptionArg}
    Wait Until Element Is Visible    ${dropdownOption}
    Scroll Element Into View With Fallback    ${dropdownOption}
    Click Element    ${dropdownOption}

Select First Lightning Dropdown Option In Modal
    [Documentation]    After ``Open Dropdown``, picks the first real ``lightning-base-combobox-item`` in the modal (skips empty data-value). Use when the exact option label is unknown.
    [Tags]    interaction    pick list
    ${firstOpt}=    Set Variable
    ...    xpath:(//*[contains(@class,'modal-container')]//lightning-base-combobox-item[@role='option' and string-length(@data-value) > 0])[1]
    Wait Until Element Is Visible    ${firstOpt}    15s
    ${el}=    Get Webelement    ${firstOpt}
    Execute Javascript    arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();    ARGUMENTS    ${el}

Enter Text
    [Documentation]    Use this keyword to enter a value into the Input Field. It first identifies the input field using the provided label name, waits for the field to become visible, and scrolls it into view. It then clicks on the input field using JavaScript to ensure it is focused, and finally enters the specified text value into the field.
    [Tags]    interaction    input field
    [Arguments]    ${labelName}    ${textValue}
    ${inputField}=    Replace String    ${inputFieldDialogLocator}    <field-name>    ${labelName}
    Wait Until Element Is Visible    ${inputField}
    Scroll Element Into View With Fallback    ${inputField}
    ${inputTextFieldJS}=    Get Webelement    ${inputField}
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${inputTextFieldJS}
    Input Text    ${inputField}    ${textValue}

Enter Date
    [Documentation]    Enters a date value into a date input field. It first identifies the date field using the provided field name, waits for the field to become visible, and scrolls it into view. It then enters the specified date value into the field.
    [Tags]    interaction    date
    [Arguments]    ${dateNameArg}    ${dateValue}
    ${dateField}=    Replace String    ${dateFieldDialogLocator}    <date-field-name>    ${dateNameArg}
    Wait Until Element Is Visible    ${dateField}
    Scroll Element Into View    ${dateField}
    Input Text    ${dateField}    ${dateValue}

Enter Time
    [Documentation]    Enters a time value into a time input field. It first identifies the time field using the provided field name, waits for the field to become visible, and scrolls it into view. The keyword clears any existing value by selecting the text and using the BACKSPACE key before entering the specified time value into the field.
    [Tags]    interaction    time
    [Arguments]    ${timeNameArg}    ${timeValue}
    ${timeField}=    Replace String    ${timeFieldDialogLocator}    <time-field-name>    ${timeNameArg}
    Wait Until Element Is Visible    ${timeField}
    Scroll Element Into View    ${timeField}
    Press Keys    ${timeField}    CTRL+a    BACKSPACE
    Input Text    ${timeField}    ${timeValue}
    Press Key    ${timeField}    \\27

Click Checkbox
    [Documentation]    Check or uncheck a Salesforce checkbox. It identifies the checkbox element using the provided checkbox name, waits for the checkbox to become visible, scrolls it into view, and then clicks the checkbox to select or deselect it.
    [Tags]    interaction    checkbox
    [Arguments]    ${checkboxNameArg}
    ${checkboxField}=    Replace String    ${checkboxDialogLocator}    <checkbox-field>    ${checkboxNameArg}
    Wait Until Page Contains Element    ${checkboxField}
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
    Wait Until Element Is Visible    ${successtoastmessagelocator}
    Wait Until Element Is Not Visible    ${successtoastmessagelocator}

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
    Wait Until Element Is Visible    ${successToastMessageLocator}
    Wait Until Element Is Not Visible    ${successToastMessageLocator}

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
    [Documentation]    Verifies the creation of a record by checking if the specified data is correctly displayed in the appropriate field. It first replaces spaces in the provided record type and field names with empty strings. Then it constructs the locator for the record field, incorporating the field name and the expected data. After waiting for the field to be visible on the page, it scrolls to the field and checks that the data matches the expected value. Use 'Checkbox-Check' and 'Checkbox-Uncheck' for verifying checkboxes.
    [Tags]    records    verification
    [Arguments]    ${recordDataTypeArg}    ${recordDataFieldArg}    ${recordDataArg}
    ${recordDataTypeArg}=    Replace String    ${recordDataTypeArg}    ${SPACE}    ${EMPTY}
    ${recordDataType}=    Replace String    ${recordDataLocator}    <record-type>    ${recordDataTypeArg}
    ${recordDataFieldArg}=    Replace String    ${recordDataFieldArg}    ${SPACE}    ${EMPTY}
    ${recordDataField}=    Replace String    ${recordDataType}    <field-name>    ${recordDataFieldArg}
    ${recordActualDataLocator}=    Replace String    ${recordDataField}    <actual-data>    ${recordDataArg}
    IF    '${recordDataArg}' == 'Checkbox-Check'
        ${recordActualDataLocator}=    Set Variable    ${recordActualDataLocator}\[@checked]
    ELSE IF    '${recordDataArg}' == 'Checkbox-Uncheck'
        ${recordActualDataLocator}=    Set Variable    ${recordActualDataLocator}\[not(@checked)]
    END
    Wait Until Page Contains Element    ${recordActualDataLocator}
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
    Wait Until Element Is Visible    ${relatedRecordDropdownName}
    Scroll Element Into View    ${relatedRecordDropdownName}
    Sleep    2s
    ${relatedRecordDropdown}=    Replace String
    ...    ${relatedRecordDropdownLocator}
    ...    <record-type>
    ...    ${relatedRecordDropdownArg}
    Wait Until Element Is Visible    ${relatedRecordDropdown}
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
    Wait Until Element Is Visible    ${dialogLocator}

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
    Wait Until Element Is Visible    ${relatedRecordsViewAllLocator}
    ${relatedRecordsViewAllLocatorJS}=    Get Webelement    ${relatedRecordsViewAllLocator}
    Execute Javascript    arguments[0].click();    ARGUMENTS    ${relatedRecordsViewAllLocatorJS}
    ${realtedRecordListViewTitleLocator}=    Replace String
    ...    ${realtedRecordListViewTitleLocator}
    ...    <record-type>
    ...    ${relatedRecordNameArg}
    Wait Until Element Is Visible    ${realtedRecordListViewTitleLocator}
    Verify Table Cell Record    ${successToastMessageOnRecordDetailsPage}

Verify Table Cell Record
    [Documentation]    Verify the presence of a record ID in a table cell on a page. It checks if the specified record ID appears within a table. This keyword is useful when verifying the presence of newly created records or ensuring that a record exists in a table based on its ID.
    [Tags]    verification    records    utilities
    [Arguments]    ${recordIdArg}    ${recordIdPos}=1
    ${tableCellLocator}=    Replace String    ${tableCellLocator}    <record-id>    ${recordIdArg}
    ${tableCellLocator}=    Replace String    ${tableCellLocator}    <pos>    ${recordIdPos}
    Wait Until Page Contains Element    ${tableCellLocator}
    Page Should Contain Element    ${tableCellLocator}

Return Back To Parent
    [Documentation]    Navigates back to the parent record from a related record view. It reloads the page, waits for the breadcrumb (indicating the parent record) to become visible, and clicks on it to return to the parent record's details page. After navigating back, it ensures the parent record is visible and confirms successful navigation.
    [Tags]    navigation    back
    Reload Page
    Wait Until Element Is Visible    ${relatedRecordParentBreadcrumbLocator}
    Click Element    ${relatedRecordParentBreadcrumbLocator}
    Wait Until Element Is Visible    ${entityNameLocator}
    Element Should Be Visible    ${entityNameLocator}
#    wait until page does not contain element    ${spinnerLoadingWOLocator}    timeout=20s

Return To Previous Page
    [Documentation]    Navigates back to the previous page in the browser history. It simulates the "Back" action, typically used to return to the previous screen or page from the current one.
    [Tags]    navigation    back
    Go Back

Convert View From Intelligent To List
    [Documentation]    Switches to List view when Salesforce shows Intelligence/List toggles. If **no** toggle appears (already in List view, split view, or org-specific layout), exits without failing—this is the usual fix for "List View button not found". Otherwise clicks List View using exact then flexible locators.
    [Tags]    utilities    view
    Sleep    1s
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
            Sleep    1s
            RETURN
        END
    END
    IF    ${listFlex}
        Scroll Element Into View With Fallback    ${listViewButtonFlexible}
        ${ok2}=    Run Keyword And Return Status    Click Element    ${listViewButtonFlexible}
        IF    ${ok2}
            Sleep    1s
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
        Wait Until Element Is Visible    ${recordTypeDropdownLocator}
        Click Element    ${recordTypeDropdownLocator}
        Wait Until Element Is Visible    ${recordType}
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
    Wait Until Element Is Visible    ${accountRecordType}
    Scroll Element Into View With Fallback    ${accountRecordType}
    Click Element    ${accountRecordType}
    Select Dialog Button    Next
    ${newRecordDialogTitle}=    Replace String    ${newRecordDialogTitleLocator}    <record-name>    Account
    Wait Until Element Is Visible    ${newRecordDialogTitle}

Visit Dynamic Form Section
    [Documentation]    Scrolls to a dynamic form section by section title. Targets the section ``h3`` (not the inner span) and uses a JS scroll fallback so SLDS does not throw "element has no size and location".
    [Tags]    navigation    records
    [Arguments]    ${dynamicFormInformationSectionArg}
    ${dynamicFormInformationSection}=    Replace String
    ...    ${dynamicFormInformationSectionLocator}
    ...    <title-name>
    ...    ${dynamicFormInformationSectionArg}
    Wait Until Page Contains Element    ${dynamicFormInformationSection}
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
    Wait Until Element Is Visible    ${listViewRecordType}
    Click Element    ${listViewRecordType}
    Wait Until Element Is Visible    ${listViewDropdownOption}
    Click Element    ${listViewDropdownOption}
    Wait Until Element Is Not Visible    ${listViewSearchSpinner}

Search In List View
    [Documentation]    Searches for a specific record in the list view. Inputs the record ID into the search box, performs the search, and verifies the presence of the record in the resulting list.
    [Tags]    search in list view    interaction
    [Arguments]    ${recordIdArg}
    sleep    1s
    ${listViewSearchInput}=    Replace String    ${searchInputFieldLocator}    <search-input-field>    this list
    Wait Until Element Is Visible    ${listViewSearchInput}
    Input Text    ${listViewSearchInput}    ${recordIdArg}
    Press Key    ${listViewSearchInput}    \\13    # ASCII code for enter key
    Wait Until Element Is Not Visible    ${listViewSearchSpinner}
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
    Wait Until Page Contains Element    ${fieldName}
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
    Wait Until Element Is Visible    ${statusOption}
    Scroll Element Into View    ${statusOption}
    Mouse Down    ${statusOption}
    Mouse Up    ${statusOption}
    Wait Until Element Is Visible    ${submitPathStep}
    Mouse Down    ${submitPathStep}
    Mouse Up    ${submitPathStep}
    IF    '${statusOptionArg}' != 'Closed' and '${statusStage}' == 'None'
        Wait Until Element Is Visible    ${successToastMessageLocator}
        Wait Until Element Is Not Visible    ${successToastMessageLocator}
        Wait Until Element Is Visible    ${activeStatusOption}
        Element Should Be Visible    ${activeStatusOption}
    ELSE IF    '${statusOptionArg}' == 'Closed' and '${statusStage}' != 'None'
        Select From List By Value    ${closeStageSelectDialog}    ${statusStage}
        Select Dialog Button    Save
        Wait Until Element Is Visible    ${successToastMessageLocator}
        Wait Until Element Is Not Visible    ${successToastMessageLocator}
        Wait Until Element Is Visible    ${activeStatusOption}
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
    Wait Until Element Is Visible    ${reqFieldName}
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
    Wait Until Element Is Visible    ${reqSnagFieldName}
    Element Should Be Visible    ${reqSnagFieldName}

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
