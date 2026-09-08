# setup_azure_bot.ps1
#
# Run this ONCE to register the bot in Azure.
# Requires: Azure CLI (https://aka.ms/installazurecliwindows) + az login
#
# Usage:
#   .\setup_azure_bot.ps1 -ResourceGroup "rg-contribution-bot" -Location "eastus"
#
# After this runs it prints the App ID and App Password you need to set
# as environment variables on the host running teams_bot.py.

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroup = "rg-contribution-bot",

    [Parameter(Mandatory=$false)]
    [string]$Location = "eastus",

    [Parameter(Mandatory=$false)]
    [string]$BotName = "campus-junior-contribution-bot",

    [Parameter(Mandatory=$false)]
    [string]$AppServicePlan = "plan-contribution-bot",

    [Parameter(Mandatory=$false)]
    [string]$WebAppName = "app-contribution-bot-$(-join ((65..90) + (97..122) | Get-Random -Count 6 | % {[char]$_}))"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== Campus Junior Training Bot — Azure Setup ===" -ForegroundColor Cyan
Write-Host ""

# 1. Check az login
Write-Host "Checking Azure login..." -ForegroundColor Yellow
$account = az account show 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not logged in. Running az login..." -ForegroundColor Yellow
    az login
}
$accountInfo = az account show | ConvertFrom-Json
Write-Host "Using subscription: $($accountInfo.name) ($($accountInfo.id))" -ForegroundColor Green

# 2. Create resource group
Write-Host "`nCreating resource group '$ResourceGroup' in '$Location'..." -ForegroundColor Yellow
az group create --name $ResourceGroup --location $Location | Out-Null
Write-Host "Resource group ready." -ForegroundColor Green

# 3. Create the App Registration (gets us App ID + secret)
Write-Host "`nCreating Azure AD App Registration for the bot..." -ForegroundColor Yellow
$appCreate = az ad app create --display-name $BotName --sign-in-audience "AzureADMultipleOrgs" | ConvertFrom-Json
$appId = $appCreate.appId
Write-Host "App Registration created. App ID: $appId" -ForegroundColor Green

$secretResult = az ad app credential reset --id $appId --append | ConvertFrom-Json
$appPassword = $secretResult.password
Write-Host "Client secret created." -ForegroundColor Green

# 4. Create a service principal for the app
Write-Host "`nCreating service principal..." -ForegroundColor Yellow
az ad sp create --id $appId | Out-Null
Write-Host "Service principal created." -ForegroundColor Green

# 5. Create App Service Plan + Web App (to host teams_bot.py)
Write-Host "`nCreating App Service Plan '$AppServicePlan' (Free tier)..." -ForegroundColor Yellow
az appservice plan create `
    --name $AppServicePlan `
    --resource-group $ResourceGroup `
    --sku F1 `
    --is-linux | Out-Null
Write-Host "App Service Plan created." -ForegroundColor Green

Write-Host "`nCreating Web App '$WebAppName'..." -ForegroundColor Yellow
az webapp create `
    --name $WebAppName `
    --resource-group $ResourceGroup `
    --plan $AppServicePlan `
    --runtime "PYTHON:3.11" | Out-Null
$webAppUrl = "https://$WebAppName.azurewebsites.net"
Write-Host "Web App created at: $webAppUrl" -ForegroundColor Green

# 6. Set env vars on the web app
Write-Host "`nConfiguring environment variables on Web App..." -ForegroundColor Yellow
az webapp config appsettings set `
    --name $WebAppName `
    --resource-group $ResourceGroup `
    --settings `
        MicrosoftAppId=$appId `
        MicrosoftAppPassword=$appPassword `
        TOTALS_JSON_PATH="/home/site/wwwroot/totals.json" | Out-Null
Write-Host "Environment variables set." -ForegroundColor Green

# 7. Create the Azure Bot resource wired to the web app
Write-Host "`nCreating Azure Bot '$BotName'..." -ForegroundColor Yellow
az bot create `
    --resource-group $ResourceGroup `
    --name $BotName `
    --kind registration `
    --appid $appId `
    --password $appPassword `
    --endpoint "$webAppUrl/api/messages" | Out-Null
Write-Host "Azure Bot created." -ForegroundColor Green

# 8. Enable Teams channel
Write-Host "`nEnabling Microsoft Teams channel..." -ForegroundColor Yellow
az bot msteams create `
    --name $BotName `
    --resource-group $ResourceGroup | Out-Null
Write-Host "Teams channel enabled." -ForegroundColor Green

# 9. Grant Graph API permission User.Read.All (needed by TeamsInfo.get_member)
Write-Host "`nGranting Graph API permission User.Read.All..." -ForegroundColor Yellow
# Microsoft Graph service principal App ID is always 00000003-0000-0000-c000-000000000000
$graphAppId = "00000003-0000-0000-c000-000000000000"
$graphSp = az ad sp show --id $graphAppId | ConvertFrom-Json
# User.Read.All scope ID
$userReadAllId = ($graphSp.appRoles | Where-Object { $_.value -eq "User.Read.All" }).id
az ad app permission add `
    --id $appId `
    --api $graphAppId `
    --api-permissions "${userReadAllId}=Role" | Out-Null
Write-Host "Permission added. An Azure AD admin must still grant admin consent at:" -ForegroundColor Yellow
Write-Host "  https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/$appId" -ForegroundColor Cyan

# 10. Update manifest.json with real App ID
Write-Host "`nUpdating teams-app-package/manifest.json with real App ID..." -ForegroundColor Yellow
$manifestPath = Join-Path $PSScriptRoot "teams-app-package\manifest.json"
if (Test-Path $manifestPath) {
    (Get-Content $manifestPath) -replace "REPLACE_WITH_AZURE_BOT_APP_ID", $appId | Set-Content $manifestPath
    Write-Host "manifest.json updated." -ForegroundColor Green
}

# 11. Print summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "           SETUP COMPLETE               " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "App ID:          $appId" -ForegroundColor White
Write-Host "App Password:    $appPassword" -ForegroundColor White
Write-Host "Web App URL:     $webAppUrl" -ForegroundColor White
Write-Host "Bot Name:        $BotName" -ForegroundColor White
Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "1. Deploy teams_bot.py + totals.json to the Web App (run deploy_bot.ps1)" -ForegroundColor White
Write-Host "2. Grant admin consent for User.Read.All in the Azure Portal link above" -ForegroundColor White
Write-Host "3. Zip and upload teams-app-package\ to Teams Developer Portal" -ForegroundColor White
Write-Host ""
Write-Host "SAVE THESE CREDENTIALS — the password is not shown again:" -ForegroundColor Red
Write-Host "  MicrosoftAppId=$appId"
Write-Host "  MicrosoftAppPassword=$appPassword"
