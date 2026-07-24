# EDR-0021 — IA remota via API + memória híbrida (SQLite + Obsidian)

**Status:** Aprovado (2026-07-23)
**Data:** 2026-07-23
**Complementa:** EDR-0018, EDR-0019, EDR-0020
**Relaciona:** Cap 7 (Mission), Cap 11 (Memória), Cap 15 (Banco), Cap 17 (Configuração)

## Contexto

O Ollama local (`gemma3:4b`, Notebook) tem se mostrado pouco confiável em
uso real: trava com frequência, e o EDR-0020 já registrou que "já travou
uma vez em teste de IA" (por isso existe o Guardião de RAM). Manter
hardware capaz de rodar um LLM local com boa qualidade é caro. O usuário
tem internet disponível (celular) e prefere usar uma API remota (Gemini
Flash, sugerido) — mais rápida, mais barata por chamada, sem exigir
máquina dedicada.

O usuário também quer uma camada de memória mais rica do que a tabela
`conhecimento` (chave/valor simples) permite hoje: fatos e reflexões em
texto livre, interligados por wikilinks, no estilo Obsidian.

Isso tensiona com o princípio "100% offline" do `CLAUDE.md` — decisão
consciente e explícita do usuário (2026-07-23), não um descuido. Não é
abandonar o offline por completo: a proposta mantém o Ollama como
fallback automático quando não há internet, no mesmo espírito de
tolerância a ausência já usado no projeto (Cap 6 s.8 — Arduino, SSD e
Notebook ausentes já são tolerados hoje).

## Decisão

### IA remota (Gemini como provider primário)

- `AiManager` (`src/orion/mission/ai_manager.py`) vira uma pequena fábrica
  com dois backends atrás da mesma interface (`responder(texto, contexto)
  -> str`): `OllamaBackend` (já existe, extraído do código atual) e
  `GeminiBackend` (novo).
- Config nova em `orion.yaml`, seção `ai`: `provider: "gemini" | "ollama"`
  + `gemini_model: "gemini-2.0-flash"` (nome exato a confirmar na
  implementação), mantendo `ollama_model` como está.
- **Fallback automático**: se a chamada remota falhar (sem internet, erro
  de API, timeout), cai para o Ollama local na mesma resposta — loga o
  evento (`diagnostic.error` ou similar), nunca deixa a conversa muda.
  Se o Ollama também falhar, resposta padrão de "sem conexão no momento".
- **Chave de API nunca em `orion.yaml`/git**: lida de variável de ambiente
  (`GEMINI_API_KEY`), configurada via `Environment=` no
  `orion-avatar.service` ou um arquivo `.env` fora do controle de versão
  (`.gitignore`). Chave já recebida do usuário nesta sessão, guardada
  localmente (não commitada) até a implementação.

### Memória híbrida (SQLite continua, Obsidian se soma)

- **SQLite continua dono** dos dados estruturados/operacionais (pessoas,
  conversas, telemetria, eventos, configuração) — Cap 15 não muda, e o
  backup diário + réplica cruzada para o Notebook (consertado de verdade
  em 2026-07-23) continua servindo esses dados sem alteração.
- **Novo**: vault Obsidian (pasta de arquivos `.md` com wikilinks) para
  conhecimento de longo prazo — fatos aprendidos, reflexões, resumos —
  que não cabem bem numa tabela chave/valor.
- Vive **dentro do SSD do Raspberry** (`/mnt/ssd/orion/obsidian_vault/`)
  para herdar o mesmo backup diário + réplica cruzada que o `orion.db` já
  tem — nenhuma infraestrutura de backup nova precisa ser criada.
- **Acesso só via API de memória** (regra 5 do `CLAUDE.md` — nenhum módulo
  abre arquivo/banco direto): novo módulo
  `motion_core/memory/vault.py` (`VaultConhecimento`), com métodos como
  `escrever_nota(titulo, conteudo, links)`, `buscar(consulta)` (busca
  simples por texto/FTS para começar — embeddings/RAG ficam para uma
  fase futura se a qualidade não bastar) e `ler_nota(titulo)`. Exposto
  via os mesmos comandos `memory.*` já usados via TCP (categoria nova ou
  comandos dedicados — detalhe de implementação).
- A IA (no Notebook, via `MemoryClient` já existente) consulta o vault
  antes de responder, do mesmo jeito que já usa `conversas_recentes` e
  `conhecimento_relevante` hoje — só mais uma fonte de contexto.
- Sincronizar com o app Obsidian de verdade (celular/Notebook do usuário)
  fica em aberto — mais simples é o vault viver na mesma pasta que o
  Obsidian abre (via Syncthing ou pasta compartilhada), sem reinventar
  sincronização.

## Consequências

- Quebra "100% offline" **enquanto a API estiver em uso** — mitigado pelo
  fallback automático para o Ollama local (nunca fica sem IA nenhuma, só
  degrada).
- Novo custo recorrente (chamadas de API) — baixo com Gemini Flash, mas é
  gasto que não existia antes.
- Reaproveita 100% da infraestrutura de backup/réplica já corrigida hoje
  (SSD, backup diário, réplica cruzada) — nenhum trabalho duplicado.
- Novo módulo (`motion_core/memory/vault.py`) e nova seção de config; o
  roadmap (Cap 20) ganha essa camada de memória.
- Em aberto, a decidir na implementação: motor de busca do vault (texto
  simples vs. embeddings de verdade) e o formato exato dos comandos
  `memory.*` para notas.

**Fim do EDR-0021 (proposto)**
