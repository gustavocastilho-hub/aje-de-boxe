# AJE DE BOXE — Instruções do projeto

Bot de WhatsApp (assistente "Vic") para a academia AJE DE BOXE. Atende leads via webhook UAZAPI, processa mensagens com Google Gemini e registra interações no Google Sheets.

## Regra obrigatória: commit, push e deploy

**Antes de qualquer operação de commit, push ou redeploy, SEMPRE perguntar:**

> "Quer que eu faça commit, push e redeploy agora?"

Aguardar confirmação explícita antes de executar. Nunca fazer essas operações de forma automática ou sem aprovação, mesmo que o código esteja pronto.

Isso inclui:
- `git commit`
- `git push`
- Redeploy via Portainer (build + force-update do serviço Swarm)
- Build de imagem Docker com `nocache=true`

## Deploy

O redeploy **não depende do GitHub Actions**. Claude executa o build e o force-update diretamente via Portainer API.

### Dados do deploy

- **Portainer URL:** https://91.98.64.92:9443
- **Endpoint (ID):** `1` (local-swarm)
- **Stack (ID / nome):** `49` / `aje-de-boxe`
- **Imagem Docker (tag):** `ghcr.io/gustavocastilho-hub/aje-de-boxe:latest`
- **URL do projeto:** https://webhook-whatsapp.strategicai.com.br
- **Serviços Swarm:**
  - API: `aje-de-boxe_aje-api` — ID `ha4h76aw9l2z075am5ekwjvv9`
  - Worker: `aje-de-boxe_aje-worker` — ID `rydt8l56yrsaw2ikedwwxf7hp`

Credenciais (`PORTAINER_TOKEN`, `GITHUB_TOKEN`) estão em `.env` na raiz do projeto (nunca commitado).

### Fluxo de redeploy

1. `git commit` + `git push origin main` (apenas versionamento; o redeploy é feito manualmente, não pelo webhook do Actions).
2. Criar tarball do contexto de build:
   ```
   tar -czf /tmp/build-context.tar.gz --exclude='.git' --exclude='node_modules' --exclude='.env' .
   ```
3. Build via Portainer API (endpoint `1`, tag `ghcr.io/gustavocastilho-hub/aje-de-boxe:latest`, `nocache=true`):
   ```
   POST https://91.98.64.92:9443/api/endpoints/1/docker/build?t=ghcr.io/gustavocastilho-hub/aje-de-boxe:latest&nocache=true
   Header: X-API-Key: $PORTAINER_TOKEN
   Header: Content-Type: application/x-tar
   Body: conteúdo do tarball
   ```
4. Force-update dos dois serviços Swarm (API e worker) com o spec completo, incrementando `ForceUpdate` em `TaskTemplate`:
   - `GET /api/endpoints/1/docker/services/<SERVICE_ID>` → pega spec atual + version
   - `POST /api/endpoints/1/docker/services/<SERVICE_ID>/update?version=<version>` com o spec modificado
5. Verificar HTTP 200 em https://webhook-whatsapp.strategicai.com.br/
6. Verificar se os containers estão rodando via Portainer API:
   `GET /api/endpoints/1/docker/services/<SERVICE_ID>` e checar tasks ativas.
   - Se algum container estiver com estado diferente de `running`, ler os logs e corrigir o erro antes de encerrar.

## Tom e idioma

- Responder sempre em português brasileiro.
- Respostas curtas e diretas.
- Não usar emojis a menos que solicitado.
