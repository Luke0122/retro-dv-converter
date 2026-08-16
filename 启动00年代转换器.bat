@echo off
cd /d "%~dp0"
if defined PYTHONW (
  start "" "%PYTHONW%" "%~dp0server.py"
  exit /b
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
for %%P in ("%USERPROFILE%\miniconda3\pythonw.exe" "%USERPROFILE%\anaconda3\pythonw.exe" "C:\ProgramData\miniconda3\pythonw.exe" "C:\ProgramData\anaconda3\pythonw.exe" "%LOCALAPPDATA%\Programs\Python\Python3*\pythonw.exe") do (
  if exist %%P (
    start "" "%%~P" "%~dp0server.py"
    exit /b
  )
)
echo ??? Python?pythonw/pyw/python????? Python 3.11+ ????
pause
