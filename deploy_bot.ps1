# deploy_bot.ps1
#
# Deploys teams_bot.py (and the current totals.json) to the Azure Web App
# created by setup_azure_bot.ps1.
#
# Run this after setup_azure_bot.ps1, and again whenever you want to push
# an updated version of the bot code.
#
# Usage:
#   .\deploy_bot.ps1 -WebAppName "app-contribution-bot-xxxxxx" -ResourceGroup "rg-contribution-bot"

param(
    [Parameter(Mandatory=$true)]
    [string]$WebAppName,

    [Parameter(Mandatory=$false)]
    [string]$ResourceGroup = "rg-contribution-bot"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$BotDir = $PSScriptRoot
$ZipPath = Join-Path $env:TEMP "contribution-bot-deploy.zip"

Write-Host "=== Deploying contribution bot to Azure ===" -ForegroundColor Cyan

# Write startup command file
$startupContent = "gunicorn --bind=0.0.0.0 --timeout 600 teams_bot:APP"
Set-Content -Path (Join-Path $BotDir "startup.txt") -Value $startupContent

# Write requirements.txt if missing
$reqPath = Join-Path $BotDir "requirements.txt"
if (-not (Test-Path $reqPath)) {
    @"
pandas
openpyxl
playwright
botbuilder-core
botbuilder-schema
aiohttp
gunicorn
"@ | Set-Content $reqPath
}

# Zip the files needed on the server (no Playwright/session capture — server only needs the bot + calc)
Write-Host "Zipping deployment package..." -ForegroundColor Yellow
$filesToDeploy = @(
    "teams_bot.py",
    "contribution_calc.py",
    "totals.json",
    "requirements.txt",
    "startup.txt"
)
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path ($filesToDeploy | ForEach-Object { Join-Path $BotDir $_ }) -DestinationPath $ZipPath
Write-Host "Package ready: $ZipPath" -ForegroundColor Green

# Deploy via zip deploy
Write-Host "Deploying to $WebAppName..." -ForegroundColor Yellow
az webapp deploy `
    --name $WebAppName `
    --resource-group $ResourceGroup `
    --src-path $ZipPath `
    --type zip

# Set startup command
az webapp config set `
    --name $WebAppName `
    --resource-group $ResourceGroup `
    --startup-file "startup.txt"

Write-Host ""
Write-Host "Deployed. Bot endpoint: https://$WebAppName.azurewebsites.net/api/messages" -ForegroundColor Green
Write-Host "Health check:           https://$WebAppName.azurewebsites.net/health" -ForegroundColor Green
