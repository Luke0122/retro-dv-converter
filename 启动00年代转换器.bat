@echo off
cd /d "%~dp0"
if defined PYTHONW (
  start "" "%PYTHONW%" "%~dp0server.py"
  exit /b
)
if exist "%USERPROFILE%\miniconda3\pythonw.exe" (
  start "" "%USERPROFILE%\miniconda3\pythonw.exe" "%~dp0server.py"
  exit /b
)
if exist "%USERPROFILE%\miniconda3\python.exe" (
  start "" "%USERPROFILE%\miniconda3\python.exe" "%~dp0server.py"
  exit /b
)
if exist "%USERPROFILE%\anaconda3\pythonw.exe" (
  start "" "%USERPROFILE%\anaconda3\pythonw.exe" "%~dp0server.py"
  exit /b
)
if exist "%USERPROFILE%\anaconda3\python.exe" (
  start "" "%USERPROFILE%\anaconda3\python.exe" "%~dp0server.py"
  exit /b
)
if exist "C:\ProgramData\miniconda3\pythonw.exe" (
  start "" "C:\ProgramData\miniconda3\pythonw.exe" "%~dp0server.py"
  exit /b
)
if exist "C:\ProgramData\anaconda3\pythonw.exe" (
  start "" "C:\ProgramData\anaconda3\pythonw.exe" "%~dp0server.py"
  exit /b
)
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe") do (
  if exist %%P (
    start "" "%%~P" "%~dp0server.py"
    exit /b
  )
)
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 "%~dp0server.py"
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0server.py"
  exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
  start "" python "%~dp0server.py"
  exit /b
)
echo Python not found (miniconda/pythonw/pyw/python). Please install Python 3.11+ and try again.
pause
