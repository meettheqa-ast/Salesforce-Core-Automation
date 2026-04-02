*** Settings ***
Library     String
Library     SeleniumLibrary
Library     Collections
Resource    ../../TestData/LucyChatBot/LucyChatBotData.robot
Resource    ../../TestData/LucyChatBot/LucyChatBotEnv.robot


*** Variables ***
# Chatbot CTAs
${chatBotButton}=                   xpath://button[@id="embeddedMessagingConversationButton"]
${chatBotDialog}=                   xpath://iframe[@title="Chat with an Agent"]
${chatBotCloseButton}=              xpath://button[@title="Close chat window"]
${chatBotMenuButton}=               xpath://button[@title='Open messaging menu']
${chatBotEndConversation}=          xpath://button[starts-with(@id, 'closeConversationOptionButton')]

# Chatbot PreChatForm
${chatBotFirstNameInput}=           xpath://input[@name="firstName"]
${chatBotLastNameInput}=            xpath://input[@name="lastName"]
${chatBotEmailInput}=               xpath://input[@name="email"]
${chatBotSubjectInput}=             xpath://input[@name="subject"]
${chatBotStartConversation}=        xpath://button[@title='Start Conversation']
${chatBotInput}=                    xpath://textarea[@placeholder="Type your message..."]
${backToMainMenuOptionFirst}=       xpath://button[@title='Back to Main Menu']
${mainMenuMessage}=                 xpath:(//embeddedmessaging-conversation-entry-item[.//span[contains(., "I’m here to help. Type your question or choose an option from the menu below. Let’s make sure you get the support you need!")] and .//embeddedmessaging-choices-menu ])[last()][count(preceding-sibling::embeddedmessaging-conversation-entry-item[.//span[contains(., "I’m here to help. Type your question or choose an option from the menu below. Let’s make sure you get the support you need!")] and .//embeddedmessaging-choices-menu]) > 0]

# Lucy Chatbot Main Menu Options
${supportOptions}=                  xpath://embeddedmessaging-choices-menu
${warrantiesOption}=                xpath://button[@title='Warranties']
${serviceContractsOption}=          xpath://button[@title='Service Contracts']
${partsOption}=                     xpath://button[@title='Parts']
${serviceAppointmentsOption}=       xpath://button[@title='Service Appointments']
${transferToAgentOption}=           xpath://button[@title='Transfer To Agent']
${unseenMessageBubble}=             xpath://div[starts-with(@id, 'unseenMessageBubble')]
${scrollToLatestMessage}=           xpath://button[starts-with(., 'Scroll the latest messages into view')]
# homepage Home Link present in Nav
${homeLink}=                        xpath://a[text()='Home']

# Yopmail Input Field
${emailClienInput}=                 xpath://input[@id="login"]
${emailClientIframe}=               xpath://iframe[@id="ifmail"]
${emailClientBody}=                 xpath://div[@id="mail"]//pre

# Menu Options Navigation
${optionLocator}=                   xpath://button[@title="<optionName>"]

# Chatbot OTP message
${oneTimePasswordMessage}=          Please enter the one-time passcode sent to the email associated with your account.

${backToMainMenuOption}=            xpath:(//button[@title='Back to Main Menu'])[last()][count(//button[@title='Back to Main Menu']) > 1]


*** Keywords ***
Open Lucy ChatBot
    Click Element    ${chatBotButton}
    Select Frame    ${chatBotDialog}

Close ChatBot
    # Check if the 'Open messaging menu' button is present and click it
    ${menuButtonVisible}=    Run Keyword And Return Status    Element Should Be Visible    ${chatBotMenuButton}
    IF    ${menuButtonVisible}    Click Open Messaging Menu

    # Check if the 'Chatbot close button' is visible
    ${closeButtonVisible}=    Run Keyword And Return Status    Element Should Be Visible    ${chatBotCloseButton}
    IF    ${closeButtonVisible}    Click ChatBot Close Button

Click Open Messaging Menu
    Click Element    ${chatBotMenuButton}
    Click Element    ${chatBotEndConversation}

Click ChatBot Close Button
    Click Element    ${chatBotCloseButton}
    unSelect Frame
    Wait Until Page Does Not Contain Element    ${chatBotCloseButton}

Enter Login Details
    Input Text    ${chatBotFirstNameInput}    ${chatBotFirstName}
    Input Text    ${chatBotLastNameInput}    ${chatBotLastName}
    Input Text    ${chatBotEmailInput}    ${chatBotEmail}
    Input Text    ${chatBotSubjectInput}    ${chatBotSubject}
    Click Element    ${chatBotStartConversation}

Open Home Page
    Go To    ${homePageUrl}
    Wait Until Element Is Visible    ${homeLink}    timeout=15s

Verify User is Logged In Successfully
    Wait Until Page Contains    Hello ${chatBotFirstName}, My name is Lucy.    timeout=15s

Login to Lucy Chatbot
    Open Home Page
    Open Lucy ChatBot
    Enter Login Details
    Verify User is Logged In Successfully

Enter Email OTP
    Wait Until Page Contains    ${oneTimePasswordMessage}
    Execute Javascript    window.open('about:blank', '_blank')
    ${windowHandles}=    Get Window Handles
    Switch Window    ${windowHandles}[1]
    Go To    ${emailClient}
    Input Text    ${emailClienInput}    ${emailEmailName}
    Press Key    ${emailClienInput}    \\13    # ASCII code for enter key
    Select Frame    ${emailClientIframe}
    ${emailContent}=    Get Text    ${emailClientBody}
    ${otp}=    Get Substring    ${emailContent}    62    68
    unSelect Frame
    Close Window
    Switch Window    ${windowHandles}[0]
    Select Frame    ${chatBotDialog}
    Input Text    ${chatBotInput}    ${otp}
    Press Key    ${chatBotInput}    \\13    # ASCII code for enter key

Verify Main Menu Is Available To The User
    Execute Javascript    window.scrollBy(0, document.body.scrollHeight);
    # Wait Until Element Is Visible    ${unseenMessageBubble}    timeout=10s
    # Click Element    ${scrollToLatestMessage}
    Element Should Be Visible    ${warrantiesOption}
    Element Should Be Visible    ${serviceContractsOption}
    Element Should Be Visible    ${partsOption}
    Element Should Be Visible    ${serviceAppointmentsOption}
    Element Should Be Visible    ${transferToAgentOption}

Select Main Menu Option
    [Arguments]    ${menuOption}
    ${menuOptionLocator}=    Replace String    ${optionLocator}    <optionName>    ${menuOption}
    Scroll Element Into View    ${menuOptionLocator}
    Click Element    ${menuOptionLocator}

Select Sub Menu Option
    [Arguments]    ${subMenuOption}
    ${subMenuLocator}=    Replace String    ${optionLocator}    <optionName>    ${subMenuOption}
    Scroll Element Into View    ${subMenuLocator}
    Click Element    ${subMenuLocator}

Select Choice
    [Arguments]    ${chooseYesOrNo}
    ${chooseYesOrNoLocator}=    Replace String    ${optionLocator}    <optionName>    ${chooseYesOrNo}
    Scroll Element Into View    ${chooseYesOrNoLocator}
    Click Element    ${chooseYesOrNoLocator}

Select Back To Main Menu
    Scroll Element Into View    ${backToMainMenuOption}
    Click Element    ${backToMainMenuOption}
