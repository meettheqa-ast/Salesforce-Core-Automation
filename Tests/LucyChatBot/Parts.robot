*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/Common/LucyChatBot/LucyChatBotCommon.robot
Resource            ../../Resources/PO/LucyChatBot/PartsPO.robot

Test Setup          Begin Web Test
Test Teardown       End Web Test
# Run the Script
# robot --timestampoutputs -d Results/LucyChatBot/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Tests/LucyChatBot/Parts.robot


*** Test Cases ***
Verify Customer Buy Parts Within Stock
    [Tags]    parts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Parts
    LucyChatBotCommon.Enter Email OTP
    PartsPO.Purchase Parts Within Stock
    LucyChatBotCommon.Close ChatBot

Verify Customer Buy Parts Outside Stock Quantity
    [Tags]    parts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Parts
    LucyChatBotCommon.Enter Email OTP
    PartsPO.Purchase Parts Out Of Stock
    LucyChatBotCommon.Close ChatBot
