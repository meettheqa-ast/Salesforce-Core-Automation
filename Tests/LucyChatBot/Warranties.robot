*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/Common/LucyChatBot/LucyChatBotCommon.robot
Resource            ../../Resources/PO/LucyChatBot/WarrantiesPO.robot

Test Setup          Begin Web Test
Test Teardown       End Web Test
# Run the Script
# robot --timestampoutputs -d Results/LucyChatBot/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Tests/LucyChatBot/Warranties.robot


*** Test Cases ***
Verify availability of the Main Menu in Lucy chatbot
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Verify Main Menu Is Available To The User
    LucyChatBotCommon.Close ChatBot

Verify availability of the Warranties submenu
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    WarrantiesPO.Verify Warranty Details Submenu
    LucyChatBotCommon.Close ChatBot

Verify availability of the My Asset Warranty Details submenu
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    LucyChatBotCommon.Select Sub Menu Option    My Asset Warranty Details
    LucyChatBotCommon.Enter Email OTP
    WarrantiesPO.Verify My Asset Warranty Details Submenu
    LucyChatBotCommon.Close ChatBot

Verify if an asset is covered under warranty
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    LucyChatBotCommon.Select Sub Menu Option    My Asset Warranty Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    Is my asset covered under warranty?
    WarrantiesPO.Select First Asset
    WarrantiesPO.Select First Warranty
    WarrantiesPO.Verify Whether My Asset Is Covered Under Warranty
    LucyChatBotCommon.Close ChatBot

Verify retrieval of warranty details for a specific product
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    LucyChatBotCommon.Select Sub Menu Option    My Asset Warranty Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    What is my warranty for this product?
    WarrantiesPO.Select First Asset
    WarrantiesPO.Select First Warranty
    WarrantiesPO.Verify Asset Warranty Details
    LucyChatBotCommon.Close ChatBot

Verify the ability to retrieve warranty expiration date
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    LucyChatBotCommon.Select Sub Menu Option    My Asset Warranty Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    When does my warranty expire?
    WarrantiesPO.Select First Asset
    WarrantiesPO.Select First Warranty
    WarrantiesPO.Verify Warranty Expiration Date
    LucyChatBotCommon.Close ChatBot

Verify if the warranty covers parts
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    LucyChatBotCommon.Select Sub Menu Option    My Asset Warranty Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    Does my warranty cover parts?
    WarrantiesPO.Select First Asset
    WarrantiesPO.Select First Warranty
    WarrantiesPO.Verify Parts Warranty Coverage
    LucyChatBotCommon.Close ChatBot

Verify if the warranty covers labor
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    LucyChatBotCommon.Select Sub Menu Option    My Asset Warranty Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    Does my warranty cover labor?
    WarrantiesPO.Select First Asset
    WarrantiesPO.Select First Warranty
    WarrantiesPO.Verify Warranty Labor Coverage
    LucyChatBotCommon.Close ChatBot

Verify if the asset warranty is transferrable
    [Tags]    warranty    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Warranties
    LucyChatBotCommon.Select Sub Menu Option    My Asset Warranty Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    Can the warranty for my asset be transferred if I sell my asset?
    WarrantiesPO.Select First Asset
    WarrantiesPO.Select First Warranty
    WarrantiesPO.Verify Warranty Transfer
    LucyChatBotCommon.Close ChatBot
