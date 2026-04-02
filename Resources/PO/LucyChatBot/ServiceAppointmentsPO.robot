*** Settings ***
Resource    ../../Common/LucyChatBot/LucyChatBotCommon.robot


*** Variables ***
# Service Appointments Sub Main Menu
${findServiceAppointmentInformation}=       xpath://button[@title='Find Service Appointment Information']
${requestServiceAppointment}=               xpath://button[@title='Request Service Appointment']
${updateCancelServiceAppointment}=          xpath://button[@title='Update/Cancel Service Appointment']

# Find Service Appointment Information Details Submenu
${findServiceAppointment}=                  xpath://button[@title='Find Service Appointment']
${whereIsMyTechnician}=                     xpath://button[@title='Where is my technician ?']
${iNeedOtherAppointmentAssistance}=         xpath://button[@title='I need other appointment assistance']

# Update/Cancel Service Appointment Details Submenu
${updateServiceAppointmentAddress}=         xpath://button[@title='Update Service Appointment Address']
${cancelServiceAppointment}=                xpath://button[@title='Cancel Service Appointment']
${rescheduleServiceAppointment}=            xpath://button[@title='Reschedule Service Appointment']
${addAdditionalNoteForTechnician}=          xpath://button[@title='Add Additional Note for Technician']

# Find Service Appointment
${accountNameOption}=                       xpath:(//embeddedmessaging-choices-buttons[contains(., 'Account Name') and contains(., 'Account Number')])[1]
${noServiceAppointmentFoundMessage}=        xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Unfortunately currently you do not have any active service appointments.")]
${noServiceAppointmentMessageFlag}=         False
${getServiceAppointmentName}=               xpath:(//button[@class='embedded-messaging-quick-reply' and contains(., 'Service Appointment Number :')])[1]//span
${serviceAppointmentNameOption}=            xpath:(//button[@class='embedded-messaging-quick-reply' and contains(., 'Service Appointment Number :')])[1]
${finalServiceAppointmentNumber}=           ${EMPTY}
${serviceAppointmentMessageLocator}=        xpath://embeddedmessaging-conversation-entry-item//span[contains(., "Service Appointment <serviceAppointmentNumber> has been") or contains(., "Unfortunately this Service Appointment has been cancelled")]


*** Keywords ***
Select First Account
    scroll element into view    ${accountNameOption}
    click element    ${accountNameOption}

Select First Service Appointment
    ${noServiceAppointmentMessagePresent}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    ${noServiceAppointmentFoundMessage}
    ...    timeout=10s
    IF    ${noServiceAppointmentMessagePresent}
        Set Test Variable    ${noServiceAppointmentMessageFlag}    True
    ELSE
        Select Service Appointment Option
    END

Select Service Appointment Option
    ${fullServiceAppointmentDetails}=    get text    ${getServiceAppointmentName}
    ${fullServiceAppointmentDetailsLen}=    get length    ${fullServiceAppointmentDetails}
    ${startPos}=    set variable
    ...    ${fullServiceAppointmentDetails.index('Service Appointment Number :') + len('Service Appointment Number :')}
    ${subServiceAppointmentNumber}=    get substring
    ...    ${fullServiceAppointmentDetails}
    ...    ${startPos}
    ...    ${fullServiceAppointmentDetailsLen}
    ${subServiceAppointmentNumber}=    strip string    ${subServiceAppointmentNumber}    # Remove spaces
    log to console    ${subServiceAppointmentNumber}
    set test variable    ${finalServiceAppointmentNumber}    ${subServiceAppointmentNumber}
    scroll element into view    ${serviceAppointmentNameOption}
    click element    ${serviceAppointmentNameOption}

Verify Service Appointments Details Submenu
    element should be visible    ${findServiceAppointmentInformation}
    element should be visible    ${requestServiceAppointment}
    element should be visible    ${updateCancelServiceAppointment}

Verify Find Service Appointment Information Details Submenu
    element should be visible    ${findServiceAppointment}
    element should be visible    ${whereIsMyTechnician}
    element should be visible    ${iNeedOtherAppointmentAssistance}
    element should be visible    ${backToMainMenuOptionFirst}

Verify Update/Cancel Service Appointment Details Submenu
    element should be visible    ${updateServiceAppointmentAddress}
    element should be visible    ${cancelServiceAppointment}
    element should be visible    ${rescheduleServiceAppointment}
    element should be visible    ${addAdditionalNoteForTechnician}
    element should be visible    ${backToMainMenuOptionFirst}

Verify Retrieval Of Service Appointment Details By Appointment Number
    IF    '${noServiceAppointmentMessageFlag}' == 'False'
        Check Service Appointment Details
    ELSE
        Log    "Unfortunately currently you do not have any active service appointments."    WARN
    END

Check Service Appointment Details
    ${serviceAppointmentMessageLocator}=    replace string
    ...    ${serviceAppointmentMessageLocator}
    ...    <serviceAppointmentNumber>
    ...    ${finalServiceAppointmentNumber}

    ${serviceAppointmentMessageLocatorText}=    get text    ${serviceAppointmentMessageLocator}
    ${serviceAppointmentMessageLocatorText}=    replace string
    ...    ${serviceAppointmentMessageLocatorText}
    ...    \n
    ...    \\\\n

    IF    'Service Appointment ${finalServiceAppointmentNumber} has been' in '${serviceAppointmentMessageLocatorText}'
        element should contain
        ...    ${serviceAppointmentMessageLocator}
        ...    Here are the details about your Service Appointment.
    ELSE IF    'Unfortunately this Service Appointment has been cancelled' in '${serviceAppointmentMessageLocatorText}'
        Log    "Service Appointment cancellation message is present."    WARN
    ELSE
        Log    "Neither 'Service Appointment created' nor 'Service Appointment cancelled' message was found."    ERROR
        Fail    "Expected message about the Service Appointment was not found."
    END
