📁 Project Structure:

    .
    └── Project Base/
    ├── Tests/
    │   ├── LucyChatbot/
    │   │   └── LucyChatBotTest1.robot <- Test Script (Entire App)
    |   |   └── LucyChatBotTest2.robot <- Test Script (Entire App)
    │   ├── OmsChatBot/
    │   │   └── OmsChatBotTest1.robot <- Test Script (Entire App)
    │   │   └── OmsChatBotTest2.robot <- Test Script (Entire App)
    │   ├── Platform/
    │   │   └── PlatformTest1.robot <- Test Script (Entire App)
    │   │   └── PlatformTest2.robot <- Test Script (Entire App)
    │   └── B2B/
    │       └── B2BTest1.robot <- Test Script (Entire App)
    │       └── B2BTest2.robot <- Test Script (Entire App)
    ├── Resources/
    │   ├── GlobalVariables.robot
    │   ├── GlobalKeywords.robot (Suite Setup, Open Browser, etc)
    │   ├── Common/
    │   │   ├── GlobalVariables.robot
    │   │   ├── GlobalKeywords.robot (Suite Setup, Open Browser, etc)
    │   │   ├── LucyChatBot/
    │   │   │   ├── LucyChatBotCommon.robot
    │   │   ├── OmsChatBot/
    │   │   │   ├── OmsChatBotCommon.robot
    │   │   ├── Platform/
    │   │   │   ├── PlatformCommon.robot
    │   │   └── B2B/
    │   │       └── B2BCommon.robot
    │   ├── TestData/
    │   │   ├── LucyChatBot/
    │   │   │   ├── LucyChatBotData.robot
    │   │   │   └── LucyChatBotEnv.robot  (Contains diff env such as dev, stg, prod)
    │   │   ├── OmsChatBot/
    │   │   │   ├── OmsChatBotData.robot
    │   │   │   └── OmsChatBotEnv.robot (Contains diff env such as dev, stg, prod)       
    │   │   ├── Platform/
    │   │   │   ├── PlatformData.robot
    │   │   │   └── PlatformEnv.robot (Contains diff env such as dev, stg, prod)
    │   │   └── B2B/
    │   │       ├── B2BData.robot
    │   │       └── B2BEnv.robot (Contains diff env such as dev, stg, prod)
    │   ├── PO/
    │   │   ├── LucyChatBot/
    │   │   │   ├── LucyChatBotPageName1PO.robot (page specific locators & keywords)
    │   │   │   └── LucyChatBotPageName2PO.robot (page specific locators & keywords)
    │   │   ├── OmsChatBot/
    │   │   │   ├── OmsBotPageNamePageName1PO.robot (page specific locators & keywords)
    │   │   │   └── OmsBotPageNamePageName2PO.robot (page specific locators & keywords)
    │   │   ├── Platform/
    │   │   │   ├── PlatformPageName1PO.robot (page specific locators & keywords)
    │   │   │   └── PlatformPageName2PO.robot (page specific locators & keywords)
    │   │   └── B2B/
    │   │       ├── B2BPageName1PO.robot (page specific locators & keywords)
    │   │       └── B2BPageName2PO.robot (page specific locators & keywords)
    │   └── CustomLibraries
    ├── LucyChatBot/
    ├── OmsChatBot/
    ├── Platform/
    ├── B2B/
    └── Results/

The project is organized into key directories for efficient test management and execution:

- **Tests/**: Contains high-level test scripts for different applications.
- **Resources/**: Stores common variables, keywords, test data, and Page Object (PO) models.
- **Results/**: Collects the results of test executions for different applications.

### Tests Directory

This folder houses the main test scripts that will execute comprehensive end-to-end test scenarios for each module.

- **LucyChatBot.robot**: Test script for LucyChatBot's full suite.
- **OmsChatBot.robot**: Test script for OmsChatBot’s full suite.
- **Platform.robot**: Test script for Platform’s full suite.
- **B2B.robot**: Test script for B2B’s full suite.

Each of these scripts calls shared resources (like keywords and data) and executes the test flow based on reusable components defined in the `Resources` directory.

### Resources Directory

This directory provides modularized and reusable resources that are consumed by the test scripts in the `Tests/` directory.

#### a. **Global Resources**

- **GlobalVariables.robot**: Stores global variables used across test suites.
- **GlobalKeywords.robot**: Contains globally applicable keywords such as `Suite Setup`, `Open Browser`, and other utility functions.

#### b. **Common Resources**

Each module (LucyChatBot, OmsChatBot, Platform, and B2B) has its own set of common resources:

- **LucyChatBotCommonKeywords.robot**: Contains common keywords for LucyChatBot.
- **OmsChatBotCommonKeywords.robot**: Contains common keywords for OmsChatBot.
- **PlatformCommonKeywords.robot**: Contains common keywords for Platform.
- **B2BCommonKeywords.robot**: Contains common keywords for B2B.

Each of these resources stores frequently used keywords specific to the corresponding application to ensure test script reusability.

#### c. **Test Data**

Test data is organized per module:

- **LucyChatBotData.robot**: Test data for LucyChatBot.
- **OmsChatBotData.robot**: Test data for OmsChatBot.
- **PlatformData.robot**: Test data for Platform.
- **B2BData.robot**: Test data for B2B.

Each module also contains environment-specific test configurations:

- **LucyChatBotEnv.robot**: Contains environment-specific variables (dev, staging, production) for LucyChatBot.
- **OmsChatBotEnv.robot**: Contains environment-specific variables for OmsChatBot.
- **PlatformEnv.robot**: Contains environment-specific variables for Platform.
- **B2BEnv.robot**: Contains environment-specific variables for B2B.

These environment files make the tests flexible and easy to switch between environments without modifying core test logic.

#### d. **Page Objects (PO)**

The Page Object Model (POM) is implemented for each module:

- **LucyChatBotPageNamePO.robot**: Page-specific locators and actions for LucyChatBot.
- **OmsBotPageNamePageNamePO.robot**: Page-specific locators and actions for OmsChatBot.
- **PlatformPageNamePO.robot**: Page-specific locators and actions for Platform.
- **B2BPageNamePO.robot**: Page-specific locators and actions for B2B.

The POM pattern isolates UI interactions into separate files, making the test scripts more maintainable and modular.

#### **e. CustomLibraries**

Custom libraries for advanced functionalities that extend the Robot Framework’s built-in capabilities are stored here.

### **Results Directory**

The results of the test runs are stored in this directory, with separate subfolders for each module:

- **LucyChatBot/**: Contains results for LucyChatBot test executions.
- **OmsChatBot/**: Contains results for OmsChatBot test executions.
- **Platform/**: Contains results for Platform test executions.
- **B2B/**: Contains results for B2B test executions.