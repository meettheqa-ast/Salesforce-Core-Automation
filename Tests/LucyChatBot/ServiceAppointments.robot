*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/Common/LucyChatBot/LucyChatBotCommon.robot
Resource            ../../Resources/PO/LucyChatBot/ServiceAppointmentsPO.robot

Test Setup          Begin Web Test
Test Teardown       End Web Test
# Run the Script
# robot --timestampoutputs -d Results/LucyChatBot/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Tests/LucyChatBot/ServiceAppointments.robot


*** Test Cases ***
Verify availability of the Service Appointments submenu
    [Tags]    service-appointments    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Appointments
    ServiceAppointmentsPO.Verify Service Appointments Details Submenu
    LucyChatBotCommon.Close ChatBot

Verify availability of Find Service Appointment Information submenu
    [Tags]    service-appointments    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Appointments
    LucyChatBotCommon.Select Sub Menu Option    Find Service Appointment Information
    ServiceAppointmentsPO.Verify Find Service Appointment Information Details Submenu
    LucyChatBotCommon.Close ChatBot

Verify availability of Update/Cancel Service Appointment submenu
    [Tags]    service-appointments    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Appointments
    LucyChatBotCommon.Select Sub Menu Option    Update/Cancel Service Appointment
    ServiceAppointmentsPO.Verify Update/Cancel Service Appointment Details Submenu
    LucyChatBotCommon.Close ChatBot

Verify user can retrieve Service Appointment details by selecting appointment number
    [Tags]    service-appointments    smokes
    LucyChatBotCommon.Login to Lucy Chatbot
    LucyChatBotCommon.Select Main Menu Option    Service Appointments
    LucyChatBotCommon.Select Sub Menu Option    Find Service Appointment Information
    LucyChatBotCommon.Select Sub Menu Option    Find Service Appointment
    LucyChatBotCommon.Enter Email OTP
    ServiceAppointmentsPO.Select First Account
    ServiceAppointmentsPO.Select First Service Appointment
    ServiceAppointmentsPO.Verify Retrieval Of Service Appointment Details By Appointment Number
    LucyChatBotCommon.Close ChatBot
