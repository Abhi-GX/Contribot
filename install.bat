@echo off
echo Installing dependencies...
python -m pip install botbuilder-core==4.15.0 botbuilder-schema==4.15.0 botframework-connector==4.15.0 aiohttp --prefer-binary
echo.
echo Verifying...
python check_install.py
pause
