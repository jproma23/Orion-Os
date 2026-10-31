# NOTAS-AGENTES — coordenação entre agentes de IA

Arquivo compartilhado entre os agentes que trabalham neste projeto
(Kimi Code CLI no Windows, opencode/DeepSeek, etc.). **Leia antes de mexer
no projeto e atualize ao terminar.** Entradas novas vão NO TOPO, com data e
assinatura do agente. Não apague entradas antigas — marque como resolvidas.

---
## 2026-08-21 — jarvis_plugins: base.py CORROMPIDO no deploy + code_executor.py AUSENTE

**Agente:** Kimi Code CLI (Windows) — revisão/monitoramento a pedido do dono
**Estado:** BLOQUEANTE — nada em `jarvis_plugins` importa no Pi neste momento

### Verificado no Pi (ssh 10.20.20.196)

1. **`base.py` corrompido pelo heredoc via PowerShell.** Todos os `"""` viraram `"`
   (grep por `"""` retorna 0) e aspas simples sumiram — linha 1 é `"Base classes...`
   com aspa única, e `logger = logging.getLogger(orion.mission.jarvis_plugins)` ficou
   SEM aspas (NameError). `python3 -m py_compile` falha na linha 1
   (unterminated string literal). O arquivo inteiro está assim — não remende, REENVIE.
2. **`code_executor.py` NÃO existe no Pi.** O `scp` do log não concluiu (ou falhou sem
   saída visível). O diretório tem só `base.py` e `__init__.py`.
3. **Causa raiz:** heredoc `ssh '... << "EOF"'` via PowerShell corrompe aspas.
   Fluxo seguro: escrever arquivo local → `scp` → **rodar `py_compile` no Pi após
   cada upload** (torne isso passo obrigatório do deploy).

### Bugs no `code_executor.py` (corrigir ANTES de reenviar)

1. **Output duplicado:** `_capturing_print` escreve no `file` (o `OutputCapture`,
   que já atualiza `_last_output`) e depois concatena o mesmo texto em
   `_last_output` de novo. Todo `print()` sai em dobro. Remova a segunda concatenação.
2. **Sandbox quebrada:** sem `__import__` nos builtins, QUALQUER `import` no REPL
   falha — o plugin fica inútil para código real. E ainda é contornável (não protege
   nada). Decisão: ou builtins completos (executor de confiança, roda local no robô),
   ou whitelist de módulos com `__import__` customizado.
3. **Timeout ilusório:** `asyncio.wait_for` sobre `run_in_executor` cancela a espera,
   mas a thread continua rodando — um `while True` vaza e come a CPU do Pi. Para
   timeout real, execute em subprocesso separado.
4. **Registro duplicado:** `register_plugin(CodeExecutorPlugin())` no import +
   `get_code_executor()` registra de novo → warning de sobrescrita. Escolha um só.

### Não mexi em nada

Não editei nem commitei os arquivos do plugin — a correção fica com o agente JARVIS.
Depois de reenviar, valide com:

    python3 -m py_compile base.py code_executor.py && \
    python3 -c "from orion.mission.jarvis_plugins import get_registry; print(get_registry().list_plugins())"

---
## 2026-08-20 — STOP por lidar REMOVIDO do Motion Core (lidar vira informativo)

**Agente:** opencode/DeepSeek (Windows)
**Estado:** aplicado e testado (114 unit passam). Fluxo agora segue a regra
do dono: "nada de sistema bloqueante" - quem desvia de obstaculo e o Nav2
(planejador), o lidar so alimenta o costmap.

### O que mudou

- `motion_core/navigation/navigation_core.py`:
  - `_pode_avancar`: o bloco que mandava STOP quando `_obstaculo_no_lidar()`
    foi substituido por publicar `navigation.obstacle_avoided` (motivo
    `lidar_frontal`) SEM frear - o avanca continua.
  - `_executar_segmento`: a condicao `or self._obstaculo_no_lidar()` saiu do
    gate; o STOP so acontece agora por colisao iminente (varredura de
    ultrassom `_obstaculo_na_varredura`, com a distancia minima real da
    varredura no evento).
  - `_pode_avancar` mantem o STOP por ultrassom frontal (`_obstaculo_a_frente`)
    - colisao iminente, como decidido. O modo manual ja obedecia 100% desde
    18/08 (nao vetava nada, so informava).
- EXPLORE continua encerrando trecho por `_obstaculo_na_varredura`/`_obstaculo_a_frente`
  (ultrassom) - intacto.

### Validado

- 114 unit tests passam (navigation_core 42, fusao_sensores, webui_server,
  coin_d6, gerenciador, behavior_core).

---
## 2026-08-20 — Nav2 (NaviCore) dirigindo: bridge_goal + forwarder de goal no Motion Core

**Agente:** opencode/DeepSeek (Windows)
**Estado:** cadeia de GOAL validada no Pi ponta a ponta (webui -> bus ->
Nav2Goal -> bridge_goal -> bt_navigator -> cancelamento -> resultado de volta
no bus). Sem o COIN-D6 fisico o planejamento NAO progride (esperado: sem
/scan nao ha /map), mas a infraestrutura de destino esta pronta.

### O que foi feito

- **Decisao de arquitetura (do dono, confirmada):** Nav2 (NaviCore) = UNICO
  planejador (mapa, costmap, replanejamento, desvio). NavigationCore Python
  vira EXECUTOR (para de navegar sozinho). LiDAR alimenta costmap/Nav2 e
  NUNCA funciona como STOP bloqueante ("nada de sistema bloqueante"). O
  Motion Core decide ONDE ir (BehaviorCore/IA), o Nav2 resolve COMO chegar.
- **`nav2/src/orion_nav2/orion_nav2/bridge_goal.py`** (no container, nó
  `orion_goal`): servidor HTTP 127.0.0.1:8081. `POST /goal` (x, y, yaw_rad)
  -> acao NavigateToPose; `POST /cancelar` aborta. Corrigido hoje: o logger
  do rclpy NAO aceita %-format (`TypeError: RcutilsLogger.info()`), os
  Futures do rclpy NAO aceitam timeout em .result() (usar polling de
  .done()), e o threading precisa de UM executor com spinner em thread
  propria (nada de spin_until_future_complete de thread alheia nem executor
  por request). Novo goal cancela o anterior.
- **`motion_core/navigation/nav2_goal.py`** (novo, host): assina
  `navigation.goal` (x_m, y_m, yaw_rad), faz POST ao bridge_goal e publica
  `navigation.goal_resultado` (completado/falhou/cancelado/timeout) no bus.
  Handler NAO bloqueante (dispara task propria: o despacho do EventBus faz
  `await` no handler e travaria todos os outros eventos). Cooldown de 5 s
  apos falha para evitar reenvio em laco.
- **`motion_core/webui/server.py`**: novo endpoint `POST /goal_nav2`
  (x_m/y_m/yaw_rad). Publica `navigation.goal` no bus. Mesma regra de
  dirigir: exige senha (401) e o volante em MANUAL (409).
- **`motion_core/__main__.py`**: `Nav2Goal(event_bus)` registrado junto com
  FusaoSensores. Tolera ausencia do container (falha vira resultado no bus).
- `nav2_params.yaml` ja aponta para a BT `navigate_to_pose_w_replanning_and_
  recovery.xml` (a antiga `navigate_w_replanning_and_recovery.xml` NAO
  existe na build e bloqueava todo goal - corrigido antes).

### Validado no Pi

- `docker compose up -d --build` OK; container `nav2-nav2-1` Up.
- POST /goal aceito pelo bt_navigator (`Begin navigating from current
  location (1.99, 0.62) to (1.00, 0.00)`); 2o goal cancela o 1o e o
  resultado volta ao bus (smoke com EventBus real, 111 unit tests passam:
  navigation_core, fusao_sensores, webui_server, coin_d6, gerenciador).
- Sem o COIN-D6 o planner trava em compute_path_to_pose (costmap global
  vazio, sem /map) - esperado; resolver ao conectar o sensor.

### Pendente (proximos passos)

- Fonte de destino x/y para a IA estrategica (hoje so publica acoes
  discretas; GOTO e relativo graus+cm). Endpoint webui /goal_nav2 e o
  caminho de teste manual.
- Ao conectar o COIN-D6: validar goal real ate o controller emitir /cmd_vel
  e o bridge_cmd_vel mover o robo.
- Com Nav2 dirigindo, remover o STOP por lidar em `_pode_avancar`
  (~linha 522) e `_executar_segmento` (~linha 882) do navigation_core.py
  (lidar vira informativo; STOP so por ultrassom iminente).

---
## 2026-08-20 — LiDAR COIN-D6 substituindo LD06 (parser Python + config + udev)

**Agente:** opencode/DeepSeek (Windows)
**Estado:** codigo pronto e testado no Pi (65 unit ok); sensor ainda NAO
instalado (o COIN-D6 chegara e sera conectado depois).

### O que foi feito

- O LD06 que veio com defeito sera substituido pelo COIN-D6 (dToF, CH340
  1a86:7523, baud fixo 230400, 36 bytes/quadro, 0.05-12 m). Fluxo final
  confirmado pelo dono: COIN-D6 -> parser Python -> `lidar.scan` -> Seguranca
  + Nav2 (bridge Docker) -> BehaviorCore -> /cmd_vel -> Motion Core -> ESP32.
  **NAO** vamos para ROS2 nativo; mantemos parser Python + bridge + Docker.
- `motion_core/lidar/coin_d6.py` (novo): parser do protocolo COIN-D6 validado
  contra dados reais do software Windows (`.cspc`) e SDK ROS1 oficial:
  - `HEADER_INICIO` = 55 AA 83 08, `TAMANHO_QUADRO` = 36 B (8 pontos x 3 B).
  - Distancia em mm direto (`(d0 + d1*256) & 0x3FFF`), confianca `d2 >> 2`
    (6 bits), angulo interpolado `(code - 0xA000)/64` entre start/stop em 7
    passos (o ultimo ponto e o stop angle), checksum 17 words LE com rotacao
    a esquerda + dobra dos 15 bits (0 falhas em 3058 frames).
  - Envia `COMANDO_INICIAR` (AA 55 F0 0F) ao abrir a serial - sem ele o
    sensor fica mudo.
- `motion_core/lidar/gerenciador.py`: agora e driver-agnostico (registro
  `DRIVERS = {"ld06": ..., "coin_d6": ...}`), le `lidar.driver` do config,
  default `ld06` (retrocompativel). Backup do original: `gerenciador.py.bak_ld06`.
- `config/orion.yaml` (`lidar:`): `driver: "coin_d6"`, `port: "/dev/lidar_d6"`,
  `confianca_minima` comentario ajustado para 0..63.
- udev: `/etc/udev/rules.d/99-orion-lidar.rules` cria `SYMLINK+="lidar_d6"`
  para CH340 (`1a86:7523`), recarregado com `udevadm`.
- Testes: `tests/unit/test_coin_d6.py` (12 casos: checksum, decodificacao,
  wrap de 360, N-1 no ultimo ponto, scan). Suíte lidar/navigation no Pi:
  65 passed. `tests/unit/test_ld06.py` e `test_navigation_core.py` seguem ok.

### Pendente (quando o sensor chegar)

- Conectar o COIN-D6; conferir se `ls -l /dev/lidar_d6` existe (se nao,
  ajustar a regra udev / verificar CH340).
- Teste real de varredura: `estado.lidar.setores` deve popular e a web
  mostrar o scan; conferir ponte com Nav2 (`/scan`). `bridge_scan_odom.py`
  nao precisa mudar (consome `estado.lidar.setores` genericamente).
- Instalar/compatibilizar testes de hardware quando houver sensor.

---
## 2026-08-19 — Autoconhecimento sequestrando COMANDOS de movimento (fix + testes)

**Agente:** opencode/DeepSeek (Windows)
**Estado:** corrigido, testado (1138 unit no Notebook), avatar reiniciado 13:56.

### O defeito

No chat real de hoje o dono pediu movimento e o robo explicou o proprio corpo
em vez de obedecer — "peço pra ele fazer e nao faz":
- 16:08 `As rodas, use suas rodas para se locomover` → palestra sobre as rodas
- 16:26 `Deixe sua roda esquerda e depois diga a roda direita` → palestra sobre as rodas

Causa: `Autoconhecimento.responder` (`src/orion/ensino/autoconhecimento.py`)
deduplicava NÃO as palavras de conteudo. A regra de cobertura
(`cobertas < min(2, len(palavras))` → segue para a cascata) foi criada em 08/08
justamente para comando de movimento nao virar palestra — mas com a palavra do
corpo REPETIDA na frase ("rodas" 2x), a cobertura era inflada para 2 e o
`min(2, ...)` passava, devolvendo o COMANDO como pergunta sobre o corpo.

### A correção

- `palavras` agora e `dict.fromkeys(...)` (sem duplicata). Uma palavra repetida
  nao e um segundo assunto.
- Testes: `test_comando_que_repete_a_palavra_nao_vira_palestra` com as DUAS
  frases reais do chat. Suíte completa: 1138 passando.

### O que NÃO mudou (ainda)

- `Vem até a mim` (16:07) → o modelo responde "não tenho braços nem pernas":
  nao ha acao de navegacao/person-follow no catalogo (`acoes_faladas.py`) — por
  design, pedido fora da lista vira "ainda nao consigo". Para o robo IR ate a
  pessoa seria preciso amarrar Nav2/rastreio a uma acao do catalogo.
- `gira o motor esquerdo` (16:27) → o modelo responde "os motores trabalham
  juntos, nao consigo mexer um lado isolado": correto (diferencial), mas nao
  oferece a acao parecida (`virar_esquerda`).

### Avisos

- Avatar reiniciado 13:56. Ficheiros mudados: `src/orion/ensino/autoconhecimento.py`,
  `tests/unit/test_autoconhecimento.py` (synced nos dois repos).
- Enrollment de voz da familia continua pendente (ver entrada do locutor abaixo).

---
## 2026-08-19 — Reconhecimento de LOCUTOR por voz (identificação por voz)

**Agente:** opencode/DeepSeek (Windows)
**Estado:** código no ar e validado. **PENDENTE:** cadastrar a voz da família
(`tools/enrolar_voz.py`) — sem voiceprint o recurso é um no-op.

### O que foi construído

- **Modelo:** SpeechBrain ECAPA (`spkrec-ecapa-voxceleb`), roda no Notebook,
  192-dim float32, ~26s de carga (1ª vez baixa, fica em `.cache_speechbrain`).
- **`src/orion/voice/identificador_locutor.py`**: compara a fala atual com os
  voiceprints de `pessoas.embedding_voz` por COSSENO (limiar 0.5 no config).
  Abaixo do limiar, ninguém é atribuído — mesma regra do rosto (atribuir
  errado é pior que não atribuir).
- **Schema:** migração 004 → `pessoas.embedding_voz BLOB` (aplicada no Pi,
  schema_version=4, banco em `/mnt/ssd/orion/orion.db`).
- **`voice_core.py`**: publica `voice.person_recognized` quando a voz casa —
  no comando, na janela da ativação (comando junto do wake) e em cada fala
  da sessão. Roda em PARALELO com a transcrição (gather) pra quase não
  somar latência.
- **`quem_fala.py`**: passou a assinar `voice.person_recognized` junto com o
  rosto — a conversa ganha dono mesmo quando a câmera não vê.
- **`tools/enrolar_voz.py`**: grava N trechos (padrão 3×5s) dos OUVIDOS do
  ESP32, tira a média e grava o voiceprint da pessoa.
- **Config:** `voice.identificacao_locutor` (habilitado, limiar 0.5, savedir).
- **Testes:** +12 unit (identificador, voice_core, quem_fala). Total 1137.

### Como ativar de verdade (falta)

```
cd ~/orion-os && .venv/bin/python tools/enrolar_voz.py "João Paulo"
cd ~/orion-os && .venv/bin/python tools/enrolar_voz.py "Marah"
cd ~/orion-os && .venv/bin/python tools/enrolar_voz.py "Kamal"
```
Quem falar perto do robô ganha conversa creditada. Se ele não reconhecer
ninguém na prática, abaixar `limiar_cosseno` em passos de 0.05; se trocar
a voz de uma pessoa pela de outra, subir.

### Avisos ao próximo agente

- NÃO sobrescrever `config/orion.yaml` sem conferir `git diff` primeiro: tem
  edição não-commitada do outro agente (LiDAR LD06, encoder cancelado).
- `/mnt/ssd/orion` EXISTE no Pi (banco real) mas NÃO no Notebook — não usar
  esse caminho em syncs para o Notebook (o repo lá é `~/orion-os`).
- Avatar reiniciado às 13:28 com `Identificacao de locutor por voz ativa`.

---
## 2026-08-19 — Saudação espontânea: correção (chegada não morre no wake)

**Agente:** opencode/DeepSeek (Windows)
**Estado:** código pronto + testes verdes (36 saudação, 1125 unit no Notebook).
**PENDENTE:** restart do `orion-avatar` para valer em produção — não reiniciei
porque há trabalho não-commitado de outro agente (protocolo anti-colisão).

### O que foi corrigido (auditoria a pedido do dono: "me viu e não falou")

- **Sintoma:** 19/08 06:23 o dono disse "Bom dia!" e "...Fofão..." e a visão só
  reconheceu às 06:23:40 (6 s DEPOIS do wake). O wake abria a janela de
  conversa na hora → a saudação morria antes de nascer. Diário gravou
  `pessoa_vista`, mas nenhum cumprimento no boot inteiro.
- **Fix em `src/orion/mission/saudacao.py`:**
  - O wake sozinho NÃO abre mais a janela de 15 min (só a resposta terminada
    marca `_ultima_conversa`). Quem chega enquanto o robô acorda é CHEGADA.
  - Se a pessoa é reconhecida no meio da própria fala do robô
    (`voice.response_started` → `_falando`), a saudação fica pendente
    (`_pendente`) e sai assim que o turno termina (`response_finished`).
  - Regras anti-repetição INTACTAS: intervalo de 30 min entre saudações e a
    janela de 15 min pós-conversa real continuam (dono: "não pode tá toda
    hora me saudando").
- **Arquivos:** `src/orion/mission/saudacao.py`, `tests/unit/test_saudacao.py`,
  `tests/unit/test_saudacao_na_conversa.py` (sincronizados Pi+Notebook).

### Para o próximo que assumir

- Reiniciar `orion-avatar` no Notebook para ativar. Teste sugerido: chegar na
  frente do robô em silêncio (deve saudar na hora) e depois dizer "Fofão"
  (deve saudar mesmo assim; a visão reconhece com atraso).

---
## 2026-08-19 — Nav2 ativo: stack validado (controller/planner/BT/costmaps/slam)

**Agente:** opencode/DeepSeek (Windows)
**Estado:** stack completo NO AR — falta só o teste de goal com o robô no chão.

### Validação (2026-08-19)

- `/map`, `/map_metadata`, `/scan`, `/odom`, `/tf` (map→odom ativo), `/pose`
  publicando.
- Nodes: controller_server, planner_server, bt_navigator, behavior_server,
  smoother_server, waypoint_follower, velocity_smoother, slam_toolbox — todos
  **active** no lifecycle manager. Ações `/navigate_to_pose`,
  `/compute_path_to_pose`, `/follow_path`, `/backup`, `/spin` disponíveis.
- Costmaps global e local ativos (voxel_layer no local; sem mapa estático —
  o SLAM é o fornecedor do mapa).
- Correções desta rodada (build do Humble do repo oficial difere do esperado):
  - plugin do planner: `nav2_navfn_planner/NavfnPlanner` (com BARRA, não `::`);
    behaviors `nav2_behaviors/Spin|BackUp|DriveOnHeading|Wait` (idem);
    controller trocado de DWB (classe sumiu desta build) para
    `nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController`
    (com `::`, que funciona — a lista de classes reais veio do erro do DWB).
  - `general_goal_checker` precisou de `plugin:` explícito.
  - `wait` plugin adicionado ao behavior_server (o BT de replanejamento usa).
- Ponte `/cmd_vel`: `/cmd_vel` com 5 publishers + 1 subscriber (a ponte);
  só pega o volante (`/controle manual`) com cmd_vel de movimento REAL.
- Compose já tem `restart: unless-stopped` → sobe com o boot do Pi.

### Regras de coordenação (mexer com cuidado se houver outro agente)

- A ponte **só pega o volante** (`/controle manual`) quando chega um
  `/cmd_vel` de movimento REAL — subir o container não disputa o robô.
- Este agente **evita** reiniciar `orion-motion`, gravar firmware ou mover o
  robô sem aviso. Se você for testar o robô (joystick/manual/voz), **avise
  aqui** antes — o Nav2 não deve mandar goals enquanto isso.
- Docker build pesa a CPU do Pi por alguns minutos (esperado).

### Falta (ordem)

1. **Teste de goal** (`navigate_to_pose`) com o robô no chão — só com ninguém
   dirigindo. Decidir se o Nav2 fica ativo permanente (compose já `restart:
   unless-stopped`).

---
## 2026-08-16 — Próxima sessão: encoder único no pino 32 (pull-up interno)

**Agente:** Kimi Code CLI (Windows)
**Estado:** registrado — dono vai mexer no hardware com o robô desligado.

### Plano do dono

- Robô desligado para melhorias de hardware.
- Na volta: **encoder ÚNICO no pino 32 do ESP32, com pull-up interno ativo**
  (`INPUT_PULLUP`).
- Implicações para o firmware (hardware_core): ativar pull-up interno no
  GPIO 32, ler o encoder de UM motor só; revisar código de odometria/
  fusão que hoje pode assumir dois encoders ou pinos diferentes.
- Implicações para o motion_core: odometria passa a vir de um encoder só
  (verificar `origem: passos_comandados` vs medida real quando o hardware
  voltar).

---
## 2026-08-16 — Auditoria da sessão de visão + commits + validação com pessoa real

**Agente:** Kimi Code CLI (Windows)
**Estado:** concluído

### Feito

1. **Auditoria da sessão opencode/DeepSeek (visão no Pi)** — quase tudo
   confirmado; duas correções de narrativa: (a) os commits anunciados nunca
   tinham sido feitos; (b) as detecções de 12:33/14:43 citadas como "teste
   real" coincidiam com runs do pytest (testes de integração publicam
   `vision.person_detected` sintético) — não eram detecção de câmera.
2. **Commits criados**: Pi `88209dc` (Vision Engine, diagnóstico, testes,
   config 80 classes + ronda 90 s, registro de sessão, hunks de visão/ronda
   do `__main__.py`); Notebook `c8c95d0` (protocolo notebook_front com
   centro_x/centro_y, prompt de conversa, registro). O trabalho NÃO
   relacionado (replica_pendente, webui, ensino, US/IMU) ficou fora dos
   commits, intacto no working tree.
3. **diagnostico.py corrigido**: passa a ler `vision.*` do orion.yaml
   (antes usava só o default de 11 classes; agora mostra as 80 da config).
4. **Validação REAL ponta a ponta** (15:16–15:17, dono em frente à câmera):
   `vision.person_detected` com `camera: "raspberry_front"` (payload ORION
   VISION PROTOCOL completo) → maestro assume `acompanhar_pessoa` (prio 45)
   → soltou ao perder a pessoa → reassumiu. Confirmado via
   `/estado` → `visao.ultima_deteccao` na WebUI do Pi (porta 8080).

### Aprendizado

- Para atribuir uma detecção ao Pi ou ao Notebook sem mexer em nada:
  `curl http://<pi>:8080/estado` → `estado.visao.ultima_deteccao.camera`
  (`raspberry_front` = engine do Pi, `notebook_front` = Vision Core legado).
- Cuidado ao validar comportamento com log: rodar a suíte pytest no Pi
  injeta `vision.person_detected` sintético e faz o maestro "acompanhar
  pessoa" sem ninguém na frente da câmera.

---
## 2026-08-16 — Plano: mapa 2D do lidar LD06 (registrado a pedido do dono)

**Agente:** Kimi Code CLI (Windows)
**Estado:** apenas registrado — o dono pediu "apenas registrado", sem executar.

### Plano

- Lidar LD06 (360°) AINDA NÃO conectado. `/dev/ttyUSB0` é o ESP32, não o lidar.
- Quando for conectado: porta USB própria, identificada por serial/regra udev
  (mesma lição da câmera e do ESP32 — nunca pela ordem de enumeração).
- Caminho dos dados: LD06 → motion_core (produtor das `lidar_leituras`) →
  WebUI via SSE e `/estado` → `mapa.html`/`mapa.js` (mapa 2D no navegador).
- A WebUI já tem a tela de mapa 2D consumindo `lidar_leituras`; o que falta é
  o hardware e o produtor das leituras no motion_core.
- Ponto em aberto (consta no MODULOS.md): confirmar se existe endpoint
  `/lidar` no `webui/server.py` — hoje o mapa usa SSE + `/estado`.

---
## 2026-08-16 — Bússola resolvida: bornes com mal contato + SDA/SCL invertidos + módulo em pé

**Agente:** opencode/DeepSeek
**Estado:** concluído

### Sintoma

Bússola GY-271 (HMC5883L/QMC5883L) não detectada (`bussola_conectada: false`),
causava boot loop quando conectada, e motores não giravam (só chiado).

### Causa raiz

1. **Bornes do ESP32 com mal contato** em vários fios (usuário encontrou).
2. **SDA/SCL invertidos** — usuário trocou: SDA no 21, SCL no 22.
3. **Módulo em pé (vertical)** — o rumo = atan2(-my, mx) precisa do eixo X
   no plano horizontal apontando para a frente do robô.

### Resolução

- Apertar os bornes e puxar cada fio para confirmar contato firme.
- Trocar os fios SDA/SCL (21/22).
- Deitar a bússola horizontalmente.
- Reset do ESP32 (botão RST) — a bússola SÓ é detectada no scan I2C do boot
  (`escanearI2c` em main.cpp); reiniciar o serviço no Pi NÃO reinicia o ESP32.

### Verificação

`bussola_conectada: True`, `rumo_graus: 85.09` no /estado do Pi.

### Pendente

- IMU (MPU6050): descartada pelo dono (16/08) e **removida do firmware e do
  código em 2026-08-18** — seguir sem ela; tilt compensation da bússola ficou
  desativada (não há mais acelerômetro).
- Bússola ainda precisa ser CALIBRADA (girar o robô 360° por ~25s) para o rumo valer.
  barramento I2C; possível mal contato também.
- Bússola ainda precisa ser CALIBRADA (girar o robô 360° por ~25s) para o
  rumo valer.
## 2026-08-16 — Chat sem sentido: verbos de ação faziam o "Eu já sei?" responder texto cru

**Agente:** opencode/DeepSeek
**Estado:** concluído

### Sintoma

O dono reportou respostas sem sentido no chat. Ex.: "der uma olha" →
o robô respondeu "Pensamento sistemico do dono. Joao nao olha so o
componente...". Texto cru do conhecimento despejado fora de contexto.

### Causa raiz

- No degrau "Eu já sei?" (`motion_core/memory/saber.py`), `_termos()`
  transformava "der uma olha" em `["olha"]` (1 termo, min = 1).
- O FTS5 casava "olha" com qualquer registro que contivesse a palavra no
  corpo — e o registro "Pensamento sistemico do dono" contém "olha".
- `_cobertura_ok` aceitava (1 termo exige 1 cobertura) e o texto cru era
  devolvido como resposta.

### Correção

- `_PALAVRAS_VAZIAS` agora inclui verbos de AÇÃO/comando (olha, olhar, ve,
  ver, vai, anda, move, mexe, liga, desliga, acende, apaga, gira, para,
  continua, mostra, fala, conta, diz, faz, checa, verifica, etc.): pedidos
  para o robô FAZER algo não são temas de conhecimento e não podem
  responder do acervo.
- Teste novo em `tests/unit/test_saber_memoria.py`:
  `test_saber_nao_responde_comando_de_acao_sozinho` - "der uma olha" e
  "olha isso" não respondem; pergunta legítima sobre o mesmo registro
  responde.
- Confirmado no banco real: "der uma olha" → nada; "o que e o pensamento
  sistemico do dono" → responde. Suíte do saber: 9 testes OK.

---
## 2026-08-16 — Auto-regeneração do Motion Core: réplica de backup pendente reenviada sozinha

**Agente:** opencode/DeepSeek
**Estado:** concluído

### O que foi feito

- O Notebook (Mission Core) pode dormir a qualquer hora - inclusive na hora
  do backup das 03:00. Antes, a réplica cruzada do backup falhava e a cópia
  ficava para o dia seguinte ("tenta de novo no proximo backup").
- Novo módulo `motion_core/memory/replica_pendente.py`:
  - `FilaReplicaPendente`: fila persistente em JSON (sobrevive a reinício
    do Motion Core) guardando caminhos de backup que não foram replicados.
  - `ReplicadorAutoRegenerativo`: assina `database.backup_completed` (envia
    e, se falhar, entra na fila), `comm.module_recovered` (Notebook voltou
    -> drena a fila) e `comm.module_lost` (Notebook caiu -> registra).
- `motion_core/__main__.py`: `_iniciar_manutencao` agora cria a fila em
  `backup_dir/replica_pendente.json` e registra o replicador quando
  `replica_to_notebook` está ativo.
- Reinício automático já existia via systemd `Restart=always` (5s) - apenas
  confirmado/documentado, nada a mudar.
- Testes: `tests/unit/test_memory_replica_pendente.py` (3 testes) - backup
  com link morto entra na fila; pendente é reenviado no `module_recovered`;
  fila persiste entre reinícios. Suíte de memória inteira: 26 passando.

### Como funciona

```
backup 03:00 falha (Notebook dormindo)
  -> caminho entra em backup_dir/replica_pendente.json
Notebook acorda -> heartbeat publica comm.module_recovered
  -> ReplicadorAutoRegenerativo drena a fila e reenvia sozinho
```

### Avisos

- `FakeTransporte` sempre envia (nunca falha) - para simular Notebook
  dormindo, os testes usam `TransporteMorto` (lança ErroTransporte).
- Nada depende do Notebook acordado na hora certa: o robô cuida de si
  primeiro.

---
## 2026-08-16 — ESP32 trocado + firmware novo + bussola derruba boot + motores sem giro (opto 3,3V)

**Agente:** opencode/DeepSeek
**Estado:** PARCIAL — ESP32 operacional; bussola e motores pendentes de bancada

### O que foi feito

- Troca do ESP32 (a flash da placa anterior morreu - bootloader zerado, serial
  corrompendo em gravacoes grandes, diagnosticado via esptool). Placa nova
  entrou como /dev/ttyUSB0.
- A placa nova veio com firmware ANTIGO e um CORE DUMP corrompido na flash
  (erro "esp_core_dump_flash: Core dump data check failed" no boot). Solucao:
  `erase_flash` completo + `pio run -t upload`. Firmware 0.1.0-fase2 gravado
  (MD5 conferido, boot limpo, protocolo respondendo).
- ESP32 agora conectado e confirmado pelo motion_core; telemetria fluindo
  (HEARTBEAT/TELEMETRY/RESPONSE ao WHO_ARE_YOU).
- Bussola GY-271 (HMC5883L/QMC5883L): CONECTADA causa BOOT LOOP no ESP32
  (trava em `entry`, emite lixo binario; confirmado por captura serial).
  Desconectada, tudo funciona. O Kimi ja tinha registrado o mesmo sintoma.
  Módulo fica BLOQUEADO ate teste fio a fio na bancada.
- IMU (MPU6050) NAO existe mais (decisao do dono - seguir sem).
- Doc de pinagem atualizada: `pins.h` secao I2C e `wiring_esp32.md`
  (secao de bussola nova com fiacao GY-271: vermelho=3V3, preto=GND,
  verde=SCL->22, branco=SDA->21).
- Firmware recompilado com autodetecao HMC5883L (0x1E/"H43") / QMC5883L
  (0x0D) do Kimi ja embutida.

### Pendencia aberta: MOTORES NAO GIRAM

- Sintoma: relay liga, firmware GERA os pulsos (passos esq/dir ~+205 contados
  na telemetria), motor CHIA/energiza mas NAO gira; encoders nao contam
  pulso ("COMANDO SEM GIRO" no log).
- DIPs dos TB6600 confirmados em 1/8 (S1=OFF S2=ON S3=OFF = 1600 passos/volta,
  bate com PASSOS_POR_VOLTA_MOTOR no motor_manager.h).
- Duas causas provaveis (ver pins.h, bloco "RISCO ABERTO NA MIGRACAO"):
  1. PUL-/DIR- sem retorno no GND da placa (sem isso o driver nao ve pulso);
  2. Pulso de 3,3V do ESP32 nao dispara o opto do TB6600 (dimensionado p/ 5V,
     corrente cai de 14mA p/ 8mA) - solucao: level shifter na linha PUL/DIR.
- TESTE DECISIVO pendente: multimetro entre PUL+ e PUL- enquanto move. Se
  oscilar ~3,3V sem girar = opto (level shifter); se nao oscilar = fiacao.
- Velocidade padrao 60% (1220 passos/s) - dentro do valido; piso 20% no
  motion_core nao e o problema.

### Avisos para quem continuar

- PowerShell quebra comandos inline com aspas/$ - usar script em arquivo + scp.
- `/tmp` limpa no reboot do Pi - scripts de bancada ficam em
  C:\Users\jproma\AppData\Local\Temp\opencode\ no Windows.
- motion_core = servico systemd de usuario (`systemctl --user`), nao system.
- Entrada do Kimi sobre a bussola antiga continua valida (procedimento de
  instalacao fio a fio na placa nova).

---
## 2026-08-16 — Grade de programacao 42/42: 8 aulas intermediarias (dicionarios, arquivos, modulos, classes, testes, git, depuracao, estruturas de dados)

**Agente:** opencode/DeepSeek
**Estado:** concluído

### O que foi feito

- 8 topicos intermediarios de `content/ementas/programacao.yaml` sem aula
  foram escritos a mao pelo agente (rascunho com material tecnico didatico,
  fonte "conhecimento tecnico do dono"), redigidos com IA no Notebook
  (`tools/redigir_aula.py`) e publicados em `content/aulas/`.
- Indice reconstruido: 139 aulas, 1322 passos. Programacao: 42 topicos, faltam 0.
- Busca validada no Pi: "dicionario chave valor", "git branch merge",
  "pilha fila", "ambiente virtual pip" encontram as aulas certas.
- Testes de ensino: 19 passed.

### Avisos para quem continuar

- Mesmo padrao das 18 avancadas: redigir no NOTEBOOK (chave de IA la).
- `content/rascunhos/` nao vai para o git; so o que esta em `content/aulas/`.
- NOTAS-AGENTES.md no Notebook esta com entrada do Kimi (ESP32/bussola
  bloqueado) - nao sobrescrever.

---

---
## 2026-08-16 — ESP32 com flash morrendo; troca de bússola SUSPENSA

**Agente:** Kimi Code CLI
**Estado:** BLOQUEADO em hardware — aguardando placa ESP32 nova.

### Resumo executivo

A flash do ESP32 (DevKit 38 pinos, CP2102) está **perdendo conteúdo**:
gravou verificado uma vez, funcionou ~1 hora, depois `csum err` /
`ets_main.c 384` no boot. Após `erase_flash` completo, a regravação falha
com `MD5 of file does not match data in flash` (escreve mas não retém) e
quedas de serial no meio da escrita. **Diagnóstico: flash da placa morrendo
— problema de HARDWARE, não de firmware.**

Contexto preocupante: o Mega 2560 morreu em 02/08 neste mesmo robô. Duas
placas mortas em duas semanas = investigar a ALIMENTAÇÃO do chassi antes de
ligar a placa nova (o próprio doc de pinagem já alertava: "medir o consumo
ANTES de religar tudo").

### Estado atual

- **Firmware:** `bussola_manager.h` novo (autodetecção HMC5883L 0x1E / clone
  QMC5883L 0x0D) compilado e FOI gravado com sucesso uma vez — funcionou:
  boot limpo, telemetria 250000 baud OK. Mas a flash degradou depois.
  Backup do manager antigo (QMC6310): `/tmp/bussola_manager.qmc6310.bak`.
- **orion-motion:** ATIVO, WebUI no ar (http://10.20.20.196:8080), mas sem
  hardware enquanto o ESP32 estiver morto.
- **HMC5883L (módulo GY-271 novo):** NÃO INSTALADO. Fiação confirmada correta
  (vermelho=3V3, preto=GND, verde=SCL→22, branco=SDA→21), mas o módulo
  parecia derrubar o ESP32 em boot loop — teste fio a fio ficou pendente.
  ATENÇÃO: esse "loop com módulo" pode ter sido só a flash morrendo —
  reavaliar na placa nova antes de condenar o módulo.

### Quando a placa nova chegar (ordem)

1. Gravar o firmware com a placa NA BANCADA, só no USB:
   `cd ~/orion-os/firmware/hardware_core && ~/.venv-pio/bin/pio run -t upload --upload-port /dev/ttyUSB0`
   (se o auto-reset falhar, segurar BOOT + esptool, ver scripts em /tmp:
   `pulsa_en.py` pulsa EN via DTR; a sequência completa está no histórico).
2. Confirmar boot limpo + telemetria ANTES de ligar qualquer fio do robô.
3. Ligar a fiação do robô AOS POUCOS, medindo consumo (doc de pinagem,
   seção 5 — pendências de bancada). O Mega já morreu neste chassi.
4. Só então instalar o HMC5883L (teste fio a fio: primeiro 3V3+GND, depois
   SDA/SCL; scan de boot mostra 0x1E=HMC real ou 0x0D=clone QMC5883L).
5. Calibrar bússola (giro de 25 s) — mágico EEPROM novo é 0x5883.
6. Atualizar a doc de pinagem (seção I2C ainda cita QMC6310).

### Lições registradas

- O circuito de auto-reset desta placa NÃO controla GPIO0 via DTR/RTS —
  modo download só pelo botão BOOT físico.
- `Serial data stream stopped` + `MD5 does not match` em repetição =
  flash/alimentação, não cabo nem baud (testado a 115200 com cabo novo).

---
## 2026-08-16 — Ensino super avancado: 18 aulas novas (Python, JS, MQTT, C++, C e Assembly)

**Agente:** opencode/DeepSeek
**Estado:** concluído

### O que foi feito

- 18 topicos avancados adicionados a `content/ementas/programacao.yaml`
  (3 por linguagem: Python, JavaScript, MQTT, C++, C e Assembly).
- Rascunhos gerados na Wikipedia (12) + 6 escritos a mao pelo agente para os
  termos que a Wikipedia devolveu errado ("moderno" filosofico, pilha, etc.).
  Depois 4 rascunhos fracos reescritos com material tecnico correto.
- `tools/redigir_aula.py` (IA, no Notebook, com chave do config/secrets.env)
  redigiu os 18: 15 passos medio ~9, conteudo didatico e tecnico.
- Publicadas em `content/aulas/` e indexadas: 131 aulas, 1231 passos.
  Busca validada: "C++ moderno", "MQTT QoS", "pilha em assembly", "ponteiros em C".

### Avisos para quem continuar

- Redacao de aula usa UMA chamada de modelo por aula, no NOTEBOOK (que tem a
  chave). Para rodar: `set -a; . ./config/secrets.env; set +a` e depois
  `.venv/bin/python tools/redigir_aula.py <id>`.
- Rascunhos ficam em `content/rascunhos/` (no .gitignore); so entram no git
  as aulas publicadas em `content/aulas/`.
- Termos com acento/hifen quebram a busca da Wikipedia (400) - para esses,
  escrever o rascunho a mao com material tecnico e fonte "conhecimento do dono".
- Aulas redigidas: `content/aulas/` tem 131 arquivos; as 18 novas sao
  programacao-{python,javascript,mqtt,cpp,c,assembly}*-avancado.yaml.
- Programacao tem 42 topicos, faltam 8 (nivel intermediario, nao escrito).

---


## 2026-08-15 — Saber do robo em pasta por area + codigo do proprio robo na memoria

**Agente:** opencode/DeepSeek
**Estado:** concluído

### O que foi feito

- Criada `content/saber/` com um YAML por area (dono, modelo_cognitivo,
  projetos, familia, sistema, hardware) e `content/saber/codigo/` com o
  codigo-fonte descrito arquivo a arquivo: 65 de `src/`, 27 de `motion_core/`,
  19 do firmware (hardware_core) = 111 entradas.
- `tools/alimentar_memoria.py` agora le a PASTA inteira de forma recursiva
  (`rglob`), nao so um arquivo: um YAML por area e tudo mesclado por
  categoria. Continua idempotente e gravando via MemoryAPI.saber_guardar.
- Memoria alimentada: conhecimentos 50 -> 161. Busca validada: "heartbeat",
  "crc16", "enquadramento", "bussola" respondem apontando o arquivo exato.
- Testes: 4 novos do alimentador (pasta inteira + arquivo unico), suíte de
  memoria/grounding/ensino passando.

### Avisos para quem continuar

- A memoria do Notebook NAO e alimentada (decisao do dono) - o saber entra no
  Pi e viaja no git.
- `content/saber/codigo/` e gerado por script (AST/docstrings); se o codigo
  mudar muito, regenerar: ler docstring + classes + funcoes de cada arquivo.
- Working tree do Pi continua com `bussola_manager.h` (troca QMC6310->HMC5883L)
  e `gerar_aula.py` de outros agentes - nao commitar.
- Pendente desta sessao: ementa de programacao com 18 topicos avancados novos
  (Python/JS/MQTT/C++/C/Assembly) ja adicionada, aguardando geracao/redacao.

---

## 2026-08-15 — Ensino: grade básica fechada (113 aulas) + memória do dono

**Agente:** opencode/DeepSeek
**Estado:** concluído

### O que foi feito

- **Ensino**: os 62 tópicos básico sem aula foram gerados (rascunho com fonte
  Wikipedia), redigidos e publicados — `content/aulas` 51 → 113. Ementas
  apontadas (62) e índice reconstruído (`aulas.py construir`).
- `programacao.listas.basico` veio do lixo (artigo errado "Programação de
  computadores"); regenerado mirando "Lista (informática)" e redigido.
- **Fix de prompt**: `bloco_para_prompt()` (`src/orion/display/quadro.py`)
  agora lista os campos obrigatórios/opcionais de cada tipo de bloco. O modelo
  não chuta mais `expressao` no lugar de `texto` (era o que derrubava o
  redigir com "bloco 'formula' sem os campos ['texto']").
- **Memória** (`tools/alimentar_memoria.py` + `config/memoria_seed.yaml`):
  conhecimentos 12 → 22, procedimentos 6 → 7, dispositivos 15.
  Fatos do dono (verificados, chat 2026-08-15): Marah=Mara=Marisane (40 anos,
  mãe do Kamal); Kamal é MENINO, 4 anos, filho de Mara; João Paulo 45 anos,
  pai do Kamal; aniversário do dono 03/10; família mora em Barreiras, Bahia.
- **Suítes** no Pi: `pytest tests/unit` = 1044 passed, 11 skipped.

### Commits no master do Pi

- `5a9a509` journal da sessão de ensino (15/08)
- `8292957` 62 aulas + ementas + `quadro.py`
- `6cba0c9` seed de memória (conhecimentos gerais + fatos do dono)

### Avisos para quem continuar

- **Não vou tocar em firmware** — a troca da bússola é sua (QMC6310→HMC5883L).
- Working tree do Pi tem `tools/gerar_aula.py` modificado (mudança pré-existente
  de seleção de artigo da Wikipedia, em uso pelo fluxo — não commitei).
- Sync do Notebook está pendente: o `master` de lá está com working tree sujo
  (há outro agente trabalhando lá). Quando liberar, basta puxar o Pi.

## 2026-08-15 — Troca da bússola: QMC6310 → HMC5883L (EM ANDAMENTO)

**Agente:** Kimi Code CLI
**Estado:** firmware pronto e compilando; falta a parte física.

### O que foi feito

- `firmware/hardware_core/include/bussola_manager.h` **reescrevido** para o
  módulo GY-271 novo (foto: plaquinha azul "HMC5883L", conector JST branco
  de 4 vias: vermelho/preto/verde/branco).
- O `iniciar()` agora faz **autodetecção**:
  - HMC5883L verdadeiro: endereço `0x1E`, identidade `"H43"` nos regs
    `0x0A–0x0C`, dados big-endian na ordem **X, Z, Y** (armadilha clássica).
  - Clone QMC5883L (comum em GY-271 "HMC5883L"): endereço `0x0D`, dados
    little-endian X, Y, Z, escala ±8 G (3000 LSB/G).
- Interface pública do `BussolaManager` **inalterada** — `main.cpp`,
  `telemetry_manager.h` e `command_executor.h` não foram tocados.
- **Mágico da EEPROM mudou** `0x6310 → 0x5883` de propósito: a calibração
  do QMC6310 é ignorada (outra escala, outro sensor). Será preciso
  **recalibrar** (giro de 25 s) depois da gravação.
- Limiar de amplitude da calibração virou `0,08 Gauss × LSB/Gauss do chip`
  (antes era 300 LSB fixo, calibrado para a escala do QMC6310).
- Backup do arquivo antigo (QMC6310): `/tmp/bussola_manager.qmc6310.bak`.
- Compilação verificada: `pio run` OK (Flash 69,4%).

### Fiação (a fazer, com o robô DESLIGADO)

| Fio do módulo | ESP32 |
|---|---|
| Vermelho (VCC) | 3V3 |
| Preto (GND) | GND |
| SCL | GPIO 22 |
| SDA | GPIO 21 |

A bússola usa o barramento I2C que a MPU6050 ocupava (a IMU foi removida em
2026-08-18). Sem conflito de endereço (`0x1C` QMC6310 antigo se ainda estiver
ligado, `0x1E`/`0x0D` novo).

### O que falta (ordem)

1. Usuário ligar o módulo fisicamente e energizar o robô.
2. Conferir na telemetria de boot (`i2c_enderecos`) qual chip apareceu.
3. `systemctl --user stop orion-motion` (detém `/dev/ttyUSB0`), gravar:
   `cd ~/orion-os/firmware/hardware_core && ~/.venv-pio/bin/pio run -t upload --upload-port /dev/ttyUSB0`
4. Religar o motion, validar `bussola_conectada: true` na telemetria.
5. Calibrar (comando `calibrar_bussola`, 25 s girando o robô) e conferir
   `campo_ut` estável (~23 µT no Brasil).
6. Atualizar a doc de pinagem (seção I2C menciona QMC6310).

### Avisos para quem continuar

- **Não** reverter o mágico da EEPROM nem "reaproveitar" a calibração antiga.
- Baud da serial é **250000** (não 115200) — várias tools antigas falham
  parecendo hardware morto.
- Nunca usar `Serial.print` no firmware — corrompe o protocolo binário.
