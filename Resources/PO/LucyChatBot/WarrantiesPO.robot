*** Settings ***
Resource    ../../Common/LucyChatBot/LucyChatBotCommon.robot


*** Variables ***
# Warranties Sub Main Menu
${myAssetWarrantyDetailsOption}                                 xpath://button[@title='My Asset Warranty Details']
${howDoIRegisterMyAssetForWarrantyOption}                       xpath://button[@title='How do I register my asset for warranty?']

# My Asset Warranty Sub Menu
${isMyAssetCoveredUnderWarrantyOption}                          xpath://button[@title='Is my asset covered under warranty?']
${whatIsMyWarrantyForThisProductOption}                         xpath://button[@title='What is my warranty for this product?']
${whenDoesMyWarrantyExpireOption}                               xpath://button[@title='When does my warranty expire?']
${doesMyWarrantyCoverPartsOption}                               xpath://button[@title='Does my warranty cover parts?']
${doesMyWarrantyCoverLaborOption}                               xpath://button[@title='Does my warranty cover labor?']
${canTheWarrantyForMyAssetBeTransferredIfISellMyAssetOption}    xpath://button[@title='Can the warranty for my asset be transferred if I sell my asset?']
${assetWarrantyDetails2ndMessage}                               xpath://embeddedmessaging-conversation-entry-item//span[contains(., 'Here is your warranty information for this product')]

# Dictionary with string keys for months
&{Months}
...                                                             01=Jan
...                                                             02=Feb
...                                                             03=Mar
...                                                             04=Apr
...                                                             05=May
...                                                             06=Jun
...                                                             07=Jul
...                                                             08=Aug
...                                                             09=Sep
...                                                             10=Oct
...                                                             11=Nov
...                                                             12=Dec

${warrantyMessage}                                              The warranty for <assetName> with serial number
${noWarrantyFoundMessageLocator}                                xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Unfortunately there are no warranties associated with this Asset.")]
${noWarrantyFoundMessage}                                       Unfortunately there are no warranties associated with this Asset.
${noWarrantyFoundMessageFlag}                                   False

# Asset Product Selection
${assetNameOption}=                                             xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Asset Name :')])[1]
${getAssetName}=                                                xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Asset Name :')])[1]//span
${finalAssetName}=                                              ${EMPTY}

# Asset Product Warranty Details
${assetWarrantyOption}=                                         xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Asset Warranty Number : ')])[1]
${assetWarrantyNumber}=                                         xpath:(//embeddedmessaging-choices-menu-item[contains(., 'Asset Warranty Number : ')])[1]//span
${finalWarrantyNumber}=                                         ${EMPTY}
${finalWarrantyStartDate}=                                      ${EMPTY}


*** Keywords ***
Select First Asset
    ${fullAssetName}=    get text    ${getAssetName}
    ${subAssetName}=    get substring    ${fullAssetName}    13
    # Set the finalAssetName to assetName
    set test variable    ${finalAssetName}    ${subAssetName}
    scroll element into view    ${assetNameOption}
    click element    ${assetNameOption}

Select First Warranty
    ${noWarrantyFoundMessagePresent}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    ${noWarrantyFoundMessageLocator}
    ...    timeout=10s
    IF    ${noWarrantyFoundMessagePresent}
        Set Test Variable    ${noWarrantyFoundMessageFlag}    True
    ELSE
        Select First Warranty Option
    END

Select First Warranty Option
    ${fullAssetWarrantyNumber}=    get text    ${assetWarrantyNumber}
    ${subWarrantyNumber}=    get substring    ${fullAssetWarrantyNumber}    24    31
    ${subWarrantyStartDate}=    get substring    ${fullAssetWarrantyNumber}    70    80
    set test variable    ${finalWarrantyStartDate}    ${subWarrantyStartDate}
    set test variable    ${finalWarrantyNumber}    ${subWarrantyNumber}
    scroll element into view    ${assetWarrantyOption}
    click element    ${assetWarrantyOption}

Verify Warranty Details Submenu
    element should be visible    ${myAssetWarrantyDetailsOption}
    element should be visible    ${howDoIRegisterMyAssetForWarrantyOption}
    element should be visible    ${backToMainMenuOptionFirst}

Verify My Asset Warranty Details Submenu
    element should be visible    ${isMyAssetCoveredUnderWarrantyOption}
    element should be visible    ${whatIsMyWarrantyForThisProductOption}
    element should be visible    ${whenDoesMyWarrantyExpireOption}
    element should be visible    ${doesMyWarrantyCoverPartsOption}
    element should be visible    ${doesMyWarrantyCoverLaborOption}
    element should be visible    ${canTheWarrantyForMyAssetBeTransferredIfISellMyAssetOption}
    element should be visible    ${backToMainMenuOption}

Verify Whether My Asset Is Covered Under Warranty
    IF    '${noWarrantyFoundMessageFlag}' == 'False'
        Check Asset Warranty Coverage Status
    ELSE
        Log    ${noWarrantyFoundMessage}    WARN
    END

Check Asset Warranty Coverage Status
    wait until page contains    the asset ${finalAssetName} with serial number    timeout=10s

Verify Asset Warranty Details
    IF    '${noWarrantyFoundMessageFlag}' == 'False'
        Check Asset Warranty Details
    ELSE
        Log    ${noWarrantyFoundMessage}    WARN
    END

Check Asset Warranty Details
    scroll element into view    ${assetWarrantyDetails2ndMessage}
    element should contain    ${assetWarrantyDetails2ndMessage}    ${finalAssetName}
    element should contain    ${assetWarrantyDetails2ndMessage}    ${finalWarrantyNumber}

Verify Warranty Expiration Date
    IF    '${noWarrantyFoundMessageFlag}' == 'False'
        Check Warranty Expiration Date
    ELSE
        Log    ${noWarrantyFoundMessage}    WARN
    END

Check Warranty Expiration Date
    ${warrantyExpiryDetails}=    replace string    ${warrantyMessage}    <assetName>    ${finalAssetName}
    ${warrantyExpireMessageLocator}=    Set Variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${warrantyExpiryDetails}")]
    scroll element into view    ${warrantyExpireMessageLocator}

    # Split the date into year, month, and day
    ${year}=    Get Substring    ${finalWarrantyStartDate}    0    4
    ${month}=    Get Substring    ${finalWarrantyStartDate}    5    7
    ${day}=    Get Substring    ${finalWarrantyStartDate}    8    10
    ${month}=    Convert To String    ${month}
    ${month}=    Set Variable    ${month}
    # Get the month abbreviation from the dictionary
    ${monthName}=    Get From Dictionary    ${Months}    ${month}
    ${warrantyStartDate}=    Set Variable    ${monthName} ${day}, ${year}
    element should contain    ${warrantyExpireMessageLocator}    ${warrantyStartDate}

Verify Parts Warranty Coverage
    IF    '${noWarrantyFoundMessageFlag}' == 'False'
        Check Parts Warranty Coverage
    ELSE
        Log    ${noWarrantyFoundMessage}    WARN
    END

Check Parts Warranty Coverage
    ${warrantyPartDetails}=    replace string    ${warrantyMessage}    <assetName>    ${finalAssetName}
    ${warrantyPartDetailsLocator}=    set variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${warrantyPartDetails}")]
    scroll element into view    ${warrantyPartDetailsLocator}
    element should contain    ${warrantyPartDetailsLocator}    of parts and the coverage of these parts are till

Verify Warranty Labor Coverage
    IF    '${noWarrantyFoundMessageFlag}' == 'False'
        Check Warranty Labor Coverage
    ELSE
        Log    ${noWarrantyFoundMessage}    WARN
    END

Check Warranty Labor Coverage
    ${warrantyLaborDetails}=    replace string    ${warrantyMessage}    <assetName>    ${finalAssetName}
    ${warrantyLaborDetailsLocator}=    set variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${warrantyLaborDetails}")]
    scroll element into view    ${warrantyLaborDetailsLocator}
    element should contain    ${warrantyLaborDetailsLocator}    Currently it covers

Verify Warranty Transfer
    IF    '${noWarrantyFoundMessageFlag}' == 'False'
        Check Warranty Transfer
    ELSE
        Log    ${noWarrantyFoundMessage}    WARN
    END

Check Warranty Transfer
    ${warranyTransferDetails}=    replace string    ${warrantyMessage}    <assetName>    ${finalAssetName}
    ${warrantyTransferDetailsLocator}=    set variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${warranyTransferDetails}")]
    scroll element into view    ${warrantyTransferDetailsLocator}
    element should contain    ${warrantyTransferDetailsLocator}    Transferable
