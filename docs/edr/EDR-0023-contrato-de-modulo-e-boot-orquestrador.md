# EDR-0023 — Contrato de módulo e boot orquestrador do Mission Core

**Status:** Proposto (2026-07-25)
**Data:** 2026-07-25
**Complementa:** EDR-0018, EDR-0019, EDR-0020, EDR-0021, EDR-0022
**Relaciona:** Cap 6 (Kernel), Cap 7 (Mission), Cap 8 (Vision), Cap 9 (Voice),
Cap 13 (Interface), Cap 16 (Diagnóstico)

## Contexto

O boot oficial do Notebook nunca foi escrito de verdade. Três fatos medidos
em 2026-07-25:

1. **As etapas 6 a 10 do boot são um `for` que só escreve no log.** Em
   `src/orion/kernel/boot.py`, `_ETAPAS_PENDENTES` percorre Arduino, banco,
   IA, Vision e Motion Core publicando `diagnostic.error` com
   `motivo: "nao_implementado"`. Nada é iniciado.
2. **Até hoje o processo morria logo após o boot.** `_executar()` publicava
   `system.ready` e caía direto no `finally: await sistema.encerrar()`.
   Corrigido em `b1a3c0c` (o boot agora espera SIGINT/SIGTERM), mas isso só
   criou o lugar onde os módulos deveriam entrar — eles ainda não entram.
3. **O robô real vive em `tools/conversar_fofao.py`.** É esse arquivo
   (~18 KB) que o `orion-avatar.service` executa, e é ele que carrega voz,
   IA, avatar, sentinela de visão e o enlace TCP com o Raspberry. Uma
   ferramenta da pasta `tools/` tornou-se o sistema. O próprio arquivo de
   serviço admite: *"trocar ExecStart por python -m orion quando o loop
   principal existir"*.

As consequências já são visíveis e custaram tempo real:

- **`VisionCore` está completo e nunca roda.** Tem pipeline, reconexão de
  câmera, publicação de eventos — e a única referência a ele no projeto
  está dentro do próprio arquivo. A vistoria de 2026-07-24 registrou que
  `vision.resolution/fps/yolo_model/confidence_threshold` são configuração
  morta. Não existe lugar arquitetural onde ligá-lo.
- **Dois módulos disputariam a câmera.** `SentinelaVisao` abre a própria
  `CapturaCamera`; `VisionCore` abre outra. Duas aberturas do mesmo
  `/dev/videoN` no mesmo processo dão erro ou imagem corrompida. Hoje isso
  não estoura só porque um dos dois não roda.
- **O Watchdog não tem o que vigiar.** `HealthMonitor` e `Watchdog` sobem no
  boot e ficam prontos, mas nenhum módulo se registra. A vigilância do
  Cap 6 seção 8 existe em código e não existe em produção.
- **Sem lugar óbvio para as coisas, elas se espalham.** Em 2026-07-25 havia
  quatro linhas de código divergentes (Windows, GitHub, Raspberry, Notebook)
  e, só no Notebook, cópias soltas de `conversar_fofao.py` e `orion.yaml` na
  raiz do projeto. Parte disso é falta de disciplina de git, mas parte é
  ausência de um contrato que diga onde cada coisa mora.

O Motion Core (Raspberry) **já resolveu esse problema à sua maneira**:
`motion_core/__main__.py` instancia o `BehaviorCore`, registra
comportamentos (`maestro.registrar(...)`), sobe tarefas de fundo e tem
guarda-rail de prioridade. Funciona bem. Este EDR leva a mesma disciplina
para o Mission Core, com um contrato explícito em vez de convenção
implícita.

## Decisão

### 1. Contrato `ModuloOrion`

Todo módulo de alto nível do Mission Core (Vision, Voice, Mission/IA,
Display) passa a cumprir um protocolo único, em `src/orion/kernel/modulo.py`:

```python
class ModuloOrion(Protocol):
    nome: str                              # identidade no ServiceRegistry

    async def iniciar(self) -> None: ...    # sobe, assina eventos, abre hardware
    async def encerrar(self) -> None: ...   # solta hardware, cancela tarefas
    def esta_saudavel(self) -> bool: ...    # o que o Watchdog pergunta
```

Regras do contrato:

- `iniciar()` **pode falhar**. Falha significa "módulo ausente", nunca
  "sistema abortado" (Cap 6 s.8).
- `encerrar()` é **idempotente** e nunca levanta exceção. É chamado no
  desligamento e possivelmente após uma falha parcial.
- `esta_saudavel()` é **síncrono e sem efeito colateral** — o Watchdog o
  chama em laço; não pode fazer I/O.
- O módulo **não conhece outros módulos**. Toda troca continua pelo Event
  Bus (regra 1 do `CLAUDE.md`).

### 2. O boot passa a ser orquestrador, não encanamento

`BootManager` deixa de listar etapas pendentes e passa a percorrer módulos:

```python
for modulo in modulos:
    try:
        await modulo.iniciar()
        registry.registrar(modulo.nome, ...)
        health_monitor.acompanhar(modulo)
    except Exception:
        logger.warning("modulo %s indisponivel", modulo.nome)
        await event_bus.publish("diagnostic.error", {...})
```

`SistemaOrion.encerrar()` chama `encerrar()` de cada módulo **na ordem
inversa** da inicialização, antes de parar o watchdog e drenar o bus. Sem
isso, câmera e microfone ficam presos até o processo morrer.

### 3. Um dono por recurso de hardware

Cada recurso físico (câmera de visão, microfone, alto-falante) tem **um
único módulo dono**, que o abre e o fecha. Quem precisa da informação
**assina o Event Bus** — não abre o dispositivo.

Aplicação imediata: o `VisionCore` passa a ser o dono da câmera de visão, e
a `SentinelaVisao` deixa de abrir a sua, passando a ouvir os eventos
`vision.person_recognized` / `vision.person_detected` que o `VisionCore` já
publica. Isso resolve a disputa por projeto, não por sorte.

### 4. Ordem de migração

Um módulo por vez, cada um com testes, sem big bang:

1. **Vision** — `VisionCore` como primeiro a cumprir o contrato (já tem
   `executar()`/`parar()`; falta pouco). A `SentinelaVisao` migra para
   eventos no mesmo passo, senão a câmera fica disputada.
2. **Mission/IA** — `AiManager`, `MissionPlanner`, `PonteDecisao`,
   `PonteConselho`.
3. **Voice** — `VoiceCore` + síntese.
4. **Display** — avatar.

O `ExecStart` do `orion-avatar.service` **só** passa a `python -m orion`
quando os quatro estiverem de pé e testados ao vivo. Até lá,
`tools/conversar_fofao.py` continua sendo o processo de produção — trocar
antes deixaria o robô sem voz.

Ao fim da migração, `tools/conversar_fofao.py` volta a ser o que o nome diz:
uma ferramenta de conversa para desenvolvimento, sem responsabilidade de
produção.

### 5. `--sim` tem que simular

Achado durante a análise: `_conectar_raspberry()` **não recebe** o parâmetro
`simulado` e tenta o Pi real mesmo com `--sim`, apesar de o `CLAUDE.md`
documentar a flag como "com Raspberry/Arduino simulados". Isso é perigoso
além de errado: rodar o boot para testar rouba o enlace `mission_core` do
processo de produção, porque o Pi só aceita um link com esse nome por vez.

O modo simulado passa a valer para todos os enlaces e para todo hardware —
nenhum módulo abre dispositivo real nem soquete real com `--sim`.

## Consequências

- **O Watchdog ganha função.** Pela primeira vez haverá módulos registrados
  para vigiar, e a escalada do Cap 6 s.8 (reconectar → reiniciar → logar →
  publicar) passa a valer na prática.
- **A configuração de visão deixa de ser morta** — `yolo_model`,
  `confidence_threshold`, `camera_indice_principal` passam a ter efeito.
- **Desligamento deixa de vazar hardware.** Câmera e microfone são liberados
  na ordem certa.
- **Custo: mexer no caminho do boot do sistema que está em produção.** Por
  isso a migração é módulo a módulo, com o serviço só trocando no fim.
- **Não altera** a arquitetura de três computadores (EDR-0018/0019), a
  cadeia Notebook → Raspberry → Arduino, a regra do Event Bus, nem as três
  camadas de segurança. Este EDR organiza o **interior** do Mission Core.
- **Não obriga** o Motion Core a mudar agora. Ele já orquestra bem à sua
  maneira; adotar o mesmo contrato lá é desejável mas fica para depois, e
  não bloqueia nada.
- **Em aberto:** se `esta_saudavel()` basta ou se o Watchdog precisará de
  heartbeat assíncrono por módulo (hoje o `HealthMonitor` foi desenhado para
  heartbeats de enlace, não de módulo local). Decidir na implementação do
  primeiro módulo, com o caso real na mão.

**Fim do EDR-0023 (proposto)**
