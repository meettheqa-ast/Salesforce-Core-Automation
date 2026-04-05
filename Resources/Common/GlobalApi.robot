*** Settings ***
Documentation    Salesforce REST API data-seeding layer.
...              Imports ``SalesforceApiLibrary`` with the sandbox credentials written
...              by ``run_test.py`` into ``EnvData.robot``. Tests that need prerequisite
...              data should ``Resource`` this file and call ``API Create Record``,
...              ``API Delete Record``, or ``API Query Records``.
Library     ../../Libraries/SalesforceApiLibrary.py
...         username=${sandboxUserNameInput}
...         password=${sandboxPasswordInput}
...         security_token=${sandboxSecurityToken}
...         sandbox_url=${globalSandboxTestUrl}
Resource    ../../Resources/TestData/EnvData.robot

*** Keywords ***
API Seed Lead
    [Documentation]    Create a Lead via the REST API and return its record ID.
    ...                Pass field values as named arguments matching Salesforce API names.
    ...                At minimum ``LastName`` and ``Company`` are required by most orgs.
    [Tags]    api    data-seeding
    [Arguments]    ${LastName}    ${Company}    &{extra_fields}
    ${id}=    API Create Record    Lead    LastName=${LastName}    Company=${Company}    &{extra_fields}
    RETURN    ${id}

API Seed Account
    [Documentation]    Create an Account via the REST API and return its record ID.
    [Tags]    api    data-seeding
    [Arguments]    ${Name}    &{extra_fields}
    ${id}=    API Create Record    Account    Name=${Name}    &{extra_fields}
    RETURN    ${id}

API Seed Contact
    [Documentation]    Create a Contact via the REST API and return its record ID.
    ...                ``AccountId`` should be provided to link the Contact to an Account.
    [Tags]    api    data-seeding
    [Arguments]    ${LastName}    &{extra_fields}
    ${id}=    API Create Record    Contact    LastName=${LastName}    &{extra_fields}
    RETURN    ${id}

API Cleanup Record
    [Documentation]    Delete a record by SObject name and ID. Wrapper around ``API Delete Record``.
    [Tags]    api    data-seeding    teardown
    [Arguments]    ${object_name}    ${record_id}
    API Delete Record    ${object_name}    ${record_id}
