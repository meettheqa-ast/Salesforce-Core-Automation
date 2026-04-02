*** Variables ***
${sandboxUserName}=                                     xpath://input[@id="username"]
${sandboxPassword}=                                     xpath://input[@id="password"]
${sandboxLoginButton}=                                  xpath://input[@id="Login"]
${sandboxLaunch360Logo}=                                xpath://div[@class='slds-global-header__item']//div[@class='slds-global-header__logo']

# App Launcher
${appLauncher}=                                         xpath://button[@title='App Launcher']
${searchAppLauncher}=                                   xpath://input[@placeholder='Search apps and items...']
${appInLauncherLocator}=                                xpath://one-app-launcher-menu-item[. = '<app-name>']
${itemInLauncherLocator}=                               xpath://one-app-launcher-menu-item[. = '<item-name>']
${activeAppLocator}=                                    xpath://span[@title='<app-name>']

# Tab of App Launcher
${tabInAppLocator}=                                     xpath://one-app-nav-bar-item-root[a[@title='<tab-name>']]
# ${tabInAppLocator}=    xpath://one-app-nav-bar-item-root[@data-id="<tab-name>"]

# The last or part for this locator is used to handle the intelligent view breadcrumbs
${activeTabLocator}=                                    xpath://lst-breadcrumbs//span[text()='<tab-name>'] | //lst-breadcrumbs//h1[text()='<tab-name>'] | //*[@class='slds-page-header__name-meta'][contains(text(), '<tab-name>')]

# New Record Dialog
${newRecord}=                                           xpath://a[@role='button']//div[@title='New']
# ${newRecordDialogTitleLocator}=    xpath://h2[normalize-space()='New <record-name>']
${newRecordDialogTitleLocator}=                         xpath://h2[starts-with(normalize-space(), 'New ') and contains(normalize-space(), '<record-name>')]

# Field Locators for Dialog
# ${dialogAction}=    xpath://*[contains(@class,'modal-container')]//button[@title='<btn-action>' or text()='<btn-action>']
${dialogAction}=                                        xpath://*[contains(@class,'modal-container')]//button[(normalize-space(text())='<btn-action>' or .//*[normalize-space(text())='<btn-action>']) or (@title='<btn-action>' or normalize-space(text())='<btn-action>')]

# Search Input Field Dialog
${searchInputFieldDialogLocator}=                       xpath://*[contains(@class,'modal-container')]//input[@placeholder='Search <search-input-field>...' or @placeholder='Search <search-input-field>']
${searchSuggestionTermDialogLocator}=                   xpath:(//*[contains(@class,'modal-container')]//input[@aria-expanded='true']/ancestor::div[3]/following-sibling::div//lightning-base-combobox-formatted-text[@title='<search-term>'])[<pos>]|(//*[contains(@class,'modal-container')]//div[contains(@class, 'uiInput--lookup') and not(contains(@class, 'invisible'))]//div[contains(@class, 'primaryLabel') and @title='<search-term>'])[<pos>]
# Dropdown Locator. Pass State Province Abrreviation for Shipping and Billing Address
${dropdownDialogLocator}=                               xpath://*[contains(@class,'modal-container')]//div[contains(@class, 'uiInputSelect') and .//span[contains(text(), '<dropdown-field>')]]//a | //*[contains(@class,'modal-container')]//button[@aria-label='<dropdown-field>'] | //*[contains(@class,'modal-container')]//input[@aria-label='<dropdown-field>']
${dropdownOptionsDialogLocator}=                        xpath:(//div[contains(@class, 'select-options') and contains(@class, 'visible')]//a[@title='<dropdown-value>']) | (//*[contains(@class,'modal-container')]//button[@aria-expanded='true']/ancestor::div[1]/following-sibling::div[@aria-label='<dropdown-field>']//lightning-base-combobox-item[@data-value='<dropdown-value>']) | (//*[contains(@class,'modal-container')]//input[@aria-expanded='true']/ancestor::div[3]/following-sibling::div[@aria-label='<dropdown-field>']//lightning-base-combobox-item[@data-value='<dropdown-value>'])
# Normal Input Field
${inputFieldDialogLocator}=                             xpath://*[contains(@class,'modal-container')]//label[.//text()[normalize-space()='<field-name>'] or normalize-space()='<field-name>']//following::*[(self::input or self::textarea)][1]
# Date-Times Fields
${dateFieldDialogLocator}=                              xpath:(//*[contains(@class,'modal-container')]//legend[normalize-space(.)='<date-field-name>']/following-sibling::*//input)[1] | (//*[contains(@class,'modal-container')]//lightning-datepicker[.//text()[normalize-space(.)='<date-field-name>']]//input) | //*[contains(@class,'modal-container')]//label[.//text()[normalize-space(.)='<date-field-name>']]/following-sibling::*//input | //*[contains(@class,'modal-container')]//label[.//text()[normalize-space(.)='<date-field-name>']]/following-sibling::input
${timeFieldDialogLocator}=                              xpath:(//*[contains(@class,'modal-container')]//legend[normalize-space(.)='<time-field-name>']/following-sibling::*//input)[2]
# Checkbox
${checkboxDialogLocator}=                               xpath:(//*[contains(@class,'modal-container')]//label[.//text()[normalize-space(.)='<checkbox-field>']]/following-sibling::*//input[@type='checkbox']) | (//*[contains(@class,'modal-container')]//label[.//text()[normalize-space(.)='<checkbox-field>']]/following-sibling::input[@type='checkbox'])

# Lead Convert Dialog Create New Field Values
${leadConvertFieldDialogLocator}=                       xpath://*[contains(@class,'modal-container')]//fieldset[legend[text()="<field-name>"]]//button

# Locators to select option value in Change Opportunity Dialog to Closed Won or Closed Lost
${closeStageSelectDialog}=                              xpath://*[contains(@class,'modal-container')]//select[contains(@class, 'stepAction')]

# Locators for Required Field on Dialog
${dialogFieldRequired}=                                 //*[contains(@class,"modal-container")]//span[@class="slds-assistive-text" and text()="<field-name>"]/parent::div

# General Locators
# Search Input Field General
${searchInputFieldLocator}=                             xpath://input[@placeholder='Search <search-input-field>...' or @placeholder='Search <search-input-field>']
${searchSuggestionTermLocator}=                         xpath:(//input[@aria-expanded='true']/ancestor::div[3]/following-sibling::div//lightning-base-combobox-formatted-text[@title='<search-term>'])[<pos>]|(//div[contains(@class, 'uiInput--lookup') and not(contains(@class, 'invisible'))]//div[contains(@class, 'primaryLabel') and @title='<search-term>'])[<pos>]
# Dropdown Locator. Pass State Province Abrreviation for Shipping and Billing Address General
${dropdownLocator}=                                     xpath://div[contains(@class, 'uiInputSelect') and .//span[contains(text(), '<dropdown-field>')]]//a | //button[@aria-label='<dropdown-field>'] | //input[@aria-label='<dropdown-field>']
${dropdownOptionsLocator}=                              xpath:(//div[contains(@class, 'select-options') and contains(@class, 'visible')]//a[@title='<dropdown-value>']) | (//button[@aria-expanded='true']/ancestor::div[1]/following-sibling::div[@aria-label='<dropdown-field>']//lightning-base-combobox-item[@data-value='<dropdown-value>']) | (//input[@aria-expanded='true']/ancestor::div[3]/following-sibling::div[@aria-label='<dropdown-field>']//lightning-base-combobox-item[@data-value='<dropdown-value>'])
# Normal Input Field General
${inputFieldLocator}=                                   xpath://label[.//text()[normalize-space()='<field-name>'] or normalize-space()='<field-name>']//following::*[(self::input or self::textarea)][1]
# Date-Times Fields General
${dateFieldLocator}=                                    xpath:(//legend[normalize-space(.)='<date-field-name>']/following-sibling::*//input)[1] | (//lightning-datepicker[.//text()[normalize-space(.)='<date-field-name>']]//input) | //label[.//text()[normalize-space(.)='<date-field-name>']]/following-sibling::*//input | //label[.//text()[normalize-space(.)='<date-field-name>']]/following-sibling::input
${timeFieldLocator}=                                    xpath:(//legend[normalize-space(.)='<time-field-name>']/following-sibling::*//input)[2]
# Checkbox General
${checkboxLocator}=                                     xpath:(//label[.//text()[normalize-space(.)='<checkbox-field>']]/following-sibling::*//input[@type='checkbox']) | (//label[.//text()[normalize-space(.)='<checkbox-field>']]/following-sibling::input[@type='checkbox'])

${entityNameLocator}=                                   xpath://div[@class="slds-media__body"]//*[text()="<entity-name>"]

# use to delete currently opened record on record details page
${actionRecordTypeLocator}=                             xpath://*[@data-target-selection-name='sfdc:StandardButton.<record-type>.<record-action>']
${headerQuickActionDropdownLocator}=                    xpath:(//*[@data-target-reveals[contains(., '<record-type>')]])[1]

# Record Details Page
${recordDataLocator}=                                   xpath://*[@data-target-selection-name='sfdc:RecordField.<record-type>.<field-name>']//*[contains(text(),'<actual-data>')] | //*[@data-target-selection-name='sfdc:RecordField.<record-type>.<field-name>Id']//*[contains(text(),'<actual-data>')] | //*[@data-target-selection-name='sfdc:RecordField.<record-type>.<field-name>']//lightning-primitive-input-checkbox

# Record Details Page Left Side bar
${relatedRecordDropdownNameLocator}=                    xpath://article[@aria-label='<record-type>']
${relatedRecordDropdownLocator}=                        xpath://article[@aria-label='<record-type>']//a[@role='button']
${relatedRecordDropdownOptionLocator}=                  xpath://div[contains(@class, 'actionMenu') and (contains(@class, 'visible'))]//a[@title='<dropdown-option>']

${dialogLocator}=                                       xpath://*[contains(@class,'modal-container')]
${successToastMessageOnRecordDetailsPageLocator}=       xpath://div[@data-key='success']//a//div
${relatedRecordsViewAllLocator}=                        xpath://article[@aria-label='<record-type>']//span[@class='view-all-label']
${realtedRecordListViewTitleLocator}=                   xpath://h1[@title='<record-type>']
${tableCellLocator}=                                    xpath:(//a[starts-with(@href, '/lightning/r/') and contains(@href, '/view')][.//text()='<record-id>'])[<pos>]
${successToastMessageLocator}=                          xpath://div[@data-key='success']

${relatedRecordParentBreadcrumbLocator}=                xpath://nav[@role='navigation' and @aria-label='Breadcrumbs']//li[2]

${spinnerLoadingWOLocator}=                             xpath://lightning-spinner

# List view button locator used for converting intelligent view to list view
${listViewButton}=                                      xpath://button[normalize-space()='List View']
${intelligentListButton}=                               xpath://div[@title='Intelligence View']

# Select Record Type In Account Dialog
${accountRecordTypeLocator}=                            xpath://*[contains(@class,'modal-container')]//div[@class='changeRecordTypeOptionLeftColumn']/following-sibling::div//*[normalize-space(text())='<account-record-type>']

# Dynamic Form Section Title Locator
${dynamicFormInformationSectionLocator}=                xpath://h3[contains(@class, 'slds-section__title')]//span[text()='<title-name>']

# Locator for Empty Message Container that appears when no record is found through search.
${emptyContainerListViewLocator}=                       xpath://div[contains(@class,'emptyContent')]
${listViewDropdownLocator}=                             xpath://button[@title="Select a List View: <record-type>"]
${listViewDropdownOptionLocator}=                       xpath://div[@role="dialog" and @aria-hidden="false"]//li/a/span[text()="<dropdown-value>"]
${listViewSearchSpinner}=                               xpath://div[@class='slds-spinner_container slds-grid']

# Locators to change path option
${pathOption}=                                          xpath://ul[@class="slds-path__nav"]//li[@data-name="<path-option>"]/a
${activePathOption}=                                    xpath://ul[@class="slds-path__nav"]//li[@data-name="<path-option>"]//a[@aria-selected="true"]
${submitPathStep}=                                      xpath://button[contains(@class, 'slds-button') and contains(@class, 'slds-button--brand') and contains(@class, 'slds-path__mark-complete') and contains(@class, 'stepAction')]

# Locators for Required Field on Records Details Page
${fieldRequired}=                                       //span[@class="slds-assistive-text" and text()="<field-name>"]/parent::div

# Locator for Required Field present in Snag message (applicable for dialog and record page)
${snagFieldRequired}=                                   xpath://div[@class="fieldLevelErrors"]//ul/li/a[text()="<snag-field-name>"]

# Dont Touch This
# ${dialog}=    ${EMPTY}
# ${dialogLocator}=    //*[contains(@class,'modal-container')]
# ${dialogAction}=    xpath:\${dialog}//button[@title='<btn-action>' or text()='<btn-action>']
#
## Search Input Field
# ${searchInputFieldLocator}=    xpath:\${dialog}//input[@placeholder='Search <search-input-field>...' or @placeholder='Search <search-input-field>']
# ${searchSuggestionTermLocator}=    xpath:(\${dialog}//input[@aria-expanded='true']/ancestor::div[3]/following-sibling::div//lightning-base-combobox-formatted-text[@title='<search-term>'])[<pos>]|(\${dialog}//div[contains(@class, 'uiInput--lookup') and not(contains(@class, 'invisible'))]//div[contains(@class, 'primaryLabel') and @title='<search-term>'])[<pos>]
#
##Dropdown Locator. Pass State Province Abrreviation for Shipping and Billing Address
# ${dropdownLocator}=    xpath:\${dialog}//div[contains(@class, 'uiInputSelect') and .//span[contains(text(), '<dropdown-field>')]]//a | \${dialog}//button[@aria-label='<dropdown-field>'] | \${dialog}//input[@aria-label='<dropdown-field>']
# ${dropdownOptionsLocator}=    xpath:(//div[contains(@class, 'select-options') and contains(@class, 'visible')]//a[@title='<dropdown-value>']) | (\${dialog}//button[@aria-expanded='true']/ancestor::div[1]/following-sibling::div[@aria-label='<dropdown-field>']//lightning-base-combobox-item[@data-value='<dropdown-value>']) | (\${dialog}//input[@aria-expanded='true']/ancestor::div[3]/following-sibling::div[@aria-label='<dropdown-field>']//lightning-base-combobox-item[@data-value='<dropdown-value>'])
#
##Normal Input Field
# ${inputFieldLocator}=    xpath:\${dialog}//label[.//text()[normalize-space()='<field-name>'] or normalize-space()='<field-name>']//following::*[(self::input or self::textarea)][1]
#
##Date-Times Fields
# ${dateFieldLocator}=    xpath:(\${dialog}//legend[normalize-space(.)='<date-field-name>']/following-sibling::*//input)[1] | (\${dialog}//lightning-datepicker[.//text()[normalize-space(.)='<date-field-name>']]//input) | \${dialog}//label[.//text()[normalize-space(.)='<date-field-name>']]/following-sibling::*//input | \${dialog}//label[.//text()[normalize-space(.)='<date-field-name>']]/following-sibling::input
# ${timeFieldLocator}=    xpath:(\${dialog}//legend[normalize-space(.)='<time-field-name>']/following-sibling::*//input)[2]
#
##Checkbox
# ${checkboxLocator}=    xpath:(\${dialog}//label[.//text()[normalize-space(.)='<checkbox-field>']]/following-sibling::*//input[@type='checkbox']) | (\${dialog}//label[.//text()[normalize-space(.)='<checkbox-field>']]/following-sibling::input[@type='checkbox'])
