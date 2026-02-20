"""
Gets the path of the file.
file might be exe or zip.
if zip, unzip it into a temp folder. A password might be needed so support getting the password from command line.
if exe, run it.

a typical windows installers has multiple steps,some of the steps are interactive for example accepting the license agreement, choosing the installation path, etc.
We need to automate these steps as well.

the idea is in Microsoft Windows you can fully work with the system using only the keyboard and keyboard shortcuts (without a mouse).
so in each step we should take a screenshot and use OCR to extract the text and data from screenshot and decide which keyboard shortcuts should be used in order.

for OCR and thinking steps we can use Gemini models, the offer a free tier and are very powerful.
    1. extract text from screenshot using OCR
    2. use Gemini to analyze the text and decide which keyboard shortcuts to use
    3. use pyautogui to send the keyboard shortcuts
    
challenges:
1. OCR might not be perfect, a post processing step might be needed
2. The installer might be in a different language, so we need to support multiple languages
3. Return the binary path of the installed software
4. The setup binary might need to be run as administrator, so we need to support that as well
5. This python code might be run in WSL.

sample installer:
- C:\Users\seyyedaliayati\Downloads\Docker Desktop Installer.exe
"""