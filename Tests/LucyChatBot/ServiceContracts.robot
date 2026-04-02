*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/Common/LucyChatBot/LucyChatBotCommon.robot
Resource            ../../Resources/PO/LucyChatBot/ServiceContractsPO.robot

Test Setup          Begin Web Test
Test Teardown       End Web Test
# Run the Script
# robot --timestampoutputs -d Results/LucyChatBot/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Tests/LucyChatBot/ServiceContracts.robot


*** Test Cases ***
Verify availability of the Service Contracts submenu
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    ServiceContractsPO.Verify Service Contracts Submenu
    LucyChatBotCommon.Close ChatBot

Verify availability of the 'Get My Service Contracts' submenu
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    LucyChatBotCommon.Select Sub Menu Option    Get My Service Contracts' Details
    LucyChatBotCommon.Enter Email OTP
    ServiceContractsPO.Verify Get My Service Contracts Submenu
    LucyChatBotCommon.Close ChatBot

Verify retrieval of service contract details by entering contract number
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    LucyChatBotCommon.Select Sub Menu Option    Get My Service Contracts' Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    What are my active service contracts?
    LucyChatBotCommon.Select Choice    Yes
    ServiceContractsPO.Verify Service Contract Details With Contract Number

Verify retrieval of service contract details without entering contract number
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    LucyChatBotCommon.Select Sub Menu Option    Get My Service Contracts' Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    What are my active service contracts?
    LucyChatBotCommon.Select Choice    No
    ServiceContractsPO.Select First Service Contract
    ServiceContractsPO.Verify Service Contract Details Without Contract Number
    LucyChatBotCommon.Close ChatBot

Verify user can retrieve service contract expiration date
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    LucyChatBotCommon.Select Sub Menu Option    Get My Service Contracts' Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    What is the expiration date for my service contract?
    LucyChatBotCommon.Select Choice    No
    ServiceContractsPO.Select First Service Contract
    ServiceContractsPO.Verify Expiration Date For Service Contract
    LucyChatBotCommon.Close ChatBot

Verify user can retrieve details of products covered under service contract
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    LucyChatBotCommon.Select Sub Menu Option    Get My Service Contracts' Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    What products are covered under my service contract?
    LucyChatBotCommon.Select Choice    No
    ServiceContractsPO.Select First Service Contract
    ServiceContractsPO.Select First Product
    ServiceContractsPO.Verify Product Details Covered Under Warranty
    LucyChatBotCommon.Close ChatBot

Verify user can retrieve details of support entitlements
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    LucyChatBotCommon.Select Sub Menu Option    Get My Service Contracts' Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Sub Menu Option    What type of support am I entitled to?
    LucyChatBotCommon.Select Choice    No
    ServiceContractsPO.Select First Service Contract
    ServiceContractsPO.Select First Entitlement
    ServiceContractsPO.Verify Entitlement Details Associated With Service Contract
    LucyChatBotCommon.Close ChatBot

Verify user can return to Main Menu from Service Contractts | Get My Service Contracts' Details
    [Tags]    service-contracts    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Contracts
    LucyChatBotCommon.Select Sub Menu Option    Get My Service Contracts' Details
    LucyChatBotCommon.Enter Email OTP
    LucyChatBotCommon.Select Back To Main Menu
    ServiceContractsPO.Verify Return To Main Menu
    LucyChatBotCommon.Close ChatBot
