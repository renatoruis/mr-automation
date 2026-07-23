#!/usr/bin/env python3
"""
KONG MR Generator CLI — wizard que provisiona API num repositório GitOps via MR.

Uso (wizard — caminho feliz):
  1. Coloque o OpenAPI 3 (.json) na pasta atual
  2. Execute o bootstrap OU: python provision.py
       Windows:  irm https://mr.timdevops.com.br | iex
       mac/linux: curl -fsSL https://mr.timdevops.com.br/run | bash

  Na 1ª execução pede ADO PAT + URL do repositório GitOps e salva em:
    Windows: %USERPROFILE%\\.kong-mr-generator\\credentials
    Unix:    ~/.kong-mr-generator/credentials

  Wizard (menus numerados): ambiente, preset, server URL, tags.
  Defaults: ENVIRONMENT=dev, PRESET=legacy-api

Overrides (CI / avançado): ADO_PAT, ADO_REPO_URL / REPO_URL, OPENAPI_FILE,
ENVIRONMENT, PRESET, KONG_NAME, ADO_ORG, ADO_PROJECT, ADO_REPO_NAME,
ADO_REPO_ID, ADO_TARGET_BRANCH, GIT_USER_NAME, GIT_USER_EMAIL

Aceita apenas OpenAPI 3.x (campo "openapi"). Swagger 2 não é convertido.
Idioma da interface: pt-BR.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

try:
    import requests
    import yaml
except ImportError as exc:
    raise SystemExit(
        "Erro: dependências ausentes (requests, PyYAML). "
        "Use run.ps1 / run.sh ou: pip install requests PyYAML python-dotenv"
    ) from exc

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None  # type: ignore[assignment,misc]

APP_NAME = "KONG MR Generator"
MR_TITLE_PREFIX = f"[{APP_NAME}]"
IMPORT_TAG = "importacao:automacao-openapi-ds"
KONG_NAME_REGEX = re.compile(r"^[a-zA-Z0-9._-]+$")
ADO_GIT_URL_RE = re.compile(
    r"^https://dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/?#]+)/?$",
    re.IGNORECASE,
)
PRD_BACKEND_IDS = ("pc", "pr", "p2", "pd")
VALID_ENVS = ("dev", "hml", "prd")
VALID_PRESETS = ("legacy-api", "standard-api", "auth-api", "wsdl-proxy")
CONFIG_DIR_NAME = ".kong-mr-generator"
CREDENTIALS_FILE = "credentials"
DEFAULT_PRESET = "legacy-api"
DEFAULT_GIT_USER_NAME = "KONG MR Generator Bot"
DEFAULT_GIT_USER_EMAIL = "kong-mr-generator@users.noreply.github.com"

ENV_OPTIONS: list[tuple[str, str]] = [
    ("dev", "dev - Desenvolvimento"),
    ("hml", "hml - Homologacao"),
    ("prd", "prd - Producao"),
]
PRESET_OPTIONS: list[tuple[str, str]] = [
    ("legacy-api", "legacy-api - API REST normal (maioria dos casos)"),
    ("standard-api", "standard-api - API com limites e CORS"),
    ("auth-api", "auth-api - API com login JWT"),
    ("wsdl-proxy", "wsdl-proxy - Servico SOAP legado"),
]
# Nota: labels sem acentos/em-dash para consoles Windows cp1252 (evita fechar o host).

# ANSI (PowerShell 7+ / Windows Terminal / macOS / Linux). Desliga com NO_COLOR=1.
_S_RESET = "\033[0m"
_S_BOLD = "\033[1m"
_S_DIM = "\033[2m"
_S_CYAN = "\033[36m"
_S_GREEN = "\033[32m"
_S_YELLOW = "\033[33m"
_S_RED = "\033[31m"
_S_BLUE = "\033[94m"
_S_MAGENTA = "\033[35m"
_COLOR_ENABLED = False


# ---------------------------------------------------------------------------
# Terminal UI
# ---------------------------------------------------------------------------


def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def init_ui() -> None:
    global _COLOR_ENABLED
    _enable_windows_ansi()
    # Windows legado (cp1252) quebra com Unicode; força UTF-8 quando possível.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    no_color = bool(os.environ.get("NO_COLOR", "").strip())
    _COLOR_ENABLED = (
        not no_color
        and os.environ.get("TERM", "") != "dumb"
        and sys.stdout.isatty()
    )


def paint(text: str, *styles: str) -> str:
    if not _COLOR_ENABLED or not styles:
        return text
    return f"{''.join(styles)}{text}{_S_RESET}"


def clear_screen() -> None:
    if not sys.stdout.isatty():
        return
    print("\033[2J\033[H", end="", flush=True)


def ui_blank(lines: int = 1) -> None:
    print("\n" * max(lines, 0), end="")


def ui_banner() -> None:
    clear_screen()
    ui_blank(1)
    print(paint(f"  {APP_NAME}", _S_BOLD, _S_CYAN))
    print(paint("  OpenAPI -> GitOps -> Merge Request", _S_DIM))
    print(paint("  " + ("-" * 44), _S_DIM))
    ui_blank(1)


def ui_step(title: str) -> None:
    """Limpa a tela e mostra o cabeçalho do passo atual do wizard."""
    clear_screen()
    ui_blank(1)
    print(paint(f"  {APP_NAME}", _S_DIM, _S_CYAN))
    print(paint(f"  > {title}", _S_BOLD, _S_BLUE))
    print(paint("  " + ("-" * 44), _S_DIM))
    ui_blank(1)


def ui_info(message: str) -> None:
    print(paint("  > ", _S_CYAN) + message)


def ui_ok(message: str) -> None:
    print(paint("  * ", _S_GREEN, _S_BOLD) + message)


def ui_warn(message: str) -> None:
    print(paint("  ! ", _S_YELLOW, _S_BOLD) + message)


def ui_err(message: str) -> None:
    print(paint("  x ", _S_RED, _S_BOLD) + message, file=sys.stderr)


def ui_muted(message: str) -> None:
    print(paint(f"  {message}", _S_DIM))


def ui_prompt(label: str) -> str:
    return input(paint(f"  {label}", _S_BOLD, _S_CYAN) + " ")


def pause_before_exit() -> None:
    """Evita fechar a janela do terminal antes de o usuário ler a mensagem/URL."""
    if not sys.stdin.isatty():
        return
    try:
        ui_blank(1)
        input(paint("  Pressione Enter para sair...", _S_DIM))
    except EOFError:
        pass


def die(message: str, code: int = 1) -> None:
    """Erro fatal (a pausa fica no handler de SystemExit em __main__)."""
    try:
        ui_blank(1)
        ui_err(message)
    except Exception:
        print(message, file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# Config / wizard
# ---------------------------------------------------------------------------


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def config_dir() -> Path:
    return Path.home() / CONFIG_DIR_NAME


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILE


def read_key_value_file(path: Path) -> dict[str, str]:
    if dotenv_values is not None:
        raw = dotenv_values(path)
        return {k: (v or "").strip() for k, v in raw.items() if k}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip("'\"")
    return out


def save_credentials(
    *,
    pat: str | None = None,
    repo_url: str | None = None,
) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = credentials_path()
    existing = read_key_value_file(path) if path.is_file() else {}
    if pat is not None:
        existing["ADO_PAT"] = pat
    if repo_url is not None:
        existing["ADO_REPO_URL"] = repo_url
    lines = [f"{key}={value}" for key, value in existing.items() if value]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def parse_ado_git_url(url: str) -> dict[str, str]:
    """Parse https://dev.azure.com/{org}/{project}/_git/{repo}."""
    cleaned = url.strip().rstrip("/")
    match = ADO_GIT_URL_RE.match(cleaned)
    if not match:
        raise SystemExit(
            "Erro: URL do repositório inválida.\n"
            "Use o formato:\n"
            "  https://dev.azure.com/{org}/{project}/_git/{repo}"
        )
    org = unquote(match.group(1))
    project = unquote(match.group(2))
    repo_name = unquote(match.group(3))
    return {
        "org": org,
        "project": project,
        "repo_name": repo_name,
        "repo_url": f"https://dev.azure.com/{org}/{project}/_git/{repo_name}",
    }


def ensure_pat() -> str:
    pat = env("ADO_PAT")
    if pat:
        return pat

    path = credentials_path()
    if path.is_file():
        stored = read_key_value_file(path).get("ADO_PAT", "").strip()
        if stored:
            return stored

    ui_step("Personal Access Token")
    ui_info("É necessário um PAT do Azure DevOps")
    ui_muted("Scopes: Code Read & Write + Pull Request Read & Write")
    ui_blank(1)
    try:
        pat = getpass.getpass("  ADO PAT: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("Cancelado.") from exc
    if not pat:
        raise SystemExit("Erro: PAT vazio.")

    saved = save_credentials(pat=pat)
    ui_blank(1)
    ui_ok(f"Credenciais salvas em {saved}")
    ui_blank(1)
    return pat


def _stored_repo_url() -> str:
    forced = env("ADO_REPO_URL") or env("REPO_URL")
    if forced:
        return forced
    path = credentials_path()
    if path.is_file():
        return read_key_value_file(path).get("ADO_REPO_URL", "").strip()
    return ""


def ensure_repo() -> dict[str, str]:
    """Org/project/repo a partir da URL GitOps (wizard ou credentials/env)."""
    # Overrides CI explícitos (sem defaults de empresa)
    org = env("ADO_ORG")
    project = env("ADO_PROJECT")
    repo_name = env("ADO_REPO_NAME")
    if org and project and repo_name:
        return {
            "org": org,
            "project": project,
            "repo_name": repo_name,
            "repo_url": (
                env("ADO_REPO_URL")
                or env("REPO_URL")
                or f"https://dev.azure.com/{org}/{project}/_git/{repo_name}"
            ),
        }

    stored = _stored_repo_url()
    if stored:
        parsed = parse_ado_git_url(stored)
        ui_step("Repositório GitOps")
        ui_info(parsed["repo_url"])
        ui_blank(1)
        keep = ui_prompt("Usar este repositório? [Y/n]").strip().lower()
        if keep in ("", "y", "yes", "s", "sim"):
            return parsed

    ui_step("Repositório GitOps")
    ui_info("Cole a URL do repositório no Azure DevOps")
    ui_muted("ex.: https://dev.azure.com/{org}/{project}/_git/{repo}")
    ui_blank(1)
    try:
        typed = ui_prompt("URL:").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("Cancelado.") from exc
    if not typed:
        raise SystemExit("Erro: URL do repositório vazia.")

    parsed = parse_ado_git_url(typed)
    saved = save_credentials(repo_url=parsed["repo_url"])
    ui_blank(1)
    ui_ok(f"URL salva em {saved}")
    ui_blank(1)
    return parsed


def _looks_like_openapi3_json(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return False
        return str(data.get("openapi", "")).startswith("3.")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def discover_openapi_json(cwd: Path | None = None) -> str:
    forced = env("OPENAPI_FILE")
    if forced:
        return str(Path(forced).expanduser().resolve())

    root = cwd or Path.cwd()
    candidates = sorted(
        p
        for p in root.glob("*.json")
        if p.is_file() and _looks_like_openapi3_json(p)
    )
    if not candidates:
        die(
            f"Erro: nenhum OpenAPI 3 (.json) encontrado em {root}.\n"
            "  Coloque o arquivo OpenAPI na pasta atual e execute novamente."
        )
    if len(candidates) == 1:
        return str(candidates[0].resolve())

    ui_step("Arquivo OpenAPI")
    chosen = prompt_choice(
        "Varios arquivos OpenAPI 3 encontrados - qual usar?",
        [(str(p.resolve()), p.name) for p in candidates],
        default_index=0,
        clear=False,
    )
    return chosen


def prompt_choice(
    title: str,
    options: list[tuple[str, str]],
    *,
    default_index: int = 0,
    clear: bool = True,
    step_title: str | None = None,
) -> str:
    """Menu numerado (dropdown de terminal — funciona em bash e PowerShell)."""
    if not options:
        raise SystemExit("Erro: nenhuma opção disponível.")
    if not (0 <= default_index < len(options)):
        default_index = 0

    if clear:
        ui_step(step_title or title)
    print(paint(f"  {title}", _S_BOLD))
    ui_blank(1)
    for i, (_value, label) in enumerate(options, start=1):
        marker = (
            paint("  * ", _S_GREEN)
            if i - 1 == default_index
            else paint("  - ", _S_DIM)
        )
        suffix = (
            paint("  <- default", _S_DIM, _S_GREEN) if i - 1 == default_index else ""
        )
        num = paint(f"{i})", _S_BOLD, _S_CYAN)
        print(f"{marker}{num}  {label}{suffix}")
    ui_blank(1)
    while True:
        raw = ui_prompt(f"Escolha [1-{len(options)}] [{default_index + 1}]:").strip()
        if not raw:
            return options[default_index][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        ui_warn("Opcao invalida - tente de novo.")
        ui_blank(1)


def prompt_environment() -> str:
    """Ambiente do wizard. Ignora ENVIRONMENT inválido do shell (ex. aws-prd)."""
    forced = env("ENVIRONMENT").lower()
    if forced in VALID_ENVS:
        return forced
    return prompt_choice(
        "Em qual ambiente esta API vai rodar?",
        ENV_OPTIONS,
        default_index=0,
        step_title="Ambiente",
    )


def prompt_preset() -> str:
    forced = env("PRESET")
    if forced in VALID_PRESETS:
        return forced
    default_index = (
        VALID_PRESETS.index(DEFAULT_PRESET)
        if DEFAULT_PRESET in VALID_PRESETS
        else 0
    )
    return prompt_choice(
        "Que tipo de API é? (preset Kong)",
        PRESET_OPTIONS,
        default_index=default_index,
        step_title="Preset",
    )


def _server_urls_from_spec(spec: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for server in spec.get("servers") or []:
        if not isinstance(server, dict):
            continue
        url = (server.get("url") or "").strip().rstrip("/")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def prompt_server_url(spec: dict[str, Any]) -> str:
    """Escolhe servers[].url do OpenAPI ou permite informar manualmente."""
    existing = _server_urls_from_spec(spec)
    options: list[tuple[str, str]] = [
        (url, url) for url in existing
    ]
    options.append(("__custom__", "Informar URL manualmente"))
    if existing:
        options.append(("__empty__", "Sem upstream (deixar vazio)"))

    choice = prompt_choice(
        "Qual é o endereço do serviço backend (server URL)?",
        options,
        default_index=0,
        step_title="Server URL",
    )
    if choice == "__empty__":
        return ""
    if choice == "__custom__":
        ui_blank(1)
        while True:
            typed = ui_prompt("URL do server:").strip().rstrip("/")
            if typed:
                return typed
            ui_warn("URL vazia - informe um endereco.")
            ui_blank(1)
    return choice


def prompt_tags(suggested: list[str]) -> list[str]:
    """Confirma tags com menu (manter / adicionar / remover / substituir)."""
    tags = normalize_tags(list(suggested))
    first = True
    while True:
        if first:
            ui_step("Tags")
            first = False
        else:
            clear_screen()
            ui_blank(1)
            print(paint(f"  {APP_NAME}", _S_DIM, _S_CYAN))
            print(paint("  Tags", _S_BOLD, _S_BLUE))
            print(paint("  " + ("-" * 44), _S_DIM))
            ui_blank(1)

        print(paint("  Tags da API no Kong", _S_BOLD))
        ui_blank(1)
        if tags:
            for i, tag in enumerate(tags, start=1):
                print(paint(f"    {i}. ", _S_DIM) + paint(tag, _S_MAGENTA))
        else:
            ui_muted("(nenhuma)")
        ui_blank(1)
        action = prompt_choice(
            "O que deseja fazer com as tags?",
            [
                ("keep", "Manter estas tags"),
                ("add", "Adicionar tag"),
                ("remove", "Remover tag"),
                ("replace", "Substituir todas (separadas por vírgula)"),
            ],
            default_index=0,
            clear=False,
        )
        if action == "keep":
            return normalize_tags(tags)
        if action == "add":
            ui_blank(1)
            new_tag = ui_prompt("Nova tag:").strip()
            if new_tag:
                tags = normalize_tags([*tags, new_tag])
            else:
                ui_warn("Tag vazia - ignorada.")
        elif action == "remove":
            if not tags:
                ui_warn("Não há tags para remover.")
                continue
            remove_opts = [(t, t) for t in tags]
            remove_opts.append(("__cancel__", "Cancelar"))
            picked = prompt_choice(
                "Qual tag remover?",
                remove_opts,
                default_index=len(remove_opts) - 1,
                clear=False,
            )
            if picked != "__cancel__":
                tags = [t for t in tags if t != picked]
        elif action == "replace":
            ui_blank(1)
            raw = ui_prompt("Tags (separadas por vírgula):").strip()
            tags = normalize_tags(
                [part.strip() for part in raw.split(",") if part.strip()]
            )


def load_config(
    *,
    pat: str,
    openapi_file: str,
    environment: str,
    preset: str,
    repo: dict[str, str],
) -> dict[str, str]:
    return {
        "pat": pat,
        "openapi_file": openapi_file,
        "environment": environment,
        "preset": preset,
        "kong_name_override": env("KONG_NAME"),
        "org": repo["org"],
        "project": repo["project"],
        "repo_name": repo["repo_name"],
        "repo_url": repo["repo_url"],
        "repo_id": env("ADO_REPO_ID"),
        "target_branch": env("ADO_TARGET_BRANCH", "master") or "master",
        "git_user_name": env("GIT_USER_NAME", DEFAULT_GIT_USER_NAME)
        or DEFAULT_GIT_USER_NAME,
        "git_user_email": env("GIT_USER_EMAIL", DEFAULT_GIT_USER_EMAIL)
        or DEFAULT_GIT_USER_EMAIL,
    }


# ---------------------------------------------------------------------------
# Naming / tags (portado de lib/kong/naming.ts + tags.ts)
# ---------------------------------------------------------------------------


def to_pascal_case(segment: str) -> str:
    if not segment:
        return ""
    parts = re.split(r"[-_]", segment)
    return "".join(
        p[:1].upper() + p[1:].lower() for p in parts if p
    )


def tokenize_title(title: str) -> list[str]:
    tokens: list[str] = []
    for part in re.split(r"[\s._-]+", title):
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", part)
        if cleaned:
            tokens.append(cleaned)
    return tokens


def derive_kong_name_from_title(title: str) -> str:
    trimmed = title.strip()
    if not trimmed:
        raise ValueError("OpenAPI info.title está vazio")
    if trimmed.startswith("MB.API."):
        return trimmed

    tokens = tokenize_title(trimmed)
    start = 0
    if tokens and tokens[0].lower() == "mb":
        start += 1
    if start < len(tokens) and tokens[start].lower() == "api":
        start += 1

    body = [to_pascal_case(t) for t in tokens[start:] if to_pascal_case(t)]
    if not body:
        raise ValueError(f"Não foi possível derivar kong.name de: {title}")
    return "MB.API." + ".".join(body)


def validate_kong_name(name: str) -> None:
    if not KONG_NAME_REGEX.match(name):
        raise ValueError(
            "kong.name inválido. Use apenas letras, números, pontos, hífens e underscores."
        )


def derive_tags(kong_name: str, info_title: str | None = None) -> list[str]:
    segments = [s for s in kong_name.split(".") if s]
    if len(segments) >= 3 and segments[0] == "MB" and segments[1] == "API":
        sistema = segments[2].lower()
        recurso = to_pascal_case(segments[-1])
        if info_title:
            api_tag = f"api:{info_title.strip().lower()}"
        else:
            rest = ".".join(s.lower() for s in segments[2:])
            api_tag = f"api:mb.api.{rest}"
        return [f"sistema:{sistema}", api_tag, f"recurso:{recurso}", IMPORT_TAG]

    parts = [p for p in re.split(r"[-_.]+", kong_name) if p]
    sistema = (parts[0].lower() if parts else "app")
    recurso = to_pascal_case(parts[-1] if parts else kong_name)
    api_tag = (
        f"api:{info_title.strip().lower()}"
        if info_title
        else f"api:{kong_name.strip().lower()}"
    )
    return [f"sistema:{sistema}", api_tag, f"recurso:{recurso}", IMPORT_TAG]


def normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        t = tag.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    if IMPORT_TAG not in out:
        out.append(IMPORT_TAG)
    return out


# ---------------------------------------------------------------------------
# Config YAML
# ---------------------------------------------------------------------------


def derive_prd_legacy_backends(
    server_url: str, kong_name: str
) -> list[dict[str, Any]]:
    """Backends IBM (pc/pr/p2/pd) para legacy-api em prd — paridade com o engine."""
    path = ""
    try:
        parsed = urlparse(server_url)
        if parsed.path and parsed.path not in ("", "/"):
            path = parsed.path.rstrip("/")
    except Exception:
        pass

    if not path and kong_name:
        path = f"/{kong_name.lower()}"
    if not path:
        raise ValueError("Não foi possível derivar o path dos backends PRD")

    return [
        {
            "id": backend_id,
            "hosts": [f"kng-int-{backend_id}.corp.prd.n-mercantil.com.br"],
            "server": f"https://apiibm{backend_id}.mercantil.com.br:9444{path}",
        }
        for backend_id in PRD_BACKEND_IDS
    ]


def serialize_routing(
    mode: str,
    case_insensitive: bool,
    backends: list[dict[str, Any]] | None = None,
) -> list[str]:
    lines = ["  routing:", f"    mode: {mode}"]
    lines.append(f"    case_insensitive: {str(case_insensitive).lower()}")
    if backends:
        lines.append("    backends:")
        for backend in backends:
            lines.append(f"      - id: {backend['id']}")
            lines.append("        hosts:")
            for host in backend["hosts"]:
                lines.append(f"          - {host}")
            lines.append(f"        server: {backend['server']}")
    return lines


def build_legacy_config_yaml(
    kong_name: str,
    tags: list[str],
    environment: str,
    server_url: str,
    preset: str = "legacy-api",
) -> str:
    lines: list[str] = [f"preset: {preset}", "", "kong:", f"  name: {kong_name}", "  tags:"]
    for tag in normalize_tags(tags):
        lines.append(f"    - {tag}")

    backends = None
    if preset == "legacy-api":
        if environment == "prd" and server_url:
            try:
                backends = derive_prd_legacy_backends(server_url, kong_name)
            except ValueError:
                backends = None
        lines.extend(
            serialize_routing(
                mode="catch-all",
                case_insensitive=True,
                backends=backends,
            )
        )
    elif preset == "auth-api":
        lines.extend(
            [
                "  jwks:",
                "    enabled: true",
                "    realm: gateway",
                "    ssl_verify: false",
            ]
        )
    elif preset in ("standard-api", "wsdl-proxy"):
        lines.extend(["  jwks:", "    enabled: false"])
        if preset == "wsdl-proxy":
            lines.extend(["  path_stripper:", "    enabled: false"])

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def load_openapi_spec(path: str) -> dict[str, Any]:
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        raise SystemExit(f"Erro: arquivo OpenAPI não encontrado: {file_path}")

    with file_path.open("r", encoding="utf-8") as fh:
        raw = fh.read().strip()
    if not raw:
        raise SystemExit("Erro: arquivo OpenAPI vazio")

    try:
        if raw.startswith("{"):
            spec = json.loads(raw)
        else:
            spec = yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SystemExit(f"Erro: não foi possível ler o OpenAPI: {exc}") from exc

    if not isinstance(spec, dict):
        raise SystemExit("Erro: OpenAPI inválido (esperado objeto JSON/YAML)")

    if "swagger" in spec:
        raise SystemExit(
            "Erro: Swagger 2.0 não é suportado. Converta para OpenAPI 3.x "
            "(campo 'openapi') antes de executar o script."
        )

    openapi_ver = str(spec.get("openapi", ""))
    if not openapi_ver.startswith("3."):
        raise SystemExit(
            "Erro: é necessário OpenAPI 3.x (campo 'openapi' começando com '3.')."
        )

    title = (spec.get("info") or {}).get("title")
    if not title or not str(title).strip():
        raise SystemExit("Erro: OpenAPI sem info.title")

    return spec


def primary_server_url(spec: dict[str, Any]) -> str:
    servers = spec.get("servers") or []
    for server in servers:
        if isinstance(server, dict):
            url = (server.get("url") or "").strip()
            if url:
                return url.rstrip("/")
    return ""


def normalize_spec_servers(spec: dict[str, Any]) -> None:
    servers = spec.get("servers") or []
    urls = [
        (s.get("url") or "").strip()
        for s in servers
        if isinstance(s, dict) and (s.get("url") or "").strip()
    ]
    if not urls:
        return
    picked = next((u for u in urls if u.startswith("https://")), urls[0])
    spec["servers"] = [{"url": picked}]


def spec_to_yaml(spec: dict[str, Any]) -> str:
    return yaml.safe_dump(
        spec,
        sort_keys=False,
        allow_unicode=True,
        width=10**9,
        default_flow_style=False,
    )


# ---------------------------------------------------------------------------
# Azure DevOps REST (sem clone local)
# ---------------------------------------------------------------------------


def ado_headers(pat: str) -> dict[str, str]:
    encoded = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


def ado_repo_base(cfg: dict[str, str], repo_id: str) -> str:
    org = quote(cfg["org"], safe="")
    project = quote(cfg["project"], safe="")
    return (
        f"https://dev.azure.com/{org}/{project}"
        f"/_apis/git/repositories/{repo_id}"
    )


def get_repository_id(cfg: dict[str, str]) -> str:
    if cfg["repo_id"]:
        return cfg["repo_id"]

    org = quote(cfg["org"], safe="")
    project = quote(cfg["project"], safe="")
    url = (
        f"https://dev.azure.com/{org}/{project}"
        f"/_apis/git/repositories?api-version=7.1"
    )
    resp = requests.get(url, headers=ado_headers(cfg["pat"]), timeout=60)
    if not resp.ok:
        raise RuntimeError(
            f"Azure DevOps API error {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    for item in data.get("value", []):
        if item.get("name") == cfg["repo_name"]:
            return item["id"]
    raise RuntimeError(f"Repositório não encontrado: {cfg['repo_name']}")


def get_branch_tip(cfg: dict[str, str], branch: str) -> str:
    """objectId (commit SHA) da tip de refs/heads/{branch}."""
    repo_id = get_repository_id(cfg)
    url = (
        f"{ado_repo_base(cfg, repo_id)}/refs"
        f"?filter=heads/{quote(branch, safe='')}&api-version=7.1"
    )
    resp = requests.get(url, headers=ado_headers(cfg["pat"]), timeout=60)
    if not resp.ok:
        raise RuntimeError(
            f"Azure DevOps API error {resp.status_code}: {resp.text}"
        )
    values = resp.json().get("value") or []
    want = f"refs/heads/{branch}"
    for ref in values:
        if ref.get("name") == want and ref.get("objectId"):
            return str(ref["objectId"])
    raise RuntimeError(
        f"Branch não encontrada no Azure DevOps: {branch}"
    )


def ado_item_exists(cfg: dict[str, str], path: str, *, version: str) -> bool:
    """True se o path existir na branch version (Items API)."""
    repo_id = get_repository_id(cfg)
    normalized = path if path.startswith("/") else f"/{path}"
    url = (
        f"{ado_repo_base(cfg, repo_id)}/items"
        f"?path={quote(normalized, safe='/')}"
        f"&versionDescriptor.version={quote(version, safe='')}"
        f"&versionDescriptor.versionType=branch"
        f"&api-version=7.1"
    )
    resp = requests.get(url, headers=ado_headers(cfg["pat"]), timeout=60)
    if resp.status_code == 404:
        return False
    if resp.ok:
        return True
    raise RuntimeError(
        f"Azure DevOps API error {resp.status_code}: {resp.text}"
    )


def assert_api_not_exists(cfg: dict[str, str], kong_name: str) -> None:
    environment = cfg["environment"]
    branch = cfg["target_branch"]
    api_path = f"/apis/{environment}/{kong_name}.yaml"
    config_path = f"/apis/{environment}/{kong_name}.config.yaml"
    found: list[str] = []
    if ado_item_exists(cfg, api_path, version=branch):
        found.append(api_path.lstrip("/"))
    if ado_item_exists(cfg, config_path, version=branch):
        found.append(config_path.lstrip("/"))
    if found:
        joined = ", ".join(found)
        raise SystemExit(
            f"Erro: esta API já está registrada neste ambiente "
            f"({joined} em {branch}). Abortado."
        )


def push_new_branch_with_files(
    cfg: dict[str, str],
    *,
    branch: str,
    base_commit: str,
    commit_message: str,
    files: list[tuple[str, str]],
) -> None:
    """Cria branch a partir do tip + commit com ficheiros (Pushes API). Sem clone."""
    repo_id = get_repository_id(cfg)
    changes = []
    for rel_path, content in files:
        path = rel_path if rel_path.startswith("/") else f"/{rel_path}"
        changes.append(
            {
                "changeType": "add",
                "item": {"path": path},
                "newContent": {
                    "content": content,
                    "contentType": "rawtext",
                },
            }
        )
    payload = {
        "refUpdates": [
            {
                "name": f"refs/heads/{branch}",
                "oldObjectId": base_commit,
            }
        ],
        "commits": [
            {
                "comment": commit_message,
                "changes": changes,
            }
        ],
    }
    url = f"{ado_repo_base(cfg, repo_id)}/pushes?api-version=7.1"
    resp = requests.post(
        url, headers=ado_headers(cfg["pat"]), json=payload, timeout=120
    )
    if not resp.ok:
        raise RuntimeError(
            f"Azure DevOps API error {resp.status_code}: {resp.text}"
        )


def create_pull_request(
    cfg: dict[str, str],
    *,
    source_branch: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    repo_id = get_repository_id(cfg)
    url = (
        f"{ado_repo_base(cfg, repo_id)}/pullrequests?api-version=7.1"
    )
    payload = {
        "sourceRefName": f"refs/heads/{source_branch}",
        "targetRefName": f"refs/heads/{cfg['target_branch']}",
        "title": title,
        "description": description,
    }
    resp = requests.post(
        url, headers=ado_headers(cfg["pat"]), json=payload, timeout=60
    )
    if not resp.ok:
        raise RuntimeError(
            f"Azure DevOps API error {resp.status_code}: {resp.text}"
        )
    return resp.json()


def pull_request_web_url(cfg: dict[str, str], pull_request_id: int) -> str:
    org = quote(cfg["org"], safe="")
    project = quote(cfg["project"], safe="")
    repo = quote(cfg["repo_name"], safe="")
    return (
        f"https://dev.azure.com/{org}/{project}"
        f"/_git/{repo}/pullrequest/{pull_request_id}"
    )


def branch_name_for(kong_name: str) -> str:
    short_id = uuid.uuid4().hex[:8]
    slug = (
        re.sub(r"^MB\.API\.", "", kong_name)
        .replace(".", "-")
        .lower()[:30]
    )
    return f"feat/kong-provision-{slug}-{short_id}"


def provision_via_ado(
    cfg: dict[str, str],
    *,
    kong_name: str,
    openapi_yaml: str,
    config_yaml: str,
    openapi_source: str,
) -> dict[str, Any]:
    """Provisiona via Azure DevOps REST — sem git clone local."""
    environment = cfg["environment"]
    relative_api = f"apis/{environment}/{kong_name}.yaml"
    relative_config = f"apis/{environment}/{kong_name}.config.yaml"
    branch = branch_name_for(kong_name)
    commit_message = f"feat(kong): provision {kong_name} in {environment}"
    pr_title = f"{MR_TITLE_PREFIX} Provision {kong_name} ({environment})"

    ui_info(f"Consultando tip de {cfg['target_branch']}...")
    tip = get_branch_tip(cfg, cfg["target_branch"])

    ui_info("Verificando se a API já existe...")
    assert_api_not_exists(cfg, kong_name)

    ui_info(f"Criando branch {paint(branch, _S_BOLD)} e enviando ficheiros...")
    push_new_branch_with_files(
        cfg,
        branch=branch,
        base_commit=tip,
        commit_message=commit_message,
        files=[
            (relative_api, openapi_yaml),
            (relative_config, config_yaml),
        ],
    )

    description = "\n".join(
        [
            f"## {APP_NAME}",
            "",
            f"- **API:** {kong_name}",
            f"- **Ambiente:** {environment}",
            f"- **Preset:** {cfg['preset']}",
            "- **Modo:** create",
            f"- **OpenAPI source:** {openapi_source}",
            "",
            "A validação automática será feita na pipeline do repositório GitOps.",
        ]
    )

    ui_info("Abrindo MR...")
    pr = create_pull_request(
        cfg,
        source_branch=branch,
        title=pr_title,
        description=description,
    )
    pr_id = int(pr["pullRequestId"])
    return {
        "branch_name": branch,
        "commit_message": commit_message,
        "pull_request_id": pr_id,
        "pull_request_url": pull_request_web_url(cfg, pr_id),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    init_ui()
    ui_banner()

    pat = ensure_pat()
    repo = ensure_repo()
    openapi_file = discover_openapi_json()
    environment = prompt_environment()
    preset = prompt_preset()

    spec = load_openapi_spec(openapi_file)
    title = str(spec["info"]["title"]).strip()

    cfg = load_config(
        pat=pat,
        openapi_file=openapi_file,
        environment=environment,
        preset=preset,
        repo=repo,
    )
    kong_name = cfg["kong_name_override"] or derive_kong_name_from_title(title)
    validate_kong_name(kong_name)

    server_url = prompt_server_url(spec)
    if server_url:
        spec["servers"] = [{"url": server_url}]
    else:
        normalize_spec_servers(spec)
        server_url = primary_server_url(spec)

    tags = prompt_tags(derive_tags(kong_name, title))
    openapi_yaml = spec_to_yaml(spec)
    config_yaml = build_legacy_config_yaml(
        kong_name=kong_name,
        tags=tags,
        environment=cfg["environment"],
        server_url=server_url,
        preset=cfg["preset"],
    )

    ui_step("Resumo")
    rows = [
        ("Repositório", cfg["repo_url"]),
        ("OpenAPI", cfg["openapi_file"]),
        ("API", kong_name),
        ("Ambiente", cfg["environment"]),
        ("Preset", cfg["preset"]),
        ("Upstream", server_url or "(não definido)"),
        ("Tags", ", ".join(tags)),
        (
            "Arquivos",
            f"apis/{cfg['environment']}/{kong_name}.yaml",
        ),
        ("", f"apis/{cfg['environment']}/{kong_name}.config.yaml"),
    ]
    for label, value in rows:
        if label:
            valued = (
                paint(value, _S_BOLD, _S_GREEN)
                if label in ("API", "Ambiente")
                else value
            )
            print(paint(f"  {label + ':':<14}", _S_DIM) + valued)
        else:
            print(paint(f"  {'':<14}{value}", _S_DIM))
    ui_blank(2)
    answer = ui_prompt("Abrir MR no Azure DevOps? [y/N]").strip().lower()
    if answer not in ("y", "yes", "s", "sim"):
        ui_blank(1)
        ui_warn("Cancelado.")
        pause_before_exit()
        sys.exit(0)

    ui_blank(1)
    ui_info("Provisionando via Azure DevOps (sem clone local)...")
    ui_blank(1)

    result = provision_via_ado(
        cfg,
        kong_name=kong_name,
        openapi_yaml=openapi_yaml,
        config_yaml=config_yaml,
        openapi_source=str(Path(cfg["openapi_file"]).expanduser().resolve()),
    )

    clear_screen()
    ui_blank(1)
    print(paint(f"  {APP_NAME}", _S_DIM, _S_CYAN))
    print(paint("  *  MR criado com sucesso", _S_BOLD, _S_GREEN))
    print(paint("  " + ("-" * 44), _S_DIM))
    ui_blank(1)
    print(paint("  Branch:  ", _S_DIM) + result["branch_name"])
    print(paint("  Commit:  ", _S_DIM) + result["commit_message"])
    print(paint("  PR ID:   ", _S_DIM) + str(result["pull_request_id"]))
    ui_blank(1)
    print(paint("  URL do Merge Request:", _S_BOLD))
    ui_blank(1)
    print(paint(f"  {result['pull_request_url']}", _S_BOLD, _S_CYAN))
    ui_blank(1)
    pause_before_exit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        ui_warn("Cancelado.")
        pause_before_exit()
        sys.exit(130)
    except SystemExit as exc:
        code = exc.code
        if code in (0, None):
            raise
        # die() já pausou; sys.exit(n) / SystemExit(str) ainda sem pausa
        if isinstance(code, str):
            try:
                ui_err(code)
            except Exception:
                print(code, file=sys.stderr)
            pause_before_exit()
            sys.exit(1)
        pause_before_exit()
        raise
    except Exception as exc:
        try:
            ui_err(str(exc))
        except Exception:
            print(f"Erro: {exc}", file=sys.stderr)
        pause_before_exit()
        sys.exit(1)
