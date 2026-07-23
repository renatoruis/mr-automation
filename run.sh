#!/usr/bin/env bash
# Bootstrap Unix (macOS/Linux) do KONG MR Generator (wizard).
#
# Preferir entry único (mesma URL em todos os SO):
#   curl -fsSL https://mr.timdevops.com.br/run | bash
# Direto:
#   curl -fsSL https://mr.timdevops.com.br/run.sh | bash
#
# Variáveis opcionais:
#   KONG_MR_RAW_BASE       — URL base (sem barra final); default https://mr.timdevops.com.br
#   KONG_MR_FORCE_DOWNLOAD — se 1, ignora ./provision.py e baixa da URL

set -euo pipefail

RAW_BASE="${KONG_MR_RAW_BASE:-https://mr.timdevops.com.br}"
RAW_BASE="${RAW_BASE%/}"
PYTHON_URL="https://www.python.org/downloads/"

info() { printf '[KONG MR] %s\n' "$*" >&2; }
err()  { printf '[KONG MR] ERRO: %s\n' "$*" >&2; }

confirm() {
  local prompt="$1"
  local answer
  read -r -p "$prompt [y/N] " answer || true
  case "${answer:-}" in
    y|Y|yes|YES|s|S|sim|SIM) return 0 ;;
    *) return 1 ;;
  esac
}

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' 2>/dev/null; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

install_python() {
  info "Python 3 não encontrado."
  if ! confirm "Tentar instalar automaticamente?"; then
    err "Instale Python 3 manualmente: $PYTHON_URL"
    exit 1
  fi

  if command -v brew >/dev/null 2>&1; then
    info "Instalando via Homebrew..."
    brew install python@3.12 || brew install python3
    return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    info "Instalando via apt-get (sudo)..."
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv
    return 0
  fi
  if command -v dnf >/dev/null 2>&1; then
    info "Instalando via dnf (sudo)..."
    sudo dnf install -y python3 python3-pip
    return 0
  fi
  if command -v yum >/dev/null 2>&1; then
    info "Instalando via yum (sudo)..."
    sudo yum install -y python3 python3-pip
    return 0
  fi

  err "Não foi possível detectar um gerenciador de pacotes suportado."
  err "Instale Python 3 manualmente: $PYTHON_URL"
  exit 1
}

ensure_python() {
  local py
  if py="$(find_python)"; then
    info "Python: $py"
    printf '%s\n' "$py"
    return 0
  fi
  install_python
  if py="$(find_python)"; then
    info "Python: $py"
    printf '%s\n' "$py"
    return 0
  fi
  err "Python 3 continua indisponível após a tentativa de instalação."
  err "Instale manualmente: $PYTHON_URL"
  exit 1
}

ensure_deps() {
  local py="$1"
  info "Instalando dependências pip..."
  "$py" -m pip install --upgrade pip -q
  "$py" -m pip install -q requests PyYAML python-dotenv
}

get_provision_script() {
  # Dev local: se provision.py está no cwd, usar (salvo FORCE_DOWNLOAD=1)
  if [[ -f ./provision.py && "${KONG_MR_FORCE_DOWNLOAD:-}" != "1" ]]; then
    info "Usando provision.py local"
    printf '%s\n' "$(cd "$(dirname ./provision.py)" && pwd)/$(basename ./provision.py)"
    return 0
  fi
  # macOS mktemp exige XXXXXX no fim do template
  local dest
  dest="$(mktemp "${TMPDIR:-/tmp}/kong-mr-provision.XXXXXX")"
  local url="$RAW_BASE/provision.py"
  info "Baixando $url"
  if ! curl -fsSL "$url" -o "$dest"; then
    err "Falha ao baixar $url"
    err "Para testar localmente: deixe ./provision.py no cwd (ou clone este repo)."
    rm -f "$dest"
    exit 1
  fi
  printf '%s\n' "$dest"
}

main() {
  info "Bootstrap (cwd: $(pwd))"
  local py script
  py="$(ensure_python)"
  ensure_deps "$py"
  script="$(get_provision_script)"
  info "Iniciando wizard..."
  exec "$py" "$script"
}

main "$@"
