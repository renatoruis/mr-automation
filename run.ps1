#Requires -Version 5.1
<#
.SYNOPSIS
  Bootstrap Windows do KONG MR Generator (wizard).

.DESCRIPTION
  Garante Python 3, instala deps pip, baixa provision.py e corre no cwd atual.
  Uso:
    irm https://mr.timdevops.com.br | iex

  Variáveis opcionais:
    $env:KONG_MR_RAW_BASE       — URL base (sem barra final); default https://mr.timdevops.com.br
    $env:KONG_MR_FORCE_DOWNLOAD — se "1", ignora .\provision.py e baixa da URL
#>

$ErrorActionPreference = "Stop"

$RawBase = if ($env:KONG_MR_RAW_BASE) {
    $env:KONG_MR_RAW_BASE.TrimEnd("/")
} else {
    "https://mr.timdevops.com.br"
}

$PythonUrl = "https://www.python.org/downloads/"
$Deps = @("requests", "PyYAML", "python-dotenv")

function Write-Info([string]$Message) {
    Write-Host "[KONG MR] $Message"
}

function Write-Err([string]$Message) {
    Write-Host "[KONG MR] ERRO: $Message" -ForegroundColor Red
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Find-Python {
    foreach ($cmd in @("python", "python3")) {
        try {
            $p = Get-Command $cmd -ErrorAction Stop
            $ver = & $p.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver -match '^3\.') {
                return $p.Source
            }
        } catch { }
    }
    try {
        $ver = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) { return $ver.Trim() }
    } catch { }
    return $null
}

function Install-PythonWinget {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { return $false }
    Write-Info "Instalando Python via winget..."
    try {
        & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        Refresh-Path
        return $true
    } catch {
        return $false
    }
}

function Install-PythonOfficial {
    Write-Info "Baixando instalador oficial Python 3.12..."
    $installer = Join-Path $env:TEMP "kong-mr-python-installer.exe"
    $uri = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
    try {
        Invoke-WebRequest -Uri $uri -OutFile $installer -UseBasicParsing
        Write-Info "Instalando Python (silent)..."
        $args = "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0 SimpleInstall=1"
        $proc = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
        Refresh-Path
        return ($proc.ExitCode -eq 0)
    } catch {
        return $false
    } finally {
        Remove-Item -Force $installer -ErrorAction SilentlyContinue
    }
}

function Ensure-Python {
    $python = Find-Python
    if ($python) {
        Write-Info "Python: $python"
        return $python
    }

    Write-Info "Python 3 não encontrado. Tentando instalação automática..."
    if (-not (Install-PythonWinget)) {
        [void](Install-PythonOfficial)
    }
    Refresh-Path
    Start-Sleep -Seconds 2
    $python = Find-Python
    if (-not $python) {
        Write-Err "Não foi possível instalar o Python automaticamente."
        Write-Err "Instale manualmente: $PythonUrl"
        exit 1
    }
    Write-Info "Python: $python"
    return $python
}

function Ensure-Deps([string]$PythonExe) {
    Write-Info "Instalando dependências pip..."
    & $PythonExe -m pip install --upgrade pip -q
    & $PythonExe -m pip install -q @Deps
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Falha ao instalar dependências pip ($($Deps -join ', '))"
        exit 1
    }
}

function Get-ProvisionScript {
    if ((Test-Path -LiteralPath ".\provision.py") -and ($env:KONG_MR_FORCE_DOWNLOAD -ne "1")) {
        Write-Info "Usando provision.py local"
        return (Resolve-Path ".\provision.py").Path
    }
    $dest = Join-Path $env:TEMP "kong-mr-provision.py"
    $url = "$RawBase/provision.py"
    Write-Info "Baixando $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    } catch {
        Write-Err "Falha ao baixar $url"
        Write-Err "Para testar localmente: deixe .\provision.py no cwd (ou clone este repo)."
        exit 1
    }
    return $dest
}

# --- main ---
Write-Info "Bootstrap (cwd: $(Get-Location))"
$python = Ensure-Python
Ensure-Deps -PythonExe $python
$script = Get-ProvisionScript
Write-Info "Iniciando wizard..."
& $python $script
exit $LASTEXITCODE
