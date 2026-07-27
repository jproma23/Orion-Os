# EDR-0025 — Arquitetura em camadas do ORION OS 1.0

**Data:** 2026-07-27
**Estado:** aceito (decisões de escopo); as camadas em si são alvo, não estado atual.
**Origem:** diagrama do mantenedor, sessão de 2026-07-27 (noite).

## Contexto

O mantenedor descreveu a forma que o ORION OS deve ter no 1.0 em três peças:
uma pilha de camadas (Kernel → Core → API → Desktop), uma cadeia de boot
(BIOS → ... → robô operacional) e uma tela de inicialização com um ✓ por
subsistema.

Isso é bom e faltava: até aqui o projeto crescia por fase (`PLANO_IMPLEMENTACAO.md`),
sem um vocabulário que dissesse a que camada cada módulo pertence. O risco de
adotar o diagrama sem análise, porém, é real — ele nomeia coisas que já existem
com outros nomes, nomeia coisas que não existem, e colide com nomes que os
EDR-0018 e 0019 já fixaram.

Este EDR registra o que é alvo, o que já está pronto sob outro nome, o que fica
de fora do 1.0 e por quê.

## Decisão 1 — As camadas viram vocabulário oficial, não uma reescrita

As quatro camadas passam a ser a forma oficial de falar do sistema. **Nenhum
código muda de lugar por causa deste EDR.** O mapeamento abaixo é o estado
verificado em `c618237`:

| Camada / bloco | Estado | Onde vive hoje |
|---|---|---|
| **Kernel** — Communication Manager | pronto | `src/orion/communication/` (CRC16, ACK, retransmissão, heartbeat, descoberta) |
| **Kernel** — Sensor Manager | parcial | managers do firmware + `motion_core/navigation/fusao_sensores.py` |
| **Kernel** — Hardware Manager | parcial | encapsulado no firmware (`*_manager.h`); sem HAL em Python |
| **Kernel** — Driver Manager | não existe | — |
| **Kernel** — Power Manager | não existe | só `firmware/.../bateria_manager.h`, que lê tensão |
| **Core** — Vision Core | pronto | `src/orion/vision/vision_core.py` |
| **Core** — Audio Core | pronto | `src/orion/voice/voice_core.py` (nome atual: *voice*) |
| **Core** — Navigation Core | pronto | `motion_core/navigation/navigation_core.py` |
| **Core** — Memory Core | pronto | `motion_core/memory/` (banco no SSD do Pi, regra 5) |
| **Core** — Reasoning Core | parcial | `src/orion/mission/` + `motion_core/behavior/` (o maestro) |
| **Core** — Mission Core | pronto | `src/orion/mission/mission_planner.py` |
| **Core** — Security Core | distribuído | `safety_manager.h` (Mega) + `vigilancia_obstaculo` (Pi); nada estratégico no Notebook |
| **API** — Python / C++ | pronto | é a linguagem dos três núcleos |
| **API** — REST | pronto | endpoints da `motion_core/webui/` |
| **API** — WebSocket | substituído | a webui usa SSE (`text/event-stream`), mão única, suficiente para telemetria |
| **API** — ROS2 / MQTT | **fora do 1.0** | ver Decisões 2 e 3 |
| **Desktop** — Painel, Mapa, Logs, IA | pronto | `dashboard`, `mapa`, `diagnostico`, `conversa` |
| **Desktop** — Câmeras | não existe | não há página de vídeo; a câmera do pan/tilt ainda não transmite |
| **Desktop** — Terminal | não existe | — |

## Decisão 2 — MQTT fica de fora, definitivamente

**Decisão do mantenedor:** *"sem MQTT, porque já temos uma comunicação criada
que funciona."*

O argumento é o correto e vale registrar inteiro. O MQTT resolveria transporte
de mensagens entre módulos — problema que `src/orion/communication/` já resolve,
com garantias que um broker genérico não dá de graça:

- CRC16 idêntico em Python e C++, validado byte a byte nos dois sentidos;
- ACK obrigatório com até 3 retransmissões e `comm.link_degraded` ao esgotar;
- heartbeat com `comm.module_lost` / `comm.module_recovered`;
- o **mesmo** formato de mensagem no TCP e no Serial (regra 8).

Além disso o MQTT exige um broker no ar, o que empurra contra o "100% offline"
que é a identidade do projeto, e adicionaria um ponto de falha que hoje não
existe. Trocar código testado por dependência nova só se justifica quando falta
alguma coisa — e não falta.

Consequência prática: **a tela de inicialização não mostra `✓ MQTT`.** O
projeto já adotou a regra "nunca mostrar valor inventado como medida" (foi por
ela que `sem_eco` deixou de aparecer como 517 cm). Um ✓ verde num subsistema
inexistente é a mesma falha, na tela mais visível do robô.

## Decisão 3 — ROS2 fica fora do 1.0

Não por ser ruim, mas por duplicar arquitetura. O ROS2 traz event bus e
transporte próprios; o ORION já tem os dois (`src/orion/kernel/event_bus.py` e
`src/orion/communication/`). Adotá-lo criaria duas arquiteturas convivendo, e o
TCC precisaria justificar por que existem as duas.

Se um dia entrar, entra como **ponte opcional** na camada API — nunca como
camada obrigatória. Assim o robô continua funcionando sozinho e ganha
interoperabilidade como extra.

## Decisão 4 — "Orion Boot" é serviço de aplicação, não bootloader

A cadeia proposta colocava o Orion Boot **entre a BIOS e o kernel do Linux**.
Nessa posição só cabe um bootloader (GRUB); escrever um seria outro projeto e
não daria nenhuma capacidade ao robô.

O `src/orion/kernel/boot.py` que já existe é um Boot Manager de aplicação: roda
depois do Linux, sobe os módulos na ordem e publica `system.ready`. A cadeia
real, que já funciona hoje:

```
BIOS → GRUB → Linux → systemd → orion-motion.service → boot.py → system.ready → Painel
```

A **experiência** de "ORION OS inicializando" é obtida com um tema de Plymouth
(a splash nativa do Linux) mais os serviços que já existem em `tools/`, sem
tocar em bootloader.

## Decisão 5 — A tela de inicialização é dirigida por evento real

Os ✓ não são script nem animação: cada um acende quando o módulo correspondente
registra no Service Registry e o Boot Manager publica seu evento. Módulo que
falhar aparece em vermelho, não some. É a diferença entre uma tela que informa e
uma tela que enfeita — e ela é barata, porque `boot.py` e o `ServiceRegistry` já
produzem exatamente esses eventos.

## Decisão 6 — Resolver as colisões de nome antes de escrever código

Duas palavras têm dois significados hoje, e isso vira bug de comunicação:

- **Mission Core** = o Notebook inteiro (EDR-0018/0019) **e** um módulo do Core.
- **Vision Core** = rótulo do Notebook no diagrama **e** `vision_core.py`.

Os nomes de hardware são lei nos EDRs anteriores e **não mudam**. Quando houver
ambiguidade, o módulo é citado pelo arquivo (`mission_planner.py`,
`vision_core.py`) e a máquina pelo papel (Mission Core = Notebook).

## Decisão 7 — Apagar o stub `src/orion/memory/`

`src/orion/memory/` contém apenas um `__init__.py` de 0 byte e um README. A
memória real vive em `motion_core/memory/`, no Pi, como manda a regra 5 (banco
no SSD, nunca no Notebook). O stub faz quem segue o diagrama procurar no lugar
errado. Sai.

## O que este EDR NÃO decide

- **Driver Manager e Power Manager**: aceitos como alvo, sem desenho. O Power
  Manager tem gancho concreto esperando (botão de desligar e a tela que dorme,
  propostos em 2026-07-27), mas isso é um EDR próprio.
- **Security Core como módulo único**: hoje a segurança é distribuída em 3
  camadas por decisão da regra 7, e isso funciona. Centralizar exigiria mostrar
  que não enfraquece a camada reativa do Mega — que precisa continuar
  independente de tudo.
- **Páginas Câmeras e Terminal**: dependem, respectivamente, de a câmera do
  pan/tilt voltar a transmitir e de uma decisão de segurança (terminal via web
  numa rede doméstica não é trivial).

## Consequências

1. O 1.0 fica **menor e mais defensável**: nenhuma dependência externa nova.
2. `ARQUITETURA.txt` ganha a seção de camadas como vocabulário; as 9 regras
   invioláveis seguem valendo sem alteração.
3. A tela de inicialização passa a ser um instrumento de diagnóstico — a
   primeira coisa que se olha quando o robô não sobe.
