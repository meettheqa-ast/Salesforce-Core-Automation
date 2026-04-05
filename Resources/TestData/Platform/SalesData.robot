*** Variables ***
# Lead Status: leave empty for org-agnostic random valid option; set a label/API value when PM specifies (``-v leadStatusOption:...``).
${leadStatusOption}=                        ${EMPTY}

# Strict 10-digit phone (digits only—no spaces, hyphens, or parentheses) for orgs with tight phone validation
${randomPhone}=                             ${{FakerLibrary.FakerLibrary().numerify(text='##########')}}
# Backward-compatible alias used by legacy slicing / display patterns
${rawPhoneNumber}=                         ${randomPhone}

# Lead test data
${salutationOption}=                        Mr.
${leadFirstName}=                           ${{FakerLibrary.FakerLibrary().first_name_male()}}
${leadLastName}=                            ${{FakerLibrary.FakerLibrary().last_name()}}
${leadCompany}=                             ${{FakerLibrary.FakerLibrary().company()}}
${leadWebsite}=                             www.ptest.com
${leadPhone}=                               ${randomPhone}
${leadTitle}=                               Test Lead
${leadEmail}=                               ${{FakerLibrary.FakerLibrary().email()}}
${leadSourceOption}=                        Advertisement

# Convert lead to opportunity test data
${leadConvertedStatusOption}=               Qualified

# Opportunity Test Data
${opportunityAccountName}=                  SUN CITY PLANT
${opportunityName}=                         ${opportunityAccountName}-Opportunity-${{FakerLibrary.FakerLibrary().password(length=10, special_chars=False, digits=True, upper_case=False, lower_case=False)}}
${opportunityForecastCategoryOption}=       Pipeline
${opportunityNextStep}=                     Test Step
${opportunityAmount}=                       100
${opportunityCloseDate}=                    12/05/2024
${opportunityStageOption}=                  Proposal
${opportunityType}=                         New Business
${opportunityLeadSource}=                   Customer Event
${opportunityDescription}=                  Test Description

# App display name as it appears in the Salesforce app launcher (e.g. Mark Anthony Group)
${salesAutomationAppName}=                  Sales

# Account Test Data
${accountName}=                             ${{FakerLibrary.FakerLibrary().name()}}
${accountPhone}=                            ${randomPhone}
# ${accountPhone}=    +1 ${{FakerLibrary.FakerLibrary().password(length=10, special_chars=False, digits=True, upper_case=False, lower_case=False)}}
${accountWebsite}=                          www.ptest.com
${accountType}=                             Prospect
${accountIndustry}=                         Manufacturing
${accountEmployees}=                        2

# Contact Test Data
${contactFirstName}=                        ${{FakerLibrary.FakerLibrary().first_name()}}
${contactLastName}=                         ${{FakerLibrary.FakerLibrary().last_name()}}
${contactTitle}=                            Smoke Contact
${contactEmail}=                            ${{FakerLibrary.FakerLibrary().email()}}
${contactPhone}=                            ${randomPhone}
# Link contact to this existing account (leave empty to skip Account Name lookup)
${contactAccountName}=                      ${opportunityAccountName}
