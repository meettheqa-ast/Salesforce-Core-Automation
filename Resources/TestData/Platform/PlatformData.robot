*** Variables ***
${sandboxUserNameInput}=                        u.saija@launch360.com.test
${sandboxPasswordInput}=                        Umang@2025

# New Work Order Data
${searchWOAccountData}=                         SUN CITY PLANT
${searchWOContactsData}=                        Umang Saija
${searchWOAssetsData}=                          320 Medium Excavator Yoda
${searchWOWorkTypesData}=                       Extreme Weather
${textWOSubjectData}=                           This Work Order was created through an automated process by Umang Saija.
${selectWOPriorityOption}=                      Medium
${selectDateWOEntitlementProcessStartTime}=     10/27/2024
${selectTimeWOEntitlementProcessStartTime}=     11:00 PM
${textWOStreetData}=                            10 Testa Place

# New Product Required Data
${searchPRProductsRequiredData}=                Umang Saija Test Product
${searchPRQuantityRequiredData}=                5
${selectPRQuantityUnitOfMeasureOption}=         Each

# New Service Appointment Data
${textSASubjectData}=                           This Service Appointment was created through an automated process by Umang Saija.
${selectSAStatusOption}=                        None
${textSAUserEmailData}=                         u.saija@astounddigital.com
${selectDateSAEarliestStartPermitted}=          11/27/2024
${selectTimeSAEarliestStartPermitted}=          11:00 PM
${selectDateSADueDate}=                         11/27/2025
${selectTimeSADueDate}=                         11:00 PM

# New Product Requests Data
${selectDatePRNeedByDate}=                      ${{datetime.datetime.now().strftime('%m/%d/%Y')}}
# ${selectDatePRNeedByDate}=    2/16/2025
${selectTimePRNeedByDate}=                      10:00 PM

# New Products Consumed Data
${searchPCProductItemData}=                     PI-0142
${textPCQuantityConsumedData}=                  2

# New Expenses Data
${textEXPAmountData}=                           100
${selectDateEXPTransactionDate}=                11/15/2024
