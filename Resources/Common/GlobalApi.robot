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

API Seed Opportunity
    [Documentation]    Create an Opportunity via the REST API and return its record ID.
    ...                At minimum ``Name``, ``StageName``, and ``CloseDate`` are required.
    [Tags]    api    data-seeding
    [Arguments]    ${Name}    ${StageName}    ${CloseDate}    &{extra_fields}
    ${id}=    API Create Record    Opportunity    Name=${Name}    StageName=${StageName}    CloseDate=${CloseDate}    &{extra_fields}
    RETURN    ${id}

API Seed Campaign
    [Documentation]    Create a Salesforce Campaign via REST and return its record ID.
    ...                Sensible defaults: Type=Advertisement, Status=Planned, IsActive=True.
    ...                Pass any other Salesforce field via ``&{extra_fields}`` (e.g. ``StartDate``,
    ...                ``EndDate``, ``Description``).
    [Tags]    api    data-seeding    campaign
    [Arguments]    ${Name}    ${Type}=Advertisement    ${Status}=Planned    ${IsActive}=${TRUE}    &{extra_fields}
    ${id}=    API Create Record    Campaign    Name=${Name}    Type=${Type}    Status=${Status}    IsActive=${IsActive}    &{extra_fields}
    RETURN    ${id}

API Add Campaign Member
    [Documentation]    Link a Lead or Contact to a Campaign by creating a ``CampaignMember`` record.
    ...                Pass either ``LeadId`` or ``ContactId`` (not both — Salesforce rejects that).
    ...                Returns the new CampaignMember record ID.
    [Tags]    api    data-seeding    campaign
    [Arguments]    ${CampaignId}    ${LeadId}=${EMPTY}    ${ContactId}=${EMPTY}    ${Status}=Sent    &{extra_fields}
    ${fields}=    Create Dictionary    CampaignId=${CampaignId}    Status=${Status}    &{extra_fields}
    IF    '${LeadId}' != '${EMPTY}'
        Set To Dictionary    ${fields}    LeadId=${LeadId}
    END
    IF    '${ContactId}' != '${EMPTY}'
        Set To Dictionary    ${fields}    ContactId=${ContactId}
    END
    ${id}=    API Create Record    CampaignMember    &{fields}
    RETURN    ${id}

API Seed Lead On Campaign
    [Documentation]    Convenience: seed a Lead and link it to an existing Campaign in one call.
    ...                Returns the new Lead record ID. Useful when staging "Lead generated from
    ...                Campaign" scenarios where the test only cares about the Lead, not the
    ...                CampaignMember junction record.
    [Tags]    api    data-seeding    campaign
    [Arguments]    ${CampaignId}    ${LastName}    ${Company}    &{extra_fields}
    ${leadId}=    API Seed Lead    LastName=${LastName}    Company=${Company}    &{extra_fields}
    API Add Campaign Member    CampaignId=${CampaignId}    LeadId=${leadId}
    RETURN    ${leadId}

API Cleanup Record
    [Documentation]    Delete a record by SObject name and ID. Wrapper around ``API Delete Record``.
    ...                Safe to call from a ``[Teardown]`` even when the seed step
    ...                failed before the id variable was assigned: an empty / whitespace
    ...                ``${record_id}`` is a no-op, logged at INFO so the teardown still
    ...                shows up in the run report.
    [Tags]    api    data-seeding    teardown
    [Arguments]    ${object_name}    ${record_id}
    ${trimmed}=    Strip String    ${record_id}
    IF    "${trimmed}" == "" or "${trimmed}".upper() == "NONE"
        Log    API Cleanup Record: skipping ${object_name} cleanup (record id is empty -- seed likely failed).    INFO
        RETURN
    END
    API Delete Record    ${object_name}    ${trimmed}

API Seed Project Data Template
    [Documentation]    Parse a JSON template string, create each record via the API,
    ...                and expose the resulting IDs as TEST-scoped variables.
    ...                Also stores a teardown list in ``@{_TDM_TEARDOWN}`` for cleanup.
    [Tags]    api    data-seeding    tdm
    [Arguments]    ${template_json_string}
    ${items}=    Evaluate    json.loads(r'''${template_json_string}''')    json
    @{teardown}=    Create List
    FOR    ${item}    IN    @{items}
        ${obj}=    Set Variable    ${item}[object]
        ${var}=    Set Variable    ${item}[var_name]
        ${fields}=    Set Variable    ${item}[fields]
        ${id}=    API Create Record    ${obj}    &{fields}
        Set Test Variable    ${${var}}    ${id}
        ${entry}=    Create Dictionary    object=${obj}    id=${id}
        Append To List    ${teardown}    ${entry}
    END
    Set Test Variable    @{_TDM_TEARDOWN}    @{teardown}
    Log    Seeded ${items.__len__()} record(s) from project data template.    INFO
    RETURN    @{teardown}

API Teardown Seeded Records
    [Documentation]    Delete all records tracked in ``@{_TDM_TEARDOWN}`` (reverse order).
    ...                Safe to call even if no records were seeded.
    [Tags]    api    data-seeding    teardown    tdm
    ${exists}=    Run Keyword And Return Status    Variable Should Exist    @{_TDM_TEARDOWN}
    IF    not $exists    RETURN
    ${reversed}=    Evaluate    list(reversed(${{_TDM_TEARDOWN}}))
    FOR    ${entry}    IN    @{reversed}
        TRY
            API Delete Record    ${entry}[object]    ${entry}[id]
            Log    Deleted ${entry}[object] ${entry}[id]    INFO
        EXCEPT    AS    ${err}
            Log    Teardown failed for ${entry}[object] ${entry}[id]: ${err}    WARN
        END
    END
