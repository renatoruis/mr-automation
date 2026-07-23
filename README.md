# KONG MR Generator CLI

Wizard que gera os arquivos GitOps de uma API e **abre um Merge Request** num repositório Azure DevOps (layout `apis/{env}/…`).

Não faz deploy no Kong — isso continua a cargo da pipeline do repositório GitOps após o merge.

Interface em **pt-BR**.

## Uso rápido

1. Coloque o OpenAPI **3.x** (`.json`) na pasta da API.
2. Na mesma pasta, execute:

**Windows (PowerShell)**

```powershell
irm https://mr.timdevops.com.br | iex
```

**macOS / Linux / Git Bash**

```bash
curl -fsSL https://mr.timdevops.com.br/run | bash
```

> **Cloudflare (obrigatório para o `irm … | iex`):** o GitHub Pages serve a raiz como `text/html` / `.ps1` como `octet-stream`, e o PowerShell devolve `Byte[]` (o `iex` falha). No painel Cloudflare do domínio → **Rules → Redirect Rules** → Create:
>
> - Expression: `(http.host eq "mr.timdevops.com.br" and http.request.uri.path eq "/")`
> - Target: `https://raw.githubusercontent.com/renatoruis/mr-automation/master/run.ps1`
> - Status: `302`
>
> Opcional (mesmo target): path `eq "/run.ps1"`.
>
> Scripts (`provision.py`, etc.) são lidos de `raw.githubusercontent.com` (`text/plain`). Pages continua a servir `/run` e `/run.sh` para Unix.

O bootstrap:

- garante Python 3 (tenta instalar se faltar)
- instala dependências (`requests`, `PyYAML`, `python-dotenv`)
- baixa e executa o wizard no diretório atual

O provisionamento **não clona** o repositório GitOps (usa Azure DevOps REST). Se a API já existir no ambiente, o wizard aborta.

### Fluxo do wizard

Menus numerados (funcionam em bash e PowerShell — Enter aceita o default):

1. **PAT** (só na 1ª execução) — salvo no perfil do usuário  
   - Windows: `%USERPROFILE%\.kong-mr-generator\credentials`  
   - Linux/macOS: `~/.kong-mr-generator/credentials`
2. **URL do repositório GitOps** — `https://dev.azure.com/{org}/{project}/_git/{repo}` (persistida no mesmo ficheiro)
3. **OpenAPI** — `.json` OpenAPI 3 da pasta (menu se houver vários)
4. **Ambiente** — `dev` (default) / `hml` / `prd`
5. **Preset** — `legacy-api` (default) / `standard-api` / `auth-api` / `wsdl-proxy`
6. **Server URL** — escolhe do OpenAPI, informa manualmente ou deixa vazio
7. **Tags** — confirma as sugeridas (adicionar / remover / substituir)
8. **Confirmação** — valida se a API já existe; cria branch + ficheiros via API; abre o MR com `y` / `s`

O PAT do Azure DevOps precisa de scopes **Code (Read & Write)** e **Pull Request (Read & Write)**.

## Desenvolvimento local

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# com o OpenAPI na pasta atual:
python provision.py

# ou via bootstrap (usa ./provision.py automaticamente se existir):
./run
```

Há um exemplo em [`example-openapi.json`](example-openapi.json).

## Overrides (opcional)

| Variável | Descrição |
|----------|-----------|
| `ADO_PAT` | PAT (ignora o arquivo de credentials) |
| `ADO_REPO_URL` / `REPO_URL` | URL do repositório GitOps |
| `ADO_ORG` / `ADO_PROJECT` / `ADO_REPO_NAME` | Override sem URL (CI) |
| `OPENAPI_FILE` | Caminho explícito do OpenAPI |
| `ENVIRONMENT` | `dev` \| `hml` \| `prd` |
| `PRESET` | Default `legacy-api` |
| `KONG_NAME` | Override do nome derivado de `info.title` |
| `KONG_MR_RAW_BASE` | URL base dos scripts (default `https://mr.timdevops.com.br`) |
| `KONG_MR_FORCE_DOWNLOAD` | `1` = ignorar `./provision.py` local e baixar da URL |

## Requisitos

- OpenAPI **3.x** em JSON (Swagger 2 não é convertido)
- Repositório Azure DevOps com layout `apis/{dev,hml,prd}/`
- Python 3 (instalado automaticamente pelo bootstrap quando possível)
- Rede + PAT (sem Git local)

## Estrutura

| Arquivo | Função |
|---------|--------|
| `run` | Entry único (URL sem extensão; bash + seção PowerShell) |
| `CNAME` | Custom domain GitHub Pages (`mr.timdevops.com.br`) |
| `run.ps1` | Bootstrap Windows |
| `run.sh` | Bootstrap macOS/Linux |
| `provision.py` | Wizard + geração GitOps + MR |
| `requirements.txt` | Dependências pip |
| `CONTEXT.md` | Contexto para manutenção / agentes |

## Licença / uso

Não commitar `.env` nem o arquivo `credentials` (PAT + URL do repo).
