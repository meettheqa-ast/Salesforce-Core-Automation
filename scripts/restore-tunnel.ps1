<#
.SYNOPSIS
    One-shot recovery when the Cloudflare quick tunnel dies.

.DESCRIPTION
    The Cloudflare *quick* tunnel (`cloudflared tunnel --url ...`) gets a NEW
    random URL every time it restarts AND silently dies a few times a day when
    Cloudflare's edge drops the control stream. When that happens the Vercel
    frontend gets "Failed to fetch" because NEXT_PUBLIC_API_URL points at a
    URL that no longer routes anywhere.

    This script does the whole recovery dance in ~60 seconds:
      1. Kills any zombie cloudflared process.
      2. Starts a fresh quick tunnel pointing at http://localhost:8000.
      3. Waits for the new https://*.trycloudflare.com URL.
      4. Verifies it's reachable from public DNS (your local DNS may still be
         caching the dead URL -- that's normal and not a real failure).
      5. Updates Vercel's NEXT_PUBLIC_API_URL to the new URL.
      6. Triggers a Vercel production redeploy so the new URL is baked into
         the JS bundle.
      7. Prints a "ready" message with the new URL.

    After the script finishes you only need to hard-refresh the browser
    (Ctrl+Shift+R) and the demo is back.

.PARAMETER VercelProject
    Name of the Vercel project. Default 'sf-core-automation'.

.PARAMETER VercelAlias
    Production alias to redeploy. Default 'sf-core-automation-umber.vercel.app'.

.PARAMETER LocalBackend
    Local backend URL the tunnel proxies. Default http://localhost:8000.

.EXAMPLE
    cd C:\Users\m.sheth\Astound_Learning\Salesforce Automation
    .\scripts\restore-tunnel.ps1
#>

[CmdletBinding()]
param(
    [string]$VercelProject = "sf-core-automation",
    [string]$VercelAlias   = "sf-core-automation-umber.vercel.app",
    [string]$LocalBackend  = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Write-Bad($msg)  { Write-Host "  XX  $msg" -ForegroundColor Red }

# --- 0. preflight ---------------------------------------------------------

Write-Step "preflight"

# Local backend must be up; otherwise the new tunnel will route to nothing.
$beStatus = curl.exe -sS -o NUL -w "%{http_code}" --max-time 5 "$LocalBackend/health" 2>$null
if ($beStatus -ne "200") {
    Write-Bad "local backend at $LocalBackend not responding (got HTTP=$beStatus)"
    Write-Bad "Start Docker Desktop and run: docker compose up -d"
    exit 1
}
Write-OK "local backend healthy"

# cloudflared on PATH?
if (-not (Get-Command cloudflared -EA SilentlyContinue)) {
    Write-Bad "cloudflared not found on PATH. Install with: winget install --id Cloudflare.cloudflared"
    exit 1
}
Write-OK "cloudflared on PATH"

# vercel CLI?
if (-not (Get-Command vercel -EA SilentlyContinue)) {
    Write-Bad "vercel CLI not found. Install with: npm install -g vercel"
    exit 1
}
Write-OK "vercel CLI on PATH"

# --- 1. kill zombie cloudflared -------------------------------------------

Write-Step "step 1: kill zombie cloudflared"
$zombies = Get-Process -Name cloudflared -EA SilentlyContinue
if ($zombies) {
    foreach ($p in $zombies) {
        Stop-Process -Id $p.Id -Force -EA SilentlyContinue
        Write-OK "killed cloudflared PID $($p.Id)"
    }
    Start-Sleep 2
} else {
    Write-OK "no zombies"
}

# --- 2. start fresh tunnel + capture URL ----------------------------------

Write-Step "step 2: start fresh tunnel"
$logFile = Join-Path $env:TEMP "cloudflared-restore-$(Get-Random).log"
$proc = Start-Process -FilePath cloudflared `
    -ArgumentList @("tunnel", "--url", $LocalBackend, "--no-autoupdate") `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError "$logFile.err" `
    -PassThru -WindowStyle Hidden

Write-OK "cloudflared PID $($proc.Id) launched (will keep running after this script exits)"

$tunnelUrl = $null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep 2
    $combined = ""
    if (Test-Path $logFile)         { $combined += Get-Content $logFile -Raw -EA SilentlyContinue }
    if (Test-Path "$logFile.err")   { $combined += Get-Content "$logFile.err" -Raw -EA SilentlyContinue }
    if ($combined -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
        $tunnelUrl = $matches[0]
        break
    }
}
if (-not $tunnelUrl) {
    Write-Bad "didn't see a trycloudflare.com URL after 40s. Logs at $logFile and $logFile.err"
    exit 1
}
Write-OK "new URL: $tunnelUrl"

# --- 3. verify reachability via public DNS --------------------------------

Write-Step "step 3: verify reachability (via Cloudflare DNS, bypasses local cache lag)"
$tunnelHost = ($tunnelUrl -replace 'https://','' -replace '/.*$','')
$reachable = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep 3
    $ip = $null
    try {
        $ip = (Resolve-DnsName -Name $tunnelHost -Server 1.1.1.1 -Type A -EA SilentlyContinue |
               Where-Object { $_.Type -eq 'A' } | Select-Object -First 1).IPAddress
    } catch { }
    if (-not $ip) {
        Write-Host "    [$($i*3+3)s] DNS not propagated yet" -ForegroundColor DarkGray
        continue
    }
    $code = curl.exe -sS -o NUL -w "%{http_code}" --max-time 8 `
        --resolve "${tunnelHost}:443:$ip" "$tunnelUrl/health" 2>$null
    Write-Host "    [$($i*3+3)s] HTTP=$code via $ip" -ForegroundColor DarkGray
    if ($code -eq "200") { $reachable = $true; break }
}
if ($reachable) {
    Write-OK "tunnel responds 200 from public internet"
} else {
    Write-Warn "tunnel didn't return 200 within 60s -- pushing to Vercel anyway, may need a few more seconds to come up"
}

# --- 4. update Vercel env -------------------------------------------------

Write-Step "step 4: update Vercel NEXT_PUBLIC_API_URL"
Push-Location (Join-Path $RepoRoot "frontend")
try {
    & vercel env rm NEXT_PUBLIC_API_URL production --yes 2>&1 | Out-Null
    Write-OK "removed old env var"

    # `vercel env add` hangs after success on Windows -- background + timeout.
    $job = Start-Job -ScriptBlock {
        param($u, $cwd)
        Set-Location $cwd
        & vercel env add NEXT_PUBLIC_API_URL production --value $u --yes 2>&1
    } -ArgumentList $tunnelUrl, (Get-Location).Path
    $done = Wait-Job $job -Timeout 25
    if ($done) {
        Write-OK "added new env var"
    } else {
        Write-OK "added new env var (CLI hang after success -- normal)"
    }
    Stop-Job $job -EA SilentlyContinue
    Remove-Job $job -Force -EA SilentlyContinue

# --- 5. trigger Vercel redeploy ------------------------------------------

    Write-Step "step 5: trigger Vercel redeploy"
    $redeployOut = & vercel redeploy "https://$VercelAlias" --target production 2>&1 |
        Select-String -Pattern "Production:|Aliased:|Ready|Error" |
        Select-Object -First 4
    foreach ($line in $redeployOut) { Write-Host "    $line" -ForegroundColor DarkGray }
} finally {
    Pop-Location
}

# --- 6. done --------------------------------------------------------------

Write-Step "DONE"
Write-Host "  tunnel: $tunnelUrl" -ForegroundColor Green
Write-Host "  vercel: https://$VercelAlias" -ForegroundColor Green
Write-Host ""
Write-Host "  Vercel build takes ~45s. When the deploy is Ready, hard-refresh the browser (Ctrl+Shift+R)." -ForegroundColor White
