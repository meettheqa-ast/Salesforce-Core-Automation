<h1 align="center" id="title">L360 Salesforce Core Automation</h1>

<p id="description">This project is designed to automate testing for salesforce core using Robot Framework and Selenium. The primary goal is to ensure that the application behaves as expected under various scenarios providing a seamless user experience.</p>

<h2>📌 Documentation:</h2>
<a href="https://github.com/Astound-Digital/L360-Salesforce-Core-Automation/blob/main/Documentation/Project%20Details.md">To Learn About Project Structure</a>
<br><br>
Visit https://salesorce-core-keywords.pages.dev/ to know more about the Salesforce Core Keywords Usage.

<h2>🛠️ Installation Steps:</h2>

<p>1. Ensure you have Python installed on your machine.</p>

<p>2. Setup your Project</p>

```
git clone https://github.com/Astound-Digital/L360-Salesforce-Core-Automation
```

<p>3. Navigate to the project directory</p>

```
cd L360-Salesforce-Core-Automation
```

<p>4. Create a new venv in your project root</p> (Do not use <> in the console)

```
python3 -m venv <myenvname> OR python -m venv <venv> 
```

<p>5. Activate the venv </p>

```
venv/Scripts/Activate.ps1
```

<p>6. Run the command below in the project root of the virtual environment (venv) to install all required libraries and dependencies</p>

```
pip install -r requirements.txt
```

<p>7. Create the following folder and subfolders under the Project Base directory</p>

    .
    └── Results/
        ├── LucyChatBot/
        ├── OmsChatBot/
        ├── Platform/
        └── B2B/

<p>8. Inorder to use Allure reports follow the below steps:</p>
<p>&emsp;&emsp;Run the below commands in Powershell<p>
<samp>&emsp;&emsp;a. iwr -useb get.scoop.sh | iex </samp><br>
<samp>&emsp;&emsp;b. Set-ExecutionPolicy RemoteSigned -scope CurrentUser (if you're facing error for first command) </samp>
<samp>&emsp;&emsp;c. scoop install allure</samp><br>
<samp>&emsp;&emsp;d. allure --version</samp>
<p>&emsp;&emsp;Run the below commands after installation<p>
<samp>&emsp;&emsp;a. In Venv: pip install allure-robotframework </samp><br>
<samp>&emsp;&emsp;b. Add --listener allure_robotframework when you execute your test cases </samp><br>
<samp>&emsp;&emsp;c. allure generate output/allure </samp><br>
<samp>&emsp;&emsp;d. allure open </samp><br>

<h2>🗒️ Naming Conventions:</h2>

1. **Folders and File Names:**

   Use **PascalCase** for folders and file names.

2. **Test-Specific Files (in Tests folder):**

   Place Test files in their respective folders

   Example:

    - `LucyChatBotTest1.robot`
    - `OmsChatBotTest1.robot`
    - `PlatformTest1.robot`
    - `B2BTest1.robot`
3. **Page-Specific Files (in PO folder):**

   Add PO as a **suffix** at the end of PO files.

   Example:

    - `LucyChatBotPageName1PO.robot`
    - `OmsBotPageName1PO.robot`
    - `PlatformPageName1PO.robot`
    - `B2BPageName1PO.robot`
4. **Variables:**
   Use **camelCase** for variables.

   Example:

    - `chatBotStatus`
    - `userData`
5. **Keyword Names:**
   Use meaningful names, starting with a **verb** to describe the action.

   Example:

    - `Open LucyChatBot`
    - `Enter Login Details`
    - `Verify user is logged in successfully`
6. **Test Names:**
   Test names should clearly match the test cases they represent.
   Example:
    - `Verify user can successfully log in to Lucy chatbot`
    - `Verify availability of the Main Menu in Lucy chatbot`
    - `Verify availability of the Warranties submenu`

<h2>🎯 Contribution Guidelines</h2>

<h4>Please follow the guidelines below:</h4>

1. **Create a New Branch**  
    Create a new branch for your test case or bug fix. Use descriptive names for your branches, e.g., `<Prefix>-tc1` or `bugfix/fix-chatbot-button`.
2. **Make Your Changes**  
    Implement your changes, ensuring that you follow the coding style and project conventions. Add comments where necessary to explain complex logic.
3. **Test Your Changes**  
   Run tests to verify that your changes work as expected. Ensure that all existing tests pass.
4. **Submit a Pull Request**  
   Once you’re satisfied with your changes, submit a pull request to the dev branch. Provide a clear description of your changes and any relevant details.
5. **Review Process**  
   Your pull request will be reviewed by the maintainers. Be open to feedback and make any necessary adjustments.
6.  **Code of Conduct**  
    Please adhere to our Code of Conduct in all your interactions. Be respectful and inclusive to all contributors.

Thank you for contributing to the project! Your efforts are greatly appreciated.

