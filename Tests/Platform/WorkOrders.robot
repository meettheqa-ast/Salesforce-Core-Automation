*** Settings ***
Library             SeleniumLibrary
Resource            ../../Resources/Common/GlobalKeywords.robot
Resource            ../../Resources/PO/Platform/WorkOrdersPO.robot

Test Setup          Begin Web Test
Test Teardown       End Web Test
# Run the Script
# robot --timestampoutputs -d Results/Platform/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Tests/Platform/WorkOrders.robot


*** Test Cases ***
# Verify that user should be able to create Work Order with required information.
#    GlobalKeywords.Login To Sandbox
#    GlobalKeywords.Launch App    Field Service
#    GlobalKeywords.Select App Tab    Work Orders
#    GlobalKeywords.Open New Dialog    Work Order
#    WorkOrdersPO.Create a New Work Order
#    WorkOrdersPO.Verify Work Order Redirection To Record Details Page
#    WorkOrdersPO.Verify Work Order Record Creation with Data
#    WorkOrdersPO.Delete Work Order
#
# Add "Products Required" to the Work Order created and verify that "Products Required" is added successfully to the Work Order
#    GlobalKeywords.Login To Sandbox
#    GlobalKeywords.Launch App    Field Service
#    GlobalKeywords.Select App Tab    Work Orders
#    GlobalKeywords.Open New Dialog    Work Order
#    WorkOrdersPO.Create a New Work Order
#    WorkOrdersPO.Verify Work Order Redirection To Record Details Page
#    WorkOrdersPO.Verify Work Order Record Creation with Data
#    GlobalKeywords.Open Related Record Dropdown    Products Required    New
#    WorkOrdersPO.Create New Product Required In Work Order
#    GlobalKeywords.Get Success Toast Message Related Record Creation ID
#    WorkOrdersPO.Verify Related Product Required Record Creation In Work Order
#    GlobalKeywords.Return Back To Parent
#    WorkOrdersPO.Delete Work Order

Execute And Verify Entire Work Order Flow
    [Tags]    field service    smoke    regression
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    GlobalKeywords.Launch App    Field Service
    GlobalKeywords.Select App Tab    Work Orders
    GlobalKeywords.Open New Dialog    Work Order
    WorkOrdersPO.Create a New Work Order
    WorkOrdersPO.Verify Work Order Redirection To Record Details Page
    WorkOrdersPO.Verify Work Order Record Creation with Data

    GlobalKeywords.Open Related Record Dropdown    Products Required    New
    WorkOrdersPO.Create New Product Required In Work Order
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
    WorkOrdersPO.Verify Related Product Required Record Creation In Work Order

    GlobalKeywords.Return To Previous Page

    GlobalKeywords.Open Related Record Dropdown    Work Order Line Items    New
    WorkOrdersPO.Create New Work Order Line Item In Work Order
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
    WorkOrdersPO.Verify Related Work Order Line Item Record Creation In Work Order

    GlobalKeywords.Return To Previous Page

    GlobalKeywords.Open Related Record Dropdown    Service Appointments    New
    WorkOrdersPO.Create New Service Appointment In Work Order
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
    WorkOrdersPO.Verify Related Service Appointment Record Creation In Work Order

    GlobalKeywords.Return To Previous Page

    GlobalKeywords.Open Related Record Dropdown    Product Requests    New
    WorkOrdersPO.Create New Product Requests In Work Order
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
    WorkOrdersPO.Verify Related Product Request Record Creation In Work Order

    GlobalKeywords.Return To Previous Page

    GlobalKeywords.Open Related Record Dropdown    Products Consumed    New
    WorkOrdersPO.Create New Products Consumed In Work Order
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
    WorkOrdersPO.Verify Related Products Consumed Record Creation In Work Order

    GlobalKeywords.Return To Previous Page

    GlobalKeywords.Open Related Record Dropdown    Expenses    New
    WorkOrdersPO.Create New Expenses In Work Order
    GlobalKeywords.Get Success Toast Message Related Record Creation ID
#    WorkOrdersPO.Verify Related Expenses Record Creation In Work Order

#    GlobalKeywords.Return To Previous Page

    WorkOrdersPO.Delete Work Order
