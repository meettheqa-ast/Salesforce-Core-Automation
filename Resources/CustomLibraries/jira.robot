*** Settings ***
Library     RequestsLibrary
# API DOC: https://support.smartbear.com/zephyr-scale-cloud/api-docs/
# To generate junit xml file, add -x <junit-filename> when you execute test cases.
# Example: robot -x junitresult.xml --timestampoutputs -d Results/Platform/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Tests/Platform/Sales.robot
# run the jira.robot test using below command
# robot --timestampoutputs -d Results/Platform/$(Get-Date -Format "dd-MM-yyyy HH-mm-ss") Resources/CustomLibraries/jira.robot


*** Variables ***
${TOKEN}            eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJjb250ZXh0Ijp7ImJhc2VVcmwiOiJodHRwczovL2p1cmllbGJ1ZW5kcmlvLmF0bGFzc2lhbi5uZXQiLCJ1c2VyIjp7ImFjY291bnRJZCI6IjcxMjAyMDo5ZWIxNWE5MC0zM2UzLTRmNTYtYjMzZi1jMDg0YjdiNGI3YTYiLCJ0b2tlbklkIjoiZTlmMTMyM2ItMTYzYi00Y2QxLTk0ZDEtY2FjODAxMjBkYTI4In19LCJpc3MiOiJjb20ua2Fub2FoLnRlc3QtbWFuYWdlciIsInN1YiI6ImZiOWExNzU1LTNhYjQtMzYwZi1iMDUwLWE3NDgyMmY0YTA5MSIsImV4cCI6MTc2OTA2OTQzOCwiaWF0IjoxNzM3NTMzNDM4fQ.E2G8XCzxMBDxytCGtfcmZnaSZTK5dyeWA-I37PW9qng
${FILE_PATH}        ${CURDIR}/../../Results/Platform/22-01-2025 13-36-47/jiraupload.xml
${API_URL}          https://api.zephyrscale.smartbear.com/v2/automations/executions/junit
${PROJECT_KEY}      SCRUM


*** Test Cases ***
Upload JUnit Result To Zephyr
    ${headers}=    Create Dictionary
    ...    Authorization=Bearer ${TOKEN}

    ${params}=    Create Dictionary
    ...    projectKey=${PROJECT_KEY}
    ...    autoCreateTestCases=false

    ${file_data}=    Get File For Streaming Upload    ${FILE_PATH}
    ${files}=    Create Dictionary
    ...    file=${file_data}

    ${response}=    Post    ${API_URL}    headers=${headers}    files=${files}    params=${params}

    Should Be Equal As Numbers    ${response.status_code}    200
    Log    ${response.text}
