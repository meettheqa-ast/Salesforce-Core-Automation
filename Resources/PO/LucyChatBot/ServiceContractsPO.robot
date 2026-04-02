*** Settings ***
Resource    ../../Common/LucyChatBot/LucyChatBotCommon.robot


*** Variables ***
# Service Contracts Sub Main Menu
${getMyServiceContractsDetailsOption}                       xpath://button[@title="Get My Service Contracts' Details"]
${howCanICreateANewServiceContractOption}=                  xpath://button[@title="How can I create a new service contract?"]
${howCanIRenewMyServiceContractOption}=                     xpath://button[@title="How can I renew my service contract?"]

# Get My Service Contracts Submenu
${whatAreMyActiveServiceContractsOption}=                   xpath://button[@title='What are my active service contracts?']
${whatIsTheExpirationDateForMyServiceContractOption}=       xpath://button[@title='What is the expiration date for my service contract?']
${whatProductsAreCoveredUnderMyServiceContractOption}=      xpath://button[@title='What products are covered under my service contract?']
${whatTypeOfSupportAmIEntitledToOption}=                    xpath://button[@title='What type of support am I entitled to?']

${doYouHaveTheContractNumber}=                              xpath://span[contains(text(),'Do you have the contract number?')]
${pleaseEnterYourContractNumber} =                          xpath://embeddedmessaging-conversation-entry-item//span[contains(text(),'Please enter your contract number.')]

# Service Contract Details Active or Not
${serviceContractDetailsMessage1}=                          This contract <serviceContractName> status is <serviceContractStatus>.
${serviceContractDetailsMessage2}=                          xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Service Contract Description, It is valid for the term of")]

# Service Contract Expiration
${serviceContractExpirationDetails}=                        xpath://embeddedmessaging-conversation-entry-item//span[contains(., "The expiration date for this service contract is")]
${pattern}=                                                 ^The expiration date for this service contract is (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \\d{1,2}, \\d{4} and the status of the service contract is <serviceContractStatus>$

# Service Contract Product Details
${serviceContractProductDetailsAssetName}=                  Asset Name: <serviceContractProductName>

# Service Contract Entitlement Details
${serviceContractEntitlementDetailsName}=                   The Asset associated with <serviceContractEntitlementName> entitlement is
${noEntitlementRecordFoundMessage}=                         xpath://embeddedmessaging-conversation-entry-item//span[contains(., "According to our records, currently this Entitlement is not linked with any Asset.")]

# Service Contract Selection
${serviceContractOption}=                                   xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Name') and contains(., 'Status')])[1]
${getServiceContractName}=                                  xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Name') and contains(., 'Status')])[1]//span
${finalServiceContractName}=                                ${EMPTY}
${finalServiceContractStatus}=                              ${EMPTY}

# Product Selection
${productNameOption}=                                       xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Product Name :')])[1]
${getProductName}=                                          xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Product Name :')])[1]//span
${noProductFoundMessage}=                                   xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Unfortunately, there are currently no contract line items added to this Service Contract.")]
${finalServiceContractProductName}=                         ${EMPTY}
${noProductFoundMessageFlag}=                               False

# Entitlement Selection
${entitlementNameOption}=                                   xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Entitlement Name :')])[1]
${getEntitlementName}=                                      xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Entitlement Name :')])[1]//span
${noEntitlementFoundMessage}=                               xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Unfortunately, the service contract does not contain any entitlements yet.")]
${finalServiceContractEntitlementName}=                     ${EMPTY}
${noEntitlementFoundMessageFlag}=                           False


*** Keywords ***
Select First Service Contract
    # Get the full text from the element
    ${fullServiceContractDetails}=    get text    ${getServiceContractName}

    # Get the length of the full text
    ${fullServiceContractDetailsLen}=    get length    ${fullServiceContractDetails}

    # Extract Name
    ${startName}=    Set Variable    ${fullServiceContractDetails.index('Name :') + len('Name :')}
    ${endName}=    Set Variable    ${fullServiceContractDetails.index(' Status')}
    ${subServiceContractName}=    get substring    ${fullServiceContractDetails}    ${startName}    ${endName}
    ${subServiceContractName}=    strip string    ${subServiceContractName}    # Remove spaces

    # Extract Status
    ${startStatus}=    Set Variable    ${fullServiceContractDetails.index('Status :') + len('Status :')}
    ${subServiceContractStatus}=    get substring
    ...    ${fullServiceContractDetails}
    ...    ${startStatus}
    ...    ${fullServiceContractDetailsLen}
    ${subServiceContractStatus}=    strip string    ${subServiceContractStatus}    # Remove spaces

#    log to console    ${subServiceContractName}
#    log to console    ${subServiceContractStatus}
    set test variable    ${finalServiceContractName}    ${subServiceContractName}
    set test variable    ${finalServiceContractStatus}    ${subServiceContractStatus}
    scroll element into view    ${serviceContractOption}
    click element    ${serviceContractOption}

Select First Product
    ${noProductFoundMessagePresent}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    ${noProductFoundMessage}
    ...    timeout=10s
    IF    ${noProductFoundMessagePresent}
        Set Test Variable    ${noProductFoundMessageFlag}    True
    ELSE
        Select Product Name Option
    END

Select Product Name Option
    ${fullProductName}=    get text    ${getProductName}
    ${startIndex}=    set variable    ${fullProductName.index('Product Name :') + len('Product Name :')}
    ${endIndex}=    set variable    ${fullProductName.index(' Start Date')}
    ${subProductName}=    get substring    ${fullProductName}    ${startIndex}    ${endIndex}
    ${subProductName}=    strip string    ${subProductName}
    set test variable    ${finalServiceContractProductName}    ${subProductName}
    scroll element into view    ${productNameOption}
    click element    ${productNameOption}

Select First Entitlement
    ${noEntitlementFoundMessagePresent}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    ${noEntitlementFoundMessage}
    ...    timeout=10s
    IF    ${noEntitlementFoundMessagePresent}
        Set Test Variable    ${noEntitlementFoundMessageFlag}    True
    ELSE
        Process Entitlement Name
    END

Process Entitlement Name
    ${fullEntitlementName}=    get text    ${getEntitlementName}
    ${beginPosition}=    set variable    ${fullEntitlementName.index('Entitlement Name :') + len('Entitlement Name :')}
    ${finishPosition}=    set variable    ${fullEntitlementName.index(' Type')}
    ${subEntitlementName}=    get substring    ${fullEntitlementName}    ${beginPosition}    ${finishPosition}
    ${subEntitlementName}=    strip string    ${subEntitlementName}
    set test variable    ${finalServiceContractEntitlementName}    ${subEntitlementName}
    scroll element into view    ${entitlementNameOption}
    click element    ${entitlementNameOption}

Verify Service Contracts Submenu
    element should be visible    ${getMyServiceContractsDetailsOption}
    element should be visible    ${howCanICreateANewServiceContractOption}
    element should be visible    ${howCanIRenewMyServiceContractOption}
    element should be visible    ${backToMainMenuOptionFirst}

Verify Get My Service Contracts Submenu
    element should be visible    ${whatAreMyActiveServiceContractsOption}
    element should be visible    ${whatIsTheExpirationDateForMyServiceContractOption}
    element should be visible    ${whatProductsAreCoveredUnderMyServiceContractOption}
    element should be visible    ${whatTypeOfSupportAmIEntitledToOption}
    element should be visible    ${backToMainMenuOption}

Verify Service Contract Details Without Contract Number
    ${serviceContractDetails1}=    replace string
    ...    ${serviceContractDetailsMessage1}
    ...    <serviceContractName>
    ...    ${finalServiceContractName}
    ${serviceContractDetails}=    replace string
    ...    ${serviceContractDetails1}
    ...    <serviceContractStatus>
    ...    ${finalServiceContractStatus}
    ${serviceContractDetailsLocator}=    set variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${serviceContractDetails}")]
    element should be visible    ${serviceContractDetailsLocator}
    element should be visible    ${serviceContractDetailsMessage2}

    element should be visible    ${whatAreMyActiveServiceContractsOption}
    element should be visible    ${whatIsTheExpirationDateForMyServiceContractOption}
    element should be visible    ${whatProductsAreCoveredUnderMyServiceContractOption}
    element should be visible    ${whatTypeOfSupportAmIEntitledToOption}
    element should be visible    ${backToMainMenuOption}

Verify Service Contract Details With Contract Number
    wait until element is visible    ${pleaseEnterYourContractNumber}    timeout=10s
    input text    ${chatBotInput}    ${serviceContractNumber}
    press key    ${chatBotInput}    \\13    # ASCII code for enter key
    element should be visible    ${serviceContractDetailsMessage2}

Verify Expiration Date For Service Contract
    ${finalPattern}=    replace string    ${pattern}    <serviceContractStatus>    ${finalServiceContractStatus}
    ${serviceContractExpirationDetailsText}=    get text    ${serviceContractExpirationDetails}
    should match regexp    ${serviceContractExpirationDetailsText}    ${finalPattern}

Verify Product Details Covered Under Warranty
    IF    '${noProductFoundMessageFlag}' == 'False'
        Verify Product Details
    ELSE
        Log    "No product found under service contract, skipping warranty verification."    WARN
    END

Verify Product Details
    ${serviceContractProductMessage}=    replace string
    ...    ${serviceContractProductDetailsAssetName}
    ...    <serviceContractProductName>
    ...    ${finalServiceContractProductName}
    ${serviceContractProductDetailsLocator}=    set variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${serviceContractProductMessage}")]
    element should be visible    ${serviceContractProductDetailsLocator}
    element should contain    ${serviceContractProductDetailsLocator}    Serial Number:

Verify Entitlement Details Associated With Service Contract
    IF    '${noEntitlementFoundMessageFlag}' == 'False'
        Verify Entitlement Details
    ELSE
        Log    "Unfortunately, the service contract does not contain any entitlements yet."    WARN
    END

Verify Entitlement Details
    ${noEntitlementRecordFoundMessagePresent}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    ${noEntitlementRecordFoundMessage}
    ...    timeout=10s
    IF    ${noEntitlementRecordFoundMessagePresent}
        Log    "According to our records, currently this Entitlement is not linked with any Asset."    WARN
    ELSE
        Validate Entitlement Message
    END

Validate Entitlement Message
    ${serviceContractEntitlementMessage}=    replace string
    ...    ${serviceContractEntitlementDetailsName}
    ...    <serviceContractEntitlementName>
    ...    ${finalServiceContractEntitlementName}
    ${serviceContractEntitlementDetailsLocator}=    set variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${serviceContractEntitlementMessage}")]
    element should be visible    ${serviceContractEntitlementDetailsLocator}
    element should contain    ${serviceContractEntitlementDetailsLocator}    The current status of this Asset is

Verify Return To Main Menu
    scroll element into view    ${mainMenuMessage}
    element should be visible    ${mainMenuMessage}
