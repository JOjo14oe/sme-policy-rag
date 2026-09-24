# =====================================================================
#  ci_local.ps1 — 在本地复现 GitHub Actions 的全部检查(Windows)
#  用途:推送前先跑一遍,避免 CI 变红。
#  用法:
#    powershell -ExecutionPolicy Bypass -File scripts\ci_local.ps1
#    powershell -ExecutionPolicy Bypass -File scripts\ci_local.ps1 -Full   # 追加完整回归(需 Ollama)
# =====================================================================
param(
    [switch]$Full
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
$script:fail = 0

function Step($name) { Write-Host "`n=== $name ===" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "  OK  $msg" -ForegroundColor Green }
function Bad($msg)   { Write-Host "  FAIL $msg" -ForegroundColor Red; $script:fail++ }

# ---------------------------------------------------------------- 1) Python
Step "1/4 Python:语法与导入检查"

if (-not (Test-Path $py)) {
    Bad "未找到虚拟环境:$py(请先运行 install.bat)"
} else {
    & $py -m compileall -q "$root\backend\app" "$root\scripts" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "全部 Python 源文件编译通过" } else { Bad "compileall 失败" }

    Push-Location "$root\backend"
    $env:PYTHONIOENCODING = "utf-8"
    $imports = @(
        @("app.main",            "from app.main import app"),
        @("services",            "from app.services import hybrid_retrieval, lexical_index, conflict_service, policy_service, auth_service, audit_service"),
        @("core",                "from app.core import config, db, ollama_client"),
        @("rag",                 "from app.rag import parsers, chunker")
    )
    foreach ($pair in $imports) {
        $label = $pair[0]; $code = $pair[1]
        & $py -c "$code; print('import OK')" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "导入 $label" } else { Bad "导入 $label 失败" }
    }
    Pop-Location
}

# ---------------------------------------------------------------- 2) 前端
Step "2/4 前端:JS 语法与静态资源"
$js = Join-Path $root "backend\web\app.js"
if (Get-Command node -ErrorAction SilentlyContinue) {
    node --check $js 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "app.js 语法通过" } else { Bad "app.js 语法错误" }
} else {
    Write-Host "  SKIP 未安装 node,跳过 JS 语法检查(CI 中会执行)"
}
foreach ($f in @("index.html", "style.css", "app.js")) {
    $p = Join-Path $root "backend\web\$f"
    if ((Test-Path $p) -and ((Get-Item $p).Length -gt 0)) { Ok "静态资源 $f" } else { Bad "缺少或为空:backend\web\$f" }
}

# ---------------------------------------------------------------- 3) 卫生
Step "3/4 仓库卫生(私有数据 / 个人绝对路径)"
$gitOk = $true
try { git --version 2>&1 | Out-Null } catch { $gitOk = $false }
if (-not $gitOk) {
    Write-Host "  SKIP 未安装 git,跳过基于已跟踪文件的检查"
} else {
    $tracked = git -C $root ls-files 2>&1
    $badData = $tracked | Select-String -Pattern '(^|/)(data|\.venv|venv|__pycache__)/'
    if ($badData) { Bad "以下私有数据/缓存已被纳入版本控制:`n$($badData -join "`n")" } else { Ok "无私有数据入库" }

    $badExt = $tracked | Select-String -Pattern '\.(log|db|sqlite3|pyc)$'
    if ($badExt) { Bad "以下日志/数据库/缓存文件已入库:`n$($badExt -join "`n")" } else { Ok "无日志/数据库文件入库" }

    $pathHits = git -C $root grep -nI -E '[A-Za-z]:\\{1,2}Users\\{1,2}[^\\/[:space:]]+|/(home|Users)/[A-Za-z0-9._-]+/' `
        -- . ':!*.docx' ':!*.pptx' ':!*.pdf' ':!scripts/sanitize_evidence.py' 2>&1
    if ($LASTEXITCODE -eq 0 -and $pathHits) {
        Bad "检测到个人绝对路径:`n$($pathHits -join "`n")`n  处理:python scripts\sanitize_evidence.py --all"
    } else { Ok "无个人绝对路径" }

    $untracked = git -C $root status --porcelain 2>&1 | Select-String -Pattern '^\?\?' | Select-Object -First 10
    if ($untracked) { Write-Host "  提示:存在未跟踪文件(如需提交请 git add):" -ForegroundColor Yellow; $untracked | ForEach-Object { Write-Host "    $_" } }
}

# ---------------------------------------------------------------- 4) 关键文件
Step "4/4 关键文件与文档存在性"
foreach ($f in @("README.md", "LICENSE", ".gitignore", ".gitattributes", "CHANGELOG.md",
                 "CONTRIBUTING.md", "SECURITY.md", "backend\requirements.txt", "start.bat", "install.bat")) {
    $p = Join-Path $root $f
    if ((Test-Path $p) -and ((Get-Item $p).Length -gt 0)) { Ok $f } else { Bad "缺少关键文件:$f" }
}

# ---------------------------------------------------------------- 可选:完整回归
if ($Full) {
    Step "附加:完整回归(需 Ollama 与本地模型,且服务已启动)"
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/system/health" -TimeoutSec 5
        if ($h.ok) { Ok "后端在线" } else { Bad "后端未就绪" }
    } catch { Bad "后端不可达,请先运行 start.bat" }

    foreach ($s in @("smoke_test.py", "verify_innovations.py", "verify_productization.py", "eval_retrieval.py")) {
        Write-Host "  -- $s" -ForegroundColor DarkGray
        & $py (Join-Path $root "scripts\$s") 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host "     $_" }
    }
}

# ---------------------------------------------------------------- 汇总
Write-Host ""
if ($script:fail -eq 0) {
    Write-Host "==== 本地 CI 全部通过,可以安全推送 ====" -ForegroundColor Green
    exit 0
} else {
    Write-Host "==== 本地 CI 有 $script:fail 项未通过,请修复后再推送 ====" -ForegroundColor Red
    exit 1
}
