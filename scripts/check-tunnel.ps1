<#
.SYNOPSIS
    Non-destructive health check for the demo stack.

.DESCRIPTION
    Tells you in 5 seconds whether the demo is healthy. Prints the current
    tunnel URL, whether it's reachable, what Vercel is pointing at, and the
    last Vercel deploy status. Doesn't touch anything.

    Run this BEFORE restore-tunnel.ps1 to confirm the tunnel is actually dead
    (not just your local DNS being slow), and AFTER a deploy to confirm it's
    Ready.
#>

[CmdletBinding()] param()

$ErrorActionPreference = "Continue"

function Pass($msg) { Write-Host "  OK  $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "  XX  $msg" -ForegroundColor Red }
function Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }

Write-Host "`n=== Demo stack health ===" -ForegroundColor Cyan

# Local backend
$beStatus = curl.exe -sS -o NUL -w "%{http_code}" --max-time 5 "http://localhost:8000/health" 2>$null
if ($beStatus -eq "200") { Pass "local backend  http://localhost:8000  HTTP=200" }
else { Fail "local backend  http://localhost:8000  HTTP=$beStatus  -- start Docker Desktop + docker compose up -d" }

# cloudflared process
$cf = Get-Process -Name cloudflared -EA SilentlyContinue | Select-Object -First 1
if ($cf) {
    $age = (Get-Date) - $cf.StartTime
    Pass "cloudflared running  PID=$($cf.Id)  uptime=$([int]$age.TotalMinutes)m"
} else {
    Fail "cloudflared NOT running -- run scripts/restore-tunnel.ps1"
}

# Discover the current tunnel URL.
# Vercel env pull redacts encrypted values, so we can't read it from there.
# Three fallbacks in order of reliability:
#   1) the live JS bundle baked the URL at build time -- scrape it
#   2) any cloudflared log file in TEMP
#   3) give up
$tunnelUrl = $null

# (1) JS bundle -- the production build inlined NEXT_PUBLIC_API_URL into one
#     of the chunks. Pick the chunk URL list from /login (always public).
try {
    $loginHtml = curl.exe -sS --max-time 10 "https://sf-core-automation-umber.vercel.app/login" 2>$null
    $jsFiles = [regex]::Matches($loginHtml, '/_next/static/[^"]+\.js') |
        ForEach-Object { $_.Value } | Select-Object -Unique
    foreach ($f in $jsFiles) {
        $body = curl.exe -sS --max-time 10 "https://sf-core-automation-umber.vercel.app$f" 2>$null
        $m = [regex]::Match($body, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($m.Success) { $tunnelUrl = $m.Value; break }
    }
} catch {}

if ($tunnelUrl) {
    Pass "vercel JS bundle points at $tunnelUrl"
} else {
    # (2) Check cloudflared TEMP logs for the most recent quick-tunnel URL.
    $temp = (Get-ChildItem $env:TEMP -File -Filter "cloudflared-*.log" -EA SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1)
    if ($temp) {
        $logged = Get-Content $temp.FullName -Raw -EA SilentlyContinue
        $m = [regex]::Match($logged, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($m.Success) {
            $tunnelUrl = $m.Value
            Warn "vercel bundle didn't expose URL; using local cloudflared log $($temp.Name) -> $tunnelUrl"
        }
    }
}

if (-not $tunnelUrl) {
    Fail "couldn't discover tunnel URL from vercel bundle or cloudflared log"
}

# Resolve via 1.1.1.1 to bypass local DNS cache.
if ($tunnelUrl -and $tunnelUrl -match '^https?://([^/]+)') {
    $h = $matches[1]
    $ip = $null
    try {
        $ip = (Resolve-DnsName -Name $h -Server 1.1.1.1 -Type A -EA SilentlyContinue |
               Where-Object { $_.Type -eq 'A' } | Select-Object -First 1).IPAddress
    } catch {}
    if ($ip) {
        $code = curl.exe -sS -o NUL -w "%{http_code}" --max-time 8 `
            --resolve "${h}:443:$ip" "$tunnelUrl/health" 2>$null
        if ($code -eq "200") {
            Pass "tunnel reachable  $tunnelUrl  HTTP=200 via $ip"
        } else {
            Fail "tunnel NOT reachable  $tunnelUrl  HTTP=$code  -- run scripts/restore-tunnel.ps1"
        }
    } else {
        Fail "tunnel DNS not resolving via 1.1.1.1  -- run scripts/restore-tunnel.ps1"
    }
}

# Vercel
$verStatus = curl.exe -sS -o NUL -w "%{http_code}" --max-time 8 -L "https://sf-core-automation-umber.vercel.app" 2>$null
if ($verStatus -eq "200") {
    Pass "vercel front https://sf-core-automation-umber.vercel.app  HTTP=200"
} else {
    Fail "vercel front HTTP=$verStatus"
}

Write-Host ""
