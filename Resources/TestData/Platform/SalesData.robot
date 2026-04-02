*** Variables ***
# Dropdown value of the lead status can be set into this field
${leadStatusOption}=                        New

# Raw Phone Number without formatting
${rawPhoneNumber}=                          ${{FakerLibrary.FakerLibrary().password(length=10, special_chars=False, digits=True, upper_case=False, lower_case=False)}}

# Lead test data
${salutationOption}=                        Mr.
${leadFirstName}=                           ${{FakerLibrary.FakerLibrary().first_name_male()}}
${leadLastName}=                            ${{FakerLibrary.FakerLibrary().last_name()}}
${leadCompany}=                             ${{FakerLibrary.FakerLibrary().company()}}
${leadWebsite}=                             www.ptest.com
${leadPhone}=                               1 (${rawPhoneNumber[0:3]}) ${rawPhoneNumber[3:6]}-${rawPhoneNumber[6:10]}
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

# Account Test Data
${accountName}=                             ${{FakerLibrary.FakerLibrary().name()}}
${accountPhone}=                            1 (${rawPhoneNumber[0:3]}) ${rawPhoneNumber[3:6]}-${rawPhoneNumber[6:10]}
# ${accountPhone}=    +1 ${{FakerLibrary.FakerLibrary().password(length=10, special_chars=False, digits=True, upper_case=False, lower_case=False)}}
${accountWebsite}=                          www.ptest.com
${accountType}=                             Prospect
${accountIndustry}=                         Manufacturing
${accountEmployees}=                        2
