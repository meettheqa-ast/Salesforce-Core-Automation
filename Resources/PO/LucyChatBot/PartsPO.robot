*** Settings ***
Resource    ../../Common/LucyChatBot/LucyChatBotCommon.robot


*** Variables ***
# Parts Selection
${assetPartsNameOption}=        xpath:(//embeddedmessaging-conversation-entry-item//span[contains(text(),"Here are your all assets. Please select one to know more in details")]/ancestor::embeddedmessaging-conversation-entry-item//following-sibling::embeddedmessaging-choices-menu//embeddedmessaging-choices-menu-item)[1]
${partsNameOption}=             xpath:(//embeddedmessaging-conversation-entry-item//span[contains(text(),"Please select part from the list below:")]/ancestor::embeddedmessaging-conversation-entry-item//following-sibling::embeddedmessaging-choices-menu//embeddedmessaging-choices-menu-item)[1]
${partsName}=                   xpath:(//embeddedmessaging-conversation-entry-item//span[contains(text(),"Please select part from the list below:")]/ancestor::embeddedmessaging-conversation-entry-item//following-sibling::embeddedmessaging-choices-menu//embeddedmessaging-choices-menu-item)[1]//span
${partsNumberRegex}=            PI-\\d{4}
${partsDetailsName}=            Part Name: <partName>
${finalPartsNumber}=            ${EMPTY}
${completePurchaseMessage}=     xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Please proceed to this 'link' to complete the purchase. Thanks!!!")]
${qtyMessage}=                  xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Please provide the quantity of this part you’d like to order (example is 1, 5, 10), and I’ll check availability of the part.")]
${outOfStockMessage}=           xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Apologies, we do not have ${outStockPartsQty} in stock.")]
${partUnavailableMessage}=      xpath://embeddedmessaging-conversation-entry-item//span[contains(., "I apologize, but we currently don't have that part in stock.")]


*** Keywords ***
Purchase Parts Within Stock
    scroll element into view    ${assetPartsNameOption}
    click element    ${assetPartsNameOption}
    ${partsNameOptionPresent}=    run keyword and return status
    ...    wait until element is visible
    ...    ${partsNameOption}
    ...    timeout=10s
    ${qtyMessagePresent}=    run keyword and return status
    ...    wait until element is visible
    ...    ${qtyMessage}
    ...    timeout=10s
    ${partUnavailableMessagePresent}=    run keyword and return status
    ...    wait until element is visible
    ...    ${partUnavailableMessage}
    ...    timeout=5s
    IF    ${partsNameOptionPresent}==True
        scroll element into view    ${partsNameOption}
        click element    ${partsNameOption}
        ${fullPartsName}=    get text    ${partsName}
        ${partsNumberList}=    get regexp matches    ${fullPartsName}    ${partsNumberRegex}
        set test variable    ${finalPartsNumber}    ${partsNumberList[0]}
#    log to console    ${partsNumberList[0]}
        Verify Part Name Message
        ${partUnavailableMessagePresent}=    run keyword and return status
        ...    wait until element is visible
        ...    ${partUnavailableMessage}
        ...    timeout=10s
        IF    ${partUnavailableMessagePresent}==False
            Enter Parts Quantity    ${inStockPartsQty}
            Verify Complete Purchase Message
        ELSE
            Log    "The part is out of stock; cannot proceed with quantity entry."    WARN
        END
    ELSE IF    ${partsNameOptionPresent}==False and ${qtyMessagePresent}==False and ${partUnavailableMessagePresent}==True
        Log    "The part is out of stock; cannot proceed with quantity entry."    WARN
    ELSE IF    ${partsNameOptionPresent}==False and ${qtyMessagePresent}==True and ${partUnavailableMessagePresent}==False
        Enter Parts Quantity    ${inStockPartsQty}
        Verify Complete Purchase Message
    ELSE IF    ${partsNameOptionPresent}==False and ${qtyMessagePresent}==False and ${partUnavailableMessagePresent}==False
        Log    "Sorry, there are no parts associated with this asset."    WARN
    END

Purchase Parts Out Of Stock
    scroll element into view    ${assetPartsNameOption}
    click element    ${assetPartsNameOption}
    ${partsNameOptionPresent}=    run keyword and return status
    ...    wait until element is visible
    ...    ${partsNameOption}
    ...    timeout=10s
    ${qtyMessagePresent}=    run keyword and return status
    ...    wait until element is visible
    ...    ${qtyMessage}
    ...    timeout=10s
    ${partUnavailableMessagePresent}=    run keyword and return status
    ...    wait until element is visible
    ...    ${partUnavailableMessage}
    ...    timeout=5s
    IF    ${partsNameOptionPresent}==True
        scroll element into view    ${partsNameOption}
        click element    ${partsNameOption}
        ${fullPartsName}=    get text    ${partsName}
        ${partsNumberList}=    get regexp matches    ${fullPartsName}    ${partsNumberRegex}
        set test variable    ${finalPartsNumber}    ${partsNumberList[0]}
        log to console    ${partsNumberList[0]}
        Verify Part Name Message
        ${partUnavailableMessagePresent}=    run keyword and return status
        ...    wait until element is visible
        ...    ${partUnavailableMessage}
        ...    timeout=10s
        IF    ${partUnavailableMessagePresent}==False
            Enter Parts Quantity    ${outStockPartsQty}
            Verify Out Of Stock Message
            Select Choice    Yes
            Verify Complete Purchase Message
        ELSE
            Log    "The part is out of stock; cannot proceed with quantity entry."    WARN
        END
    ELSE IF    ${partsNameOptionPresent}==False and ${qtyMessagePresent}==False and ${partUnavailableMessagePresent}==True
        Log    "The part is out of stock; cannot proceed with quantity entry."    WARN
    ELSE IF    ${partsNameOptionPresent}==False and ${qtyMessagePresent}==True and ${partUnavailableMessagePresent}==False
        Enter Parts Quantity    ${outStockPartsQty}
        Verify Out Of Stock Message
        Select Choice    Yes
        Verify Complete Purchase Message
    ELSE IF    ${partsNameOptionPresent}==False and ${qtyMessagePresent}==False and ${partUnavailableMessagePresent}==False
        Log    "Sorry, there are no parts associated with this asset."    WARN
    END

Verify Part Name Message
    ${partNameMessage}=    replace string    ${partsDetailsName}    <partName>    ${finalPartsNumber}
    ${partNameMessageLocator}=    set variable
    ...    xpath://embeddedmessaging-conversation-entry-item//span[contains(., "${partNameMessage}")]
    element should be visible    ${partNameMessageLocator}
    element should contain    ${partNameMessageLocator}    Status:

Enter Parts Quantity
    [Arguments]    ${qty}
    wait until element is visible    ${qtyMessage}    timeout=10s
    input text    ${chatBotInput}    ${qty}
    press key    ${chatBotInput}    \\13    # ASCII code for enter key

Verify Complete Purchase Message
    element should be visible    ${completePurchaseMessage}

Verify Out Of Stock Message
    element should be visible    ${outOfStockMessage}
