*** Settings ***
Library     SeleniumLibrary
Resource    ../../TestData/Platform/PlatformData.robot
Resource    ../../Common/GlobalKeywords.robot


*** Keywords ***
Create a New Work Order
    Open Dropdown    Priority
    Select Dropdown Option    Priority    ${selectWOPriorityOption}
    Enter Into Search Field    Contacts    ${searchWOContactsData}
    Enter Into Search Field    Accounts    ${searchWOAccountData}
    Enter Into Search Field    Assets    ${searchWOAssetsData}
    Enter Text    Subject    ${textWOSubjectData}
    Enter Into Search Field    Work Types    ${searchWOWorkTypesData}
    Enter Date    Entitlement Process Start Time    ${selectDateWOEntitlementProcessStartTime}
    Enter Time    Entitlement Process Start Time    ${selectTimeWOEntitlementProcessStartTime}
    Click Checkbox    Billable Override
    Enter Text    Street    ${textWOStreetData}
    Select Dialog Button    Save

Verify Work Order Redirection To Record Details Page
    Verify Redirection to Record Details Page    Work Order

Verify Work Order Record Creation with Data
    Verify Record Creation With Data    Work Order    Asset    ${searchWOAssetsData}
    Verify Record Creation With Data    Work Order    Account    ${searchWOAccountData}
    Verify Record Creation With Data    Work Order    Contact    ${searchWOContactsData}
    Verify Record Creation With Data    Work Order    Priority    ${selectWOPriorityOption}
    Verify Record Creation With Data    Work Order    Subject    ${textWOSubjectData}
    Verify Record Creation With Data    Work Order    Work Type    ${searchWOWorkTypesData}
    Verify Record Creation With Data
    ...    Work Order
    ...    SlaStartDate
    ...    ${selectDateWOEntitlementProcessStartTime}, ${selectTimeWOEntitlementProcessStartTime}
    Verify Record Creation With Data    Work Order    Billable_Override__c    Checkbox-Check
    Verify Record Creation With Data    Work Order    Address    ${textWOStreetData}

Delete Work Order
    Delete Current Record    Work Order

Create New Product Required In Work Order
    Enter Into Search Field    Products    ${searchPRProductsRequiredData}
    Enter Text    Quantity Required    ${searchPRQuantityRequiredData}
    Open Dropdown    Quantity Unit Of Measure
    Select Dropdown Option    Quantity Unit Of Measure    ${selectPRQuantityUnitOfMeasureOption}
    Select Dialog Button    Save

Verify Related Product Required Record Creation In Work Order
    Visit Dynamic Form Section    System Information
    Verify Related Records Creation    Products Required

Create New Work Order Line Item In Work Order
    Enter Into Search Field    Work Types    ${searchWOWorkTypesData}
    Enter Into Search Field    Assets    ${searchWOAssetsData}
    Select Dialog Button    Save

Verify Related Work Order Line Item Record Creation In Work Order
    Visit Dynamic Form Section    System Information
    Verify Related Records Creation    Work Order Line Items

Create New Service Appointment In Work Order
    Open Dropdown    Status
    Select Dropdown Option    Status    ${selectSAStatusOption}
    Enter Text    User Email Id    ${textSAUserEmailData}
    Enter Date    Earliest Start Permitted    ${selectDateSAEarliestStartPermitted}
    Enter Time    Earliest Start Permitted    ${selectTimeSAEarliestStartPermitted}
    Enter Date    Due Date    ${selectDateSADueDate}
    Enter Time    Due Date    ${selectTimeSADueDate}
    Select Dialog Button    Save

Verify Related Service Appointment Record Creation In Work Order
    Visit Dynamic Form Section    System Information
    Verify Related Records Creation    Service Appointments

Create New Product Requests In Work Order
    Enter Date    Need By Date    ${selectDatePRNeedByDate}
    Enter Time    Need By Date    ${selectTimePRNeedByDate}
    Select Dialog Button    Save

Verify Related Product Request Record Creation In Work Order
    Visit Dynamic Form Section    System Information
    Verify Related Records Creation    Product Requests

Create New Products Consumed In Work Order
    Enter Into Search Field    Product Items    ${searchPCProductItemData}
    Enter Text    Quantity Consumed    ${textPCQuantityConsumedData}
    Select Dialog Button    Save

Verify Related Products Consumed Record Creation In Work Order
    Visit Dynamic Form Section    System Information
    Verify Related Records Creation    Products Consumed

Create New Expenses In Work Order
    Enter Text    Amount    ${textEXPAmountData}
    Enter Date    Transaction Date    ${selectDateEXPTransactionDate}
    Select Dialog Button    Save

Verify Related Expenses Record Creation In Work Order
    Visit Dynamic Form Section    System Information
    Verify Related Records Creation    Expenses
