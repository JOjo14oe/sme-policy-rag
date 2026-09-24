# ============================================================
#  rag-local 守护脚本:长期稳定运行
#  1) Ollama 显存保护变量;2) Ollama 唤醒/僵死重启
#  3) 后端崩溃自动重启,日志实时落盘 data\logs\
#  用法: powershell -ExecutionPolicy Bypass -File scripts\run_server.ps1
#  说明: 本文件必须保持 UTF-8 with BOM,否则 Windows PowerShell 5.1 会按 GBK 误读。
# ============================================================
$ErrorActionPreference = "Continue"
$script:root     = Split-Path -Parent $PSScriptRoot
$script:py       = Join-Path $root "backend\.venv\Scripts\python.exe"
$script:logDir   = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$script:guardLog = Join-Path $logDir "guard.log"
$script:outLog   = Join-Path $logDir "backend.out.log"
$script:errLog   = Join-Path $logDir "backend.err.log"

$env:OLLAMA_MAX_LOADED_MODELS = "2"
if (-not $env:OLLAMA_KEEP_ALIVE) { $env:OLLAMA_KEEP_ALIVE = "10m" }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$script:adoptedLogged = $false

function Guard-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $script:guardLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-Ollama {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Test-Backend {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/system/health" -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Ensure-Ollama {
    if (Test-Ollama) { return $true }
    Guard-Log "Ollama unreachable, waking it up..."
    $stale = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
    if ($stale) {
        Guard-Log "Found unresponsive ollama process, killing for restart"
        $stale | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }
    try { Start-Process "ollama" -ErrorAction SilentlyContinue } catch { }
    $deadline = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $deadline) {
        if (Test-Ollama) { Guard-Log "Ollama recovered"; return $true }
        Start-Sleep -Seconds 3
    }
    try {
        $exe = (Get-Command ollama.exe -ErrorAction SilentlyContinue).Source
        if (-not $exe) { $exe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" }
        if (Test-Path $exe) {
            Guard-Log "Starting serve directly: $exe"
            Start-Process -FilePath $exe -ArgumentList "serve" -WindowStyle Hidden
        }
    } catch { Guard-Log "Failed to start serve: $_" }
    $deadline2 = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $deadline2) {
        if (Test-Ollama) { Guard-Log "Ollama recovered (serve)"; return $true }
        Start-Sleep -Seconds 3
    }
    Guard-Log "WARNING: Ollama still unreachable, open Ollama app manually."
    return $false
}

function Ensure-Models {
    $names = @()
    try { $names = (& ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split "\s+")[0] }) } catch { }
    $needChat  = if ($env:RAG_CHAT_MODEL)  { $env:RAG_CHAT_MODEL }  else { "deepseek-r1:7b" }
    $needEmbed = if ($env:RAG_EMBED_MODEL) { $env:RAG_EMBED_MODEL } else { "bge-m3" }
    $chatBase  = ($needChat  -split ":")[0]
    $embedBase = ($needEmbed -split ":")[0]
    $nameSet = @($names | ForEach-Object { ($_ -split ":")[0] } | Sort-Object -Unique)
    $missing = @()
    if ($nameSet -notcontains $chatBase)  { $missing += $needChat }
    if ($nameSet -notcontains $embedBase) { $missing += $needEmbed }
    if ($missing.Count -gt 0) {
        Guard-Log ("WARNING missing models: " + ($missing -join ", "))
        Guard-Log "Run in another window: ollama pull <model>"
    } else {
        Guard-Log ("Models ready: " + $needChat + " / " + $needEmbed)
    }
}

function Start-BackendLoop {
    $proc = $null
    $backendFail = 0
    $ollamaFail = 0
    while ($true) {
        if (Test-Ollama) { $ollamaFail = 0 }
        else {
            $ollamaFail++
            if ($ollamaFail -ge 2) {
                [void](Ensure-Ollama)
                $ollamaFail = 0
            }
        }
        if ($proc -and $proc.HasExited) {
            Guard-Log "Backend exited (code=$($proc.ExitCode)), restarting in 5s"
            Start-Sleep -Seconds 5
            $proc = $null
            $backendFail = 0
        }
        if (-not $proc) {
            if (Test-Backend) {
                # Port 8000 already serves a healthy backend: adopt it instead of
                # fighting for the port (prevents restart loops).
                if (-not $script:adoptedLogged) {
                    Guard-Log "Backend already running on 8000 (external) - monitoring it"
                    $script:adoptedLogged = $true
                }
                Start-Sleep -Seconds 5
                continue
            }
            Guard-Log "Starting backend: python -m uvicorn app.main:app (http://127.0.0.1:8000)"
            $p = Start-Process -FilePath $script:py `
                -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" `
                -WorkingDirectory (Join-Path $script:root "backend") `
                -RedirectStandardOutput $script:outLog -RedirectStandardError $script:errLog `
                -WindowStyle Hidden -PassThru
            $proc = $p
            Start-Sleep -Seconds 4
        }
        if (Test-Backend) { $backendFail = 0 }
        else {
            $backendFail++
            if ($backendFail -ge 6) {
                Guard-Log "Backend health checks failed repeatedly, force restart"
                try { $proc.Kill() } catch { }
                $proc = $null
                $backendFail = 0
            }
        }
        Start-Sleep -Seconds 5
    }
}

Guard-Log "===== rag-local guard started ====="
Ensure-Ollama
Ensure-Models
try {
    Start-BackendLoop
} finally {
    Guard-Log "Guard stopped"
}