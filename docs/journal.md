# ORION OS — Jornal de Recuperação

Log cronológico do desenvolvimento. Cada entrada é escrita **no momento em
que a etapa acontece**, não em resumo de fim de sessão. Objetivo: qualquer
sessão nova (minha ou do Claude) consegue ler este arquivo e saber
exatamente onde o projeto parou, o que já funciona e o que falta.

Formato de cada entrada: data, o que foi feito, estado resultante, próximo
passo.

---

## 2026-07-16

- Recebido o scaffold inicial do projeto (`orion-os-tcc.zip`) via e-mail,
  transferido do celular para o Raspberry Pi por servidor de upload
  temporário (encerrado após uso) e extraído em `~/orion-os`.
- Repositório git inicializado em `~/orion-os` (ainda sem commits).
- Decisão: desenvolvimento vai seguir `PLANO_IMPLEMENTACAO.md` fase por
  fase, sem pular etapas, sempre com os testes da fase passando antes de
  avançar (regra já definida no próprio plano).
- Estado do código: apenas scaffold. Único código funcional é
  `src/orion/__main__.py` (Fase 0, imprime versão) e o teste de fumaça
  `tests/unit/test_smoke.py`. Nenhuma fase concluída ainda.
- **Fase 0 concluída.** Criado `.venv`, `pip install -e ".[dev]"` sem erros,
  `tools/check.sh` passa limpo (ruff OK, 1 teste de fumaça OK).
  `python -m orion` e `python -m orion --sim` funcionam (só imprimem versão,
  boot real ainda não existe — é da Fase 1).
- **Próximo passo:** iniciar Fase 1 — Kernel (Cap 6): Configuration Manager
  lendo `config/orion.yaml`, Logger estruturado, Event Bus assíncrono,
  Service Registry, Health Monitor + Watchdog, Boot Manager. Ler
  `docs/ses/ORION_OS_SES_Capitulo_06_Kernel_ORION_OS.md` antes de começar.

## 2026-07-17

- **Fase 1 concluída.** Implementado em `src/orion/kernel/`:
  - `config.py` — Configuration Manager: carrega `config/orion.yaml`, valida
    esquema mínimo (tipos e obrigatoriedade dos campos usados nesta fase),
    aborta com `ErroConfiguracaoInvalida` em config inválida (Cap 17 s.2).
  - `logger.py` — Logger estruturado: console legível + arquivo JSON, nível
    via `system.log_level`.
  - `event_bus.py` — Event Bus assíncrono: publish/subscribe por tópico com
    fila de prioridades (`Prioridade.CRITICA..BAIXA`), handler com erro é
    isolado (log, não derruba o bus).
  - `registry.py` — Service Registry: registro de módulos com nome, versão,
    dependências, estado (STARTING/RUNNING/DEGRADED/STOPPED), checagem de
    dependências satisfeitas.
  - `watchdog.py` — Health Monitor (rastreia heartbeats) + Watchdog (escalona
    reconectar → reiniciar → log → publica `diagnostic.error`), cada módulo
    tratado isoladamente.
  - `boot.py` — Boot Manager: executa a sequência do Cap 6 s.4 até publicar
    `system.ready`; etapas de fases futuras (Raspberry, Arduino, banco, IA,
    Vision, Motion Core) ainda não existem e são toleradas (log +
    `diagnostic.error` informativo, sem abortar o boot).
  - `__main__.py` atualizado para rodar o boot real via `asyncio`.
- **Testes:** 32 testes unitários novos em `tests/unit/` (event bus, registry,
  watchdog, config, boot). `tools/check.sh` passa limpo (ruff + pytest).
- **Verificado manualmente:** `python -m orion --sim` chega a `system.ready`
  e encerra de forma limpa (desligamento seguro para watchdog e event bus).
- **Contexto físico confirmado:** esta sessão roda no próprio Raspberry Pi
  (Motion Core) — Mega no CH340 `/dev/ttyUSB0`. O Notebook (Mission Core)
  está ligado a ele por **USB** neste momento, não pela Ethernet que o
  EDR-0018 prevê para produção; isso cai dentro do `maintenance_usb: "auto"`
  já existente na config, não é violação de arquitetura, só o link de
  desenvolvimento atual.
- **Próximo passo:** iniciar Fase 2 — Comunicação + firmware mínimo (Caps 5,
  14, 10): camada de transporte (TCP Notebook↔Raspberry, Serial
  Raspberry↔Arduino), enquadramento com CRC16, mensagens
  COMMAND/ACK/NACK/EVENT/TELEMETRY/RESPONSE/HEARTBEAT, APIs
  comm.send/publish/request, descoberta (WHO_ARE_YOU), simuladores e
  firmware mínimo no Mega.

- **Fase 2 concluída.** Implementado em `src/orion/communication/`:
  - `crc.py` / `protocol.py` — CRC16 (CCITT-FALSE) e `Mensagem` (Cap 5 s.5):
    protocolo, origem, destino, tipo (COMMAND/ACK/NACK/EVENT/TELEMETRY/
    RESPONSE/HEARTBEAT), id, timestamp, payload, checksum, id_referencia.
  - `framing.py` — serial: STX/ETX + byte-stuffing + CRC16 (`DecodificadorSerial`
    stateful, ressincroniza após ruído, descarta quadro com CRC inválido);
    TCP: prefixo de tamanho de 4 bytes (`DecodificadorTcp`).
  - `transport.py` — `TcpTransport` (cliente), `ConexaoTcp` (lado servidor),
    `iniciar_servidor_tcp`, `SerialTransport` (pyserial via executor
    dedicado de 1 thread).
  - `service.py` — `ComunicacaoService`: comm.send (ACK + até 3
    retransmissões → `comm.link_degraded`), comm.publish (EVENT difundido +
    Event Bus local), comm.request/responder (RESPONSE correlacionada por
    id_referencia), comm.status, roteamento transparente pelo campo destino,
    CRC inválido → NACK, responde WHO_ARE_YOU automaticamente.
  - `discovery.py` — `descobrir()`: WHO_ARE_YOU + verificação de versão de
    protocolo, publica `comm.protocol_mismatch` se incompatível.
  - `heartbeat.py` — `MonitorHeartbeat`: heartbeat periódico, `comm.module_lost`
    / `comm.module_recovered`, reutiliza o `HealthMonitor` do Kernel (Fase 1).
  - `tools/sim_raspberry.py` e `tools/sim_arduino.py` — simuladores completos
    (TCP e pty) para desenvolver sem hardware.
  - `firmware/hardware_core/` — firmware mínimo em C++ (PlatformIO,
    ArduinoJson): responde WHO_ARE_YOU e RETURN_STATUS, ACKa todo COMMAND,
    envia HEARTBEAT periódico sem bloquear o loop.
- **Decisão de design:** o checksum de mensagem (campo JSON) não é validado
  no link serial (`exigir_checksum_mensagem=False` para o link com o
  Arduino) — reproduzir a serialização JSON canônica do Python em C++ seria
  frágil (especialmente floats); a integridade do link serial já é garantida
  pelo CRC16 da camada de enquadramento, que é idêntico nas duas linguagens
  (validado byte a byte, ver testes de interoperabilidade abaixo).
- **Testes:** 68 testes unitários + 3 de integração (`tests/integration/`,
  marcador `sim`) usando transportes reais (TCP de loopback, pty). Validação
  cruzada C++/Python do CRC16 e do enquadramento completo (byte-stuffing)
  feita com um pequeno harness nativo (g++) — compatibilidade confirmada nos
  dois sentidos, inclusive com bytes especiais (STX/ETX/ESC) no payload.
- **Bugs reais encontrados e corrigidos durante o desenvolvimento** (não
  eram só do teste, eram da lib):
  1. `EventBus`: se `parar()` fosse chamado antes da task de `iniciar()`
     rodar sua primeira iteração, `iniciar()` sobrescrevia `_executando` de
     volta para `True` (corrida de inicialização). Corrigido movendo o
     `True` inicial para `__init__`.
  2. `SerialTransport`: leitura e escrita concorrentes na mesma porta via o
     executor padrão (multi-thread) do asyncio corrompiam o estado interno
     do pyserial (`self.fd` virava `None` em pleno `read()`). Corrigido com
     um `ThreadPoolExecutor(max_workers=1)` dedicado por transporte,
     serializando todo acesso à porta.
  3. `SerialTransport.conectar()` não esperava o Arduino terminar de
     reiniciar (abrir a porta ativa o DTR e reseta o Mega via CH340/bootloader
     — comportamento padrão da maioria dos adaptadores USB-serial): comandos
     enviados logo após conectar se perdiam. Corrigido com `atraso_reset_s`
     (padrão 2s, configurável, 0 para portas que não resetam como um pty).
  4. `ComunicacaoService._loop_recepcao`: uma exceção de transporte (ex.:
     `ConnectionResetError` num TCP derrubado sem aviso) subia sem tratamento
     e derrubava a task de recepção por completo. Corrigido isolando por
     link (Cap 6 s.8: falha de um módulo/link não derruba o resto).
- **Validado com hardware físico real** (não só simulado):
  - Firmware compilado e gravado no Mega real (`/dev/ttyUSB0`, CH340) via
    PlatformIO; WHO_ARE_YOU, ACK, RETURN_STATUS e HEARTBEAT confirmados
    funcionando ponta a ponta com o `ComunicacaoService` real.
  - Notebook real (10.20.20.195, Debian 13) conversando pela rede Wi-Fi
    (10.20.20.0/24) com este Raspberry rodando `sim_raspberry.py`: descoberta,
    comando com ACK e heartbeat recebidos com sucesso.
- **Notebook provisionado:** apt atualizado, toolchain de build/áudio
  instalado (ffmpeg, portaudio19-dev, build-essential, etc.), venv do projeto
  criado e testado (Fase 1 completa rodando lá também), Ollama instalado com
  o modelo `llama3.2:3b` já baixado (2 GB, CPU-only — sem GPU dedicada).
  `jproma23` adicionado ao grupo `sudo` no notebook (não tinha nenhum
  privilégio antes). PlatformIO instalado neste Pi em venv própria
  (`~/.platformio-venv`) para compilar/gravar o firmware.
- **Próximo passo:** iniciar Fase 3 — Banco de dados e memória no Raspberry
  (Caps 15, 11): Database Manager (SQLite WAL no SSD), migrações
  versionadas, API de memória (remember/recall/update/forget/context/stats)
  exposta ao Notebook via comm.request, backup diário + réplica cruzada no
  Notebook.

- **Fase 3 concluída.** Implementado em `motion_core/memory/` (novo pacote
  Python — código do Raspberry, deploy separado do `src/orion/` do
  Notebook; `pyproject.toml` ganhou `"."` no `pythonpath` de teste para
  importar `motion_core.*`):
  - `schema.py` — todas as tabelas do Cap 15 s.3 e Cap 11 s.5 (pessoas,
    ambientes, objetos, conhecimento, conversas, configuracao_memoria,
    missoes, eventos, telemetria, logs, diagnosticos, configuracao,
    schema_version) + os índices mínimos do Cap 15 s.4, como migração
    versionada (`MIGRACOES`).
  - `database.py` — `DatabaseManager`: WAL, `PRAGMA foreign_keys`,
    `integrity_check`, recuperação em cascata (Cap 15 s.7: checkpoint do
    WAL → restaurar backup mais recente → recriar do zero em modo
    degradado), backup via API nativa do SQLite com rotação (7 diários + 4
    semanais), retenção (telemetria 30d, eventos 90d, logs 30d/erros 180d)
    com `VACUUM`. Deliberadamente síncrono (mais simples de testar) —
    quem chama em contexto assíncrono delega para `asyncio.to_thread`.
  - `manutencao.py` — ponte assíncrona com o Event Bus: `iniciar_banco()`
    publica `database.ready`/`database.rebuilt`; `TarefaManutencao` roda
    backup + retenção uma vez por dia no horário configurado
    (`database.backup_hour`), publicando `database.backup_completed` /
    `database.backup_failed`.
  - `api.py` — `MemoryAPI`: remember/recall/update/forget/context/stats
    (Cap 11 s.6), publicando `memory.updated`/`memory.recall_executed`.
    Toda exclusão vira log (Cap 11 s.8). `context()` monta pessoa +
    conversas recentes + conhecimento relevante (o resto do contexto de
    curto prazo — missão em execução, sensores — é RAM do Mission Core,
    não vem do banco).
  - `bridge.py` — `PonteMemoria`: liga comandos `memory.*` recebidos via
    `comm.mensagem.command` (Fase 2) à `MemoryAPI`, respondendo com
    RESPONSE — é assim que `memory.context()` chamado do Notebook chega
    até aqui.
  - `replica.py` — réplica cruzada do backup para o Notebook em blocos via
    `comm.send` (ACK + retransmissão, reusa o protocolo da Fase 2 em vez de
    um transporte de arquivos à parte); `ReceptorReplica` reconstrói por
    índice (não por ordem de chegada, já que retransmissão pode reordenar).
- **Bug de segurança encontrado e corrigido antes de ir para produção:**
  `MemoryAPI` interpolava nomes de coluna de `dados`/`filtro` direto na SQL
  - como esses dicts podem vir de uma requisição remota (`comm.request`),
  isso era brecha de SQL injection via nome de campo malicioso. Corrigido
  validando toda coluna contra `PRAGMA table_info` antes de montar a query
  (`ErroColunaInvalida`), com teste de regressão usando um nome de coluna
  contendo `; DROP TABLE ...`.
- **Bug real encontrado ao ligar `asyncio.to_thread` ao sqlite3:** conexões
  SQLite são presas à thread que as criou por padrão; como cada chamada
  assíncrona pode rodar em uma thread diferente do executor (mesma classe
  de problema já visto no pyserial da Fase 2), foi preciso abrir a conexão
  com `check_same_thread=False` — seguro aqui porque o acesso é sempre
  sequencial (nunca duas chamadas concorrentes na mesma conexão).
- **Testes:** 106 testes unitários + 5 de integração (`tests/integration/`).
  `conftest.py` (com `FakeTransporte`) subiu de `tests/unit/` para
  `tests/` para poder ser compartilhado com `tests/integration/`. Cenário
  ponta a ponta cobrindo backup → réplica → corrupção → recuperação, e
  benchmark confirmando `memory.context()` via `comm.request` abaixo de
  100 ms com massa de teste (500 conversas + 50 fatos).
- **Limitação conhecida, documentada no código:** a ponte de memória ainda
  não valida a origem da solicitação contra o Service Registry (Cap 14 s.9
  / Cap 11 s.8 exigem isso) — o Communication Core da Fase 2 ainda não
  implementa esse controle de acesso. Revisitar mais adiante.
- **Nota:** os testes usam `tmp_path` para o banco/backups, não os caminhos
  reais de `config/orion.yaml` (`/mnt/ssd/orion/...`) — isso será ligado de
  verdade quando o processo do Motion Core existir (Fase 7); por enquanto
  `motion_core/memory/` é uma biblioteca testável, ainda sem um daemon
  próprio rodando no boot.
- **Próximo passo:** iniciar Fase 4 — Hardware Core completo (Cap 10):
  firmware modular (Motor/Sensor/Radar/IMU/Encoder/Command Executor/
  Telemetry/Safety Manager), máquina de estados completa (BOOT→...→
  SAFE_MODE), todos os comandos de movimento com ACK e progresso, pacote
  periódico Radar Inteligente, segurança reativa (parada por distância
  mínima, inclinação, timeout) sem depender do Raspberry nem do Notebook.

## 2026-07-17 (continuação)

- **Fase 4 implementada (código completo), validação física ainda
  pendente.** Encontrado o guia de ligação elétrica do Sentinela X
  (`~/Downloads/Sentinela X — Guia de Ligação Elétrica.pdf`) com pinos
  reais para motores/ultrassom/IMU/DHT do mesmo Mega físico. Usado como
  base para `firmware/hardware_core/include/pins.h`.
- Implementado em `firmware/hardware_core/`:
  - `pins.h` — pinos confirmados (motores 2-6, HC-SR04 22/23, MPU6050 I2C
    20/21, DHT 24) e reservados (encoders 18/19, ultrassom traseiro 26/27,
    servo radar 9, pan/tilt 10/11, LED 25).
  - `estado.h` — máquina de estados completa do Cap 10 s.4 (BOOT→READY→
    IDLE→EXECUTING_MISSION→OBSTACLE_DETECTED→MISSION_PAUSED→ERROR→
    SAFE_MODE→SHUTDOWN), com notificação de transição ao Motion Core
    (EVENT `motion.status`).
  - `motor_manager.h` — 2 steppers via STEP/DIR/ENABLE, pulsos gerados por
    temporização com `micros()` (sem `delay()`), odometria aproximada por
    contagem de passos. `PASSOS_POR_METRO`/`PASSOS_POR_GRAU` são
    constantes de bancada (sem calibração real ainda — isso é a
    autocalibração da Fase 12).
  - `encoder_manager.cpp` — contagem de pulsos via interrupt (zero
    gracioso, sem hardware ligado ainda).
  - `sensor_ultrassonico.h` — driver HC-SR04 **não bloqueante** (maquina de
    estados; `pulseIn()` bloquearia até 1s por leitura, inaceitável no
    loop principal).
  - `radar_manager.h` — varredura do servo frontal (SCAN_FRONT) + leitura
    fixa usada pelo Safety Manager.
  - `imu_manager.h` / `dht_manager.h` — MPU6050 (Adafruit) e DHT11
    (Adafruit DHT); DHT só lê quando o robô está parado (a biblioteca
    bloqueia ~20-25ms por leitura).
  - `safety_manager.h` — parada reativa por obstáculo frontal, inclinação,
    impacto ou timeout de comando, **independente do Raspberry/Notebook**
    (Cap 6 regra 7).
  - `command_executor.h` — despacha MOVE_FORWARD/MOVE_DISTANCE/
    MOVE_CONTINUOUS/TURN_LEFT/TURN_RIGHT/STOP/DOCK/SCAN_FRONT/LIGHT_ON/OFF.
  - `telemetry_manager.h` — pacote Radar Inteligente (Cap 5 s.7) a cada
    500ms via TELEMETRY.
  - `main.cpp` reescrito integrando tudo sobre o protocolo/enquadramento
    da Fase 2. Compilado com PlatformIO: RAM 26,2% (2149/8192 bytes),
    Flash 15,0% (38160/253952 bytes) — bastante folga.
- **Descoberta importante (correção de contexto):** o guia de fiação
  descreve uma montagem **passada/planejada**, não o estado físico atual —
  o usuário confirmou "não temos hardware ainda". Só o Mega + cabo USB
  existem de verdade agora. Isso foi confirmado na prática: telemetria do
  Mega real mostra `imu_conectado=false`, `distancia_frontal_valida=false`,
  `dht_valido=false` de forma consistente (ausência real de hardware, não
  bug do firmware).
- **Validado no Mega real** (nível de protocolo/lógica, sem periféricos
  físicos): firmware gravado via PlatformIO; WHO_ARE_YOU, RETURN_STATUS e
  HEARTBEAT respondendo; MOVE_FORWARD/TURN_LEFT/STOP fazem a máquina de
  estados transicionar corretamente (IDLE↔EXECUTING_MISSION) com ACK e
  evento `motion.status`; LIGHT_ON/LIGHT_OFF ACKados; SCAN_FRONT completa
  e publica `motion.scan_complete` com as 7 leituras (todas inválidas,
  como esperado sem sensor real). Nenhum teste moveu nada fisicamente
  (nada de motor está conectado) - seguro de rodar sem risco.
- **Enviado para o usuário:** rascunho de e-mail (Gmail) com o guia de
  ligação elétrica completo, e o mesmo conteúdo salvo em
  `docs/hardware/wiring_arduino.md` (esse caminho era citado no PDF do
  Sentinela mas nunca tinha sido criado de fato).
- **Pendência real, não é código:** o critério de "pronto" da Fase 4
  ("bancada executa cada comando com rodas suspensas; obstáculo para o
  robô") exige motores/sensores fisicamente montados. Isso é responsabilidade
  do usuário montar o hardware (guia em mãos) - o firmware já está pronto
  para quando isso acontecer.
- **Próximo passo:** ou (a) aguardar a montagem física para validar o
  Cap 10 por completo, ou (b) adiantar a Fase 5 (Vision Core no Notebook,
  Cap 8) e Fase 6 (Voz + IA, Caps 9/7) em paralelo, já que essas rodam no
  Notebook e não dependem de hardware do Arduino estar montado.

## 2026-07-17 (Fase 5)

- **Fase 5 implementada e validada com webcam real.** Criado
  `src/orion/vision/`: `captura.py` (CapturaCamera assíncrona sobre
  cv2.VideoCapture, com descarte de frames de aquecimento — achado real: o
  primeiro frame lido logo após abrir a porta vem quase preto, a câmera
  ainda ajustando exposição/balanço de branco), `deteccao.py` (DetectorYolo,
  YOLOv8n), `reconhecimento_facial.py` (ReconhecedorFacial via
  `face_recognition`/dlib, embeddings comparados por distância euclidiana
  contra `pessoas.embedding_face`), `rastreamento.py` (Rastreador de um
  alvo único, com janela de tolerância antes de declarar "perdido"),
  `pan_tilt.py` (CalculadoraPanTilt: controle proporcional simples com
  limites de ângulo/velocidade, sem dependências pesadas), `vision_core.py`
  (orquestrador completo, publica todos os eventos do Cap 8 s.5,
  recuperação de câmera desconectada).
- **Extensão justificada no firmware:** adicionado o comando `SET_PAN_TILT`
  ao Mega (não estava na lista original do Cap 10 s.5, mas os servos
  pan/tilt já constavam no Cap 10 s.2 e o Cap 8 s.8 exige o comando) -
  testado no Mega real (ACK ok), servos ainda não montados fisicamente.
- **Dependências novas** (`pyproject.toml`, extra `vision`): opencv-python,
  ultralytics, face-recognition. Instalado no Notebook (dlib/face_recognition
  compilou por ~25min usando os 4 núcleos via LTO).
- **Bug de ambiente real encontrado e corrigido:** `face_recognition`
  chama `quit()` internamente (não levanta `ImportError`) quando falta
  `pkg_resources` - o `setuptools` mais recente (83.x) removeu esse módulo
  (deprecado, remoção prevista para 2025-11-30). Como `quit()` gera
  `SystemExit`, isso derrubava o processo `pytest` inteiro, não só pulava
  o teste. Corrigido fixando `setuptools<81` e instalando
  `face-recognition-models` direto do GitHub (não vem sozinho via pip) -
  ambos documentados em `pyproject.toml`.
- **Padrão de teste:** `pytest.importorskip` para cv2/ultralytics/
  face_recognition/numpy em todos os testes que precisam dessas libs -
  no Raspberry Pi (que nunca roda Vision Core) esses testes pulam
  graciosamente; no Notebook rodam de verdade. `VisionCore` ganhou
  parâmetros de injeção (`captura`, `detector`, `reconhecedor`,
  `timeout_alvo_perdido_s`) para testar a lógica de orquestração sem
  precisar de câmera/modelos reais.
- **Descoberta de hardware:** o Notebook tem DUAS câmeras físicas - a
  integrada ("PC Camera", índice 0/1) e, após o usuário conectar durante a
  sessão, a webcam USB externa dedicada à visão ("DV20 USB", índice 2/3).
  Adicionado `camera_indice_principal`/`camera_indice_luminosidade` em
  `config/orion.yaml` (Cap 17) para não fixar esses índices no código -
  podem mudar se os cabos forem replugados em outra ordem. Dois
  dispositivos de captura de áudio também presentes (mic da webcam +
  USB Audio Device) - relevante para a Fase 6.
- **Testado com a webcam real:** pipeline completo (captura → YOLO →
  reconhecimento facial → rastreamento → Event Bus) rodou sem erros contra
  `/dev/video2` de verdade; frame capturado e inspecionado (câmera
  apontada para o canto do teto, sem pessoa no quadro - por isso zero
  detecções, resultado correto para a cena, não um bug). Usuário optou por
  não repetir o teste com uma pessoa em frente à câmera por ora.
- **128 testes unitários passando no Notebook** (118 + 2 pulados no
  Raspberry, onde as libs de visão não são instaladas de propósito).
- **Pendência real, não é código:** assim como a Fase 4, falta a montagem
  física dos servos pan/tilt para fechar o critério de "pronto" por
  completo (SET_PAN_TILT já funciona no protocolo; falta o servo de
  verdade responder). Um teste com pessoa real em frente à câmera também
  fica para confirmar `vision.person_detected`/`person_recognized` de
  ponta a ponta.
- **Próximo passo:** Fase 6 — Voz e IA (Caps 9, 7): wake word "Fofão" +
  Whisper + Piper (offline), AI Manager via Ollama (já instalado com
  `llama3.2:3b` desde a Fase 2), Mission Planner. Dois microfones já
  disponíveis no Notebook para essa fase.

## 2026-07-17 (Fase 6)

- **Fase 6 implementada por completo (código); validação ao vivo bloqueada
  por hardware de áudio.** Criado `src/orion/voice/`: `captura_audio.py`
  (SeletorMicrofone - Cap 9 s.6, escolhe o melhor canal por RMS/estabilidade),
  `wake_word.py` (DetectorPalavraAtivacao - ver decisão abaixo),
  `transcricao.py` (Transcritor via faster-whisper), `sintese.py`
  (Sintetizador via Piper), `voice_core.py` (maquina de estados completa:
  IDLE→LISTENING→WAKE_DETECTED→TRANSCRIBING→THINKING→SPEAKING→ERROR,
  publicando todos os eventos do Cap 9 s.5), `audio_utils.py` (reamostragem
  compartilhada). Em `src/orion/mission/`: `ai_manager.py` (Ollama + prompt
  de sistema + contexto), `memory_client.py` (Notebook fala com a memória
  do Raspberry via comm.request, espelhando a MemoryAPI da Fase 3),
  `mission_planner.py` (fluxo de decisão do Cap 7 s.4: classifica
  comando/pergunta de hora/pergunta geral, consulta IA quando necessário,
  despacha comando ao hardware, registra a conversa na memória).
- **Decisão de design documentada no código:** não existe ainda modelo
  customizado openWakeWord treinado para "Fofão" (os modelos prontos da
  lib são em inglês; treinar um exige pipeline de dados sintéticos
  separado). Solução que funciona hoje, 100% offline: transcrever janelas
  curtas com faster-whisper e checar se "fofão" aparece no texto -
  `openwakeword` fica instalado como dependência para quando um modelo
  customizado existir.
- **Dependências novas** (`pyproject.toml`, extra `mission`): piper-tts,
  openwakeword. Voz baixada: `pt_BR-faber-medium` (63MB, via
  `python -m piper.download_voices`, salva em `data/piper_voices/` -
  gitignored, precisa rodar de novo em outra máquina).
- **2 bugs reais de taxa de amostragem encontrados e corrigidos:** tanto a
  gravação (`captura_audio.py`) quanto a reprodução (`sintese.py`) davam
  `PortAudioError: Invalid sample rate` - os dispositivos de áudio USB
  desta montagem só aceitam sua taxa nativa (44100Hz), não a que
  Whisper/Piper esperam (16kHz/22050Hz). Corrigido gravando/tocando na taxa
  nativa do dispositivo e reamostrando em software (interpolação linear,
  `audio_utils.reamostrar`, compartilhada entre os dois).
- **Notebook configurado para nunca dormir** (pedido do usuário): systemd
  sleep/suspend/hibernate/hybrid-sleep mascarados, logind ignorando
  tampa/idle/teclas de suspensão, Wi-Fi sem power-saving - ver
  `project_orion_os_wiring.md` na memória para os detalhes exatos.
- **164 testes passando no Notebook** (128 + 7 pulados no Raspberry, onde
  as libs de voz não são instaladas de propósito - Voice Core/Mission
  Core rodam só no Notebook).
- **Descoberta de hardware de áudio (bloqueia a validação ao vivo):**
  - Sem alto-falante físico disponível: o alto-falante interno e o P2
    (jack) do notebook não aparecem no sistema de áudio do Linux (falta
    driver/quirk do codec HDA Intel PCH - só HDMI aparece para esse chip).
    O "USB Audio Device" é só uma interface, sem caixinha conectada.
  - Microfones disponíveis (webcam integrada, USB Audio Device, webcam
    externa) captam sinal fraco demais (RMS ~0,008-0,01) para o Whisper
    transcrever de forma confiável, mesmo com o ganho do mixer ALSA já no
    máximo (95%) - testado repetidamente com o usuário falando perto e
    alto, sem sucesso na transcrição.
  - Usuário decidiu comprar um headset/caixinha USB com microfone
    embutido, resolvendo os dois problemas de uma vez. Planeja usar os
    mics atuais (integrado, webcams) como entradas secundárias para
    cancelamento de ruído no futuro (Cap 9 s.6 já prevê "fusão de áudio/
    beamforming quando o hardware permitir").
- **Pendência real, não é código:** o critério de "pronto" ("Fofão, que
  horas são?" com resposta falada; "Fofão, acenda a lanterna" aciona
  LIGHT_ON) exige áudio físico funcional dos dois lados (entrada e saída).
  Retestar assim que o headset USB chegar.
- **Próximo passo:** aguardar o headset USB para fechar a validação ao
  vivo da Fase 6, ou adiantar a Fase 8 (Avatar + interface web, Cap 13) -
  usuário já pediu um avatar de "cabeça cibernética" que acompanha
  pan/tilt na tela - ou revisitar as Fases 4/5 quando o hardware mecânico
  (motores, sensores, servos) for montado.

## 2026-07-17 (Fase 8, início — Avatar/Sentinelinha)

- **Sessão anterior caiu no meio da Fase 8** antes de logar aqui - esta
  entrada reconstrói o que ficou em disco (arquivos criados mas não
  commitados) e o que foi feito na retomada.
- **Primeira versão (perdida em decisão, mantida em código até o usuário
  mudar de ideia):** `avatar_server.py` (aiohttp) servindo uma "cabeça
  cibernética" 2D em canvas/CSS - visor tático/holográfico, sóbrio.
  Consumidor puro do Event Bus via SSE (`/eventos`), repassando
  `voice.status`, `motion.pan_tilt`, `motion.status`,
  `motion.obstacle_front`, `diagnostic.error`, `system.ready`.
- **Mudança de direção do usuário:** ele tinha, num rascunho de e-mail
  separado (assunto "Avatar robo"), um modelo próprio já pronto - a
  **Sentinelinha**, um mascote 3D (Three.js) fofo/flutuante (cabeça
  esférica, viseira, olhos com brilho, bochechas, antena pulsante,
  "orelhas" com anel ciano, corpo flutuante com anel propulsor),
  originalmente com rastreamento de rosto via webcam (diff de frames).
  Usuário decidiu: descartar a cabeça tática 2D e adotar a Sentinelinha
  como avatar oficial do Fofão, trocando o rastreamento por webcam pelos
  eventos reais do robô.
- **Bug real encontrado no modelo do usuário:** o HTML original carregava
  `three.min.js` via CDN (`cdnjs.cloudflare.com`) - quebra a regra
  "100% offline" do projeto (funciona só com internet). Corrigido: Three.js
  r128 baixado uma vez e vendorizado em
  `src/orion/display/static/vendor/three.min.js`, servido localmente pelo
  `AvatarServer` (que já serve todo `static/` como estático).
- **Reescrita de `static/index.html`, `avatar.css`, `avatar.js`:**
  geometria 3D da Sentinelinha mantida como o usuário desenhou; removida a
  parte de webcam/`getUserMedia`/detecção de movimento por diff de frame.
  No lugar:
  - `motion.pan_tilt` (evento real, publicado por `vision_core.py`) move a
    cabeça e os olhos, normalizado pelos limites reais dos servos
    (`config/orion.yaml`, seção `vision`: `pan_limits_degrees: [-80, 80]`,
    `tilt_limits_degrees: [-30, 45]`) - **não fixado no JS**: o avatar
    busca `/config` no servidor, que devolve esses limites lidos do YAML
    (rota nova em `avatar_server.py`), respeitando a regra arquitetural
    #6 (nenhum valor fixo fora de `config/orion.yaml`).
  - `voice.status` (estados reais de `voice_core.py`: IDLE, LISTENING,
    WAKE_DETECTED, TRANSCRIBING, THINKING, SPEAKING, ERROR) controla
    "energia"/animação e o texto do status; boca anima de verdade (abre e
    fecha) só durante SPEAKING.
  - `motion.obstacle_front` e `diagnostic.error` disparam um estado de
    alerta visual (olhos/antena ficam vermelhos) em vez do vermelho ser só
    decorativo.
- **`AvatarServer.__init__` ganhou o parâmetro `limites_pan_tilt`** (dict
  com os dois limites) e a rota `GET /config` que os expõe como JSON.
- **Teste novo:** `tests/unit/test_avatar_server.py`, com
  `pytest.importorskip("aiohttp")` (mesmo padrão de Vision/Voice - Display
  também é Notebook-only, EDR-0018). Cobre: `/` serve o HTML, `/config`
  devolve os limites certos, um evento publicado no bus chega de verdade
  no cliente SSE, e um tópico fora da lista repassada não gera assinatura
  no bus. **Não roda neste Raspberry** (sem `aiohttp` instalado de
  propósito aqui) - 128 testes passando + 8 pulados no Pi (contando os 4
  novos deste arquivo).
- **Novo `tools/preview_avatar.py`:** sobe o `AvatarServer` num Event Bus
  real e fica publicando uma sequência de eventos de mentirinha (pan/tilt
  varrendo em seno, estados de voz em ciclo IDLE→...→SPEAKING) - permite
  ver o avatar reagindo na tela sem precisar da Voice/Vision Core rodando
  de verdade. Mesmo padrão dos simuladores da Fase 2
  (`tools/sim_arduino.py`, `tools/sim_raspberry.py`).
- **Decisão consciente, não é pendência esquecida:** o avatar **não** foi
  ligado no `python -m orion` (`kernel/boot.py`/`__main__.py`) ainda.
  Motivo: `__main__.py` hoje faz o boot e encerra na hora (não existe
  ainda um loop principal de longa duração), e nem Vision nem Voice Core
  estão plugadas nesse boot apesar de já implementadas nas Fases 5/6 -
  ligar só o avatar sozinho nesse meio-termo ficaria pela metade. Revisitar
  quando o loop principal de execução for construído.
- **Pendência real:** tudo isso foi escrito e testado (onde dá) no
  Raspberry, mas Display é código do **Notebook** (Mission Core,
  EDR-0018) - nada disso rodou de fato num navegador ainda. Falta: no
  Notebook, `pip install -e ".[display]"` e `python tools/preview_avatar.py`,
  depois abrir `http://127.0.0.1:8090`.
- **Nada commitado ainda** - `avatar_server.py`, `static/`,
  `tools/preview_avatar.py` e `tests/unit/test_avatar_server.py` seguem
  como arquivos novos não versionados.
- **Próximo passo:** rodar `preview_avatar.py` no Notebook de verdade e
  validar visualmente; depois decidir com o usuário se a Sentinelinha
  também deve reagir a mais eventos (ex.: `motion.status` para
  IDLE/EXECUTING_MISSION), e revisitar a integração no boot quando o loop
  principal existir.

## 2026-07-17 (Fase 8, continuação — validado ao vivo no Notebook)

- **Acesso SSH do Raspberry para o Notebook estabelecido e confirmado**
  (`ssh jproma23@10.20.20.195`, sem senha - chave deste Pi já estava
  autorizada lá). Não existe git remote ligando os dois `~/orion-os`
  (cada máquina tem seu próprio histórico local) - sincronização de
  arquivos feita via `scp` direto. Detalhes salvos na memória do Claude
  (`reference_orion_os_notebook_ssh`) para não precisar redescobrir isso
  numa sessão futura.
- **2 bugs reais corrigidos em `tests/unit/test_avatar_server.py`** ao
  rodar de verdade no Notebook (aqui no Pi o teste só compila, não
  executa - falta `aiohttp` de propósito):
  1. Fixture assíncrona (`cliente`) precisava do decorator
     `@pytest_asyncio.fixture`, não `@pytest.fixture` puro - modo
     `asyncio_mode=strict` do projeto exige isso, senão dá
     `AssertionError` genérico no setup.
  2. O teste publicava um evento no bus sem o loop de despacho
     (`bus.iniciar()`) rodando - `publish()` só enfileira, quem entrega
     de fato ao assinante é a task de `iniciar()`. Sem ela, o teste só
     via o ping de keepalive 15s depois, nunca o evento. Corrigido
     criando a task no fixture e cancelando/aguardando no teardown.
- **170 testes passando no Notebook** (com `aiohttp` de verdade instalado
  - nenhum pulado lá, todas as libs de Vision/Voice/Display presentes).
- **Achado e encerrado um processo órfão de antes da sessão cair:**
  `/tmp/rodar_avatar.py` (script solto, fora do repositório) ainda estava
  rodando na porta 8090 com o avatar antigo (a cabeça tática 2D). Matado
  e substituído pelo `tools/preview_avatar.py` novo, versionado.
- **Validação visual real, pela primeira vez:** aberto Firefox em modo
  kiosk (`--kiosk`) na tela física do Notebook (`DISPLAY=:0`, sessão
  XFCE), apontado para `http://127.0.0.1:8090` - screenshots confirmam
  o Fofão renderizando de verdade: cabeça 3D girando com o pan/tilt
  simulado, antena pulsando, pill de status trocando de texto
  (ouvindo/entendendo/falando) em sincronia com o ciclo de estados do
  `preview_avatar.py`.
- **Renomeado de "Sentinelinha" para "Fofão"** (nome/título/fala de boas-
  vindas em `index.html` e `avatar.js`) - a pedido do usuário, para bater
  com a wake word já implementada na Fase 6 (Cap 9). Ressincronizado com
  o Notebook e Firefox reiniciado para pegar a mudança.
- **Efeito colateral resolvido, não era bug do avatar:** dois popups do
  painel do XFCE (`xfce4-panel`, tipo `_NET_WM_WINDOW_TYPE_POPUP_MENU`)
  ficaram travados abertos por cima da tela, sobrando de alguma interação
  anterior à sessão - descoberto via `xprop`/`xwininfo -tree` (não eram
  janelas do Firefox nem do avatar). Resolvido reiniciando o processo
  `xfce4-panel` no Notebook.
- **Estado atual:** avatar rodando ao vivo no Notebook, validado
  visualmente, mas ainda com dados simulados (`preview_avatar.py`) - não
  com Voice/Vision Core reais. Nada commitado ainda em nenhuma das duas
  máquinas.
- **Próximo passo:** decidir com o usuário se continua a Fase 8 (interface
  web do Raspberry - dashboard/conversa/mapa/diagnóstico, Cap 13 s.5-6 -
  e mapa polar do radar, nenhum dos dois começado ainda) ou se valida
  primeiro o avatar com a Voice Core real (Fase 6, pendente de headset
  USB) antes de seguir.

## 2026-07-17 (Fase 7, início — Motion Core / Navegação)

- **Decisão de sequenciamento com o usuário:** ao pedir pra continuar o
  projeto, verifiquei o `PLANO_IMPLEMENTACAO.md` e achei que a Fase 7
  (Motion Core/Navegação, Cap 12) está 0% feita, e que o painel MAPA da
  Fase 8 depende justamente dos dados dela (posição, radar). Perguntei ao
  usuário e ele confirmou: Fase 7 primeiro. Também confirmou que **não há
  motor físico montado ainda** - toda validação aqui é em nível de
  protocolo, igual às Fases 4/5.
- **Criado `motion_core/navigation/navigation_core.py`** (`NavigationCore`):
  máquina de estados dos 6 modos do Cap 12 s.3 (HOLD, MANUAL, GOTO,
  PATROL, FOLLOW, EXPLORE), publicando todos os eventos do Cap 12 s.11
  (`navigation.plan_created/segment_started/segment_completed/
  obstacle_avoided/target_lost/mode_changed/error`). Nunca fala com o
  Arduino direto - só via `ComunicacaoService` (`comm.send`/`request`),
  igual a regra arquitetural #1 exige.
  - **HOLD, MANUAL, GOTO, PATROL: implementação completa.** GOTO/PATROL
    fazem SCAN_FRONT antes de cada segmento (Cap 12 s.4) e abortam com
    `navigation.obstacle_avoided` se a leitura (varredura OU telemetria)
    indicar obstáculo abaixo de `motion.min_front_distance_cm`. PATROL
    tenta cada segmento até `navigation.obstacle_retry_max` vezes antes de
    desistir da rota.
  - **FOLLOW e EXPLORE: versão mínima deliberada**, documentada no
    docstring do módulo - FOLLOW só reage a `vision.person_detected` com
    uma correção proporcional simples e detecta perda por timeout (falta a
    rotação de busca do Cap 12 s.5 passo 5, que depende do Vision Core
    rodando de ponta a ponta); EXPLORE é só "escaneia e anda se livre", não
    um algoritmo de mapeamento de verdade (isso fica pro SLAM do Cap 12
    s.12, ORION OS 2.0+).
- **Entrada de missão via Event Bus:** `NavigationCore` assina
  `navigation.comando` (`{"acao": "GOTO", "graus":..., ...}`) - ainda não
  ligado ao recebimento real de COMMAND vindo do Notebook via TCP (isso é
  fiação da Fase 2/Communication Core, não desta fase); por ora a entrada é
  só esse tópico do Event Bus, testável e já correta arquiteturalmente.
- **19 testes novos** em `tests/unit/test_navigation_core.py`, usando
  `ComunicacaoService` real + `FakeTransporte` (mesmo padrão de
  `test_service.py`) com um auto-respondedor de ACK/scan em background -
  não um mock solto, testa o protocolo de verdade. 2 bugs reais de teste
  corrigidos no caminho (mesma classe de erro já visto antes: fixture
  assíncrona sem `@pytest_asyncio.fixture`, e checar eventos sem esperar
  `bus.aguardar_fila_vazia()` primeiro).
- **3 bugs reais encontrados testando contra o Mega físico** (script
  descartável, removido depois de validar) - nenhum aparece em teste
  unitário porque só se manifestam com timing real de hardware:
  1. `_executar_segmento` mandava `MOVE_DISTANCE` logo após o ACK do
     `SCAN_FRONT` - mas esse ACK só confirma que a varredura *começou*, não
     que terminou (leva ~2.1s: 7 ângulos × 300ms de assentamento do servo,
     `radar_manager.h`). **Corrigido:** `NavigationCore` agora espera o
     evento `motion.scan_complete` de verdade (com timeout de 3s e
     fallback pra lista vazia) antes de prosseguir - e além de corrigir o
     timing, isso também fecha um gap de spec real: antes a leitura do
     radar nunca era usada pra decidir nada, só o `SCAN_FRONT` era
     disparado e esquecido.
  2. Comandos de movimento (`TURN_*`, `MOVE_DISTANCE`) às vezes não eram
     ACKados quando enviados logo após o ACK de um comando de movimento
     anterior - o ACK confirma só que o comando foi *recebido*, o firmware
     ACKa na hora e executa de forma assíncrona (`Estado::EXECUTING_MISSION`
     → `Estado::IDLE`). **Corrigido:** `NavigationCore` agora assina
     `motion.status` e espera `Estado::IDLE` antes de encadear o próximo
     comando de movimento (`_aguardar_ocioso`, timeout de 10s).
  3. **Pendência real, não resolvida:** mesmo com os dois fixes acima,
     ainda observei (não sempre, parece intermitente) falha de ACK logo
     após um `SCAN_FRONT` completar - às vezes no comando seguinte
     (`MOVE_DISTANCE`), às vezes no próprio `SCAN_FRONT` de um segmento
     posterior. Adicionei uma folga fixa de 0.3s após `motion.scan_complete`
     (`PAUSA_APOS_SCAN_S`) como mitigação pragmática, mas isso **não
     eliminou o problema por completo** - parece uma flakiness real de
     baixo nível (buffer serial/CH340, ou o firmware sob timing de carga),
     não mais um bug determinístico de ordenação que dá pra resolver só no
     lado Python. Fica como item aberto: investigar o lado firmware
     (`main.cpp`/`radar_manager.h`) numa sessão dedicada, ou considerar
     aumentar `max_retries`/`ack_timeout_ms` especificamente pro link
     Raspberry↔Arduino.
- **185 testes passando no Notebook** (170 + 15 novos), **143 passando + 8
  pulados no Raspberry** (os pulados continuam sendo só os de
  Vision/Voice, de propósito).
- **Não commitado ainda.**
- **Próximo passo:** decidir entre (a) investigar a flakiness do item 3
  acima com mais tempo/instrumentação no firmware, (b) seguir pra Fase 8
  de verdade (interface web do Raspberry) usando o que já existe de Fase 7,
  ou (c) fundir sensores pra `motion.position` (Cap 12 s.8), que nenhuma
  das duas fases anteriores cobre ainda.

## 2026-07-17 (aparte — Pi Connect caindo, não é do ORION OS)

- Usuário reportou queda constante do compartilhamento de tela via
  Raspberry Pi Connect (ele está 100% remoto, sem acesso físico de
  fallback). Diagnosticado: não tem relação com o código deste projeto -
  é o `wayvnc` (servidor VNC local usado pelo Pi Connect) perdendo a saída
  HDMI-A-1 (a TV usada como monitor) repetidamente e crashando -
  `rpi-connect-wayvnc.service` já tinha reiniciado 127+ vezes no dia
  (`journalctl --user`, mensagens "Selected output HDMI-A-1 went away" /
  "No fallback outputs left. Exiting").
- Correção padrão (`hdmi_force_hotplug=1` em `/boot/firmware/config.txt`)
  exige reboot do Pi - **não aplicada**: o usuário está sem acesso físico
  de fallback, então um reboot arriscado (rede não voltar, sessão gráfica
  não subir) o deixaria sem jeito nenhum de recuperar o Pi. Detalhes e essa
  restrição crítica salvos na memória do Claude
  (`project_pi_hdmi_wayvnc_crashloop`) para não arriscar de novo sem
  reconfirmar com o usuário no momento.
- Parou de cair sozinho (TV provavelmente estabilizou o sinal) - usuário
  optou por seguir sem aplicar o fix por ora.

## 2026-07-17 (fechando o item 3 da Fase 7 - flakiness do ACK)

- Retomada a investigação do item 3 pendente (falha intermitente de ACK
  logo após SCAN_FRONT). Revisado `main.cpp`/`radar_manager.h`/
  `sensor_ultrassonico.h` do firmware por completo: nenhum bloqueio
  (`delay()`/`pulseIn()`) encontrado - `loop()` drena todo o Serial
  disponível antes de qualquer outra coisa, sensor ultrassônico é uma
  máquina de estados não-bloqueante de verdade. Nada óbvio ali.
- Usuário reiniciou o Arduino fisicamente (confirmado seguro - não afeta
  Raspberry nem acesso remoto, são componentes independentes).
- Rodado um diagnóstico com timeout generoso (5s) medindo o tempo real de
  cada ACK em 10 ciclos SCAN_FRONT+MOVE_DISTANCE: **0 falhas em 10**,
  ACKs consistentes em 100-210ms (bem abaixo de qualquer timeout usado),
  **0 quadros inválidos** no decodificador. Também conferido
  `vcgencmd get_throttled` = `0x0` - descarta queda de energia/throttling
  do Pi como causa.
- **Conclusão:** não é mais tratável como bug determinístico de ordenação
  (os 2 fixes já aplicados nesta fase - esperar `motion.scan_complete` e
  `Estado::IDLE` antes de encadear comando - continuam corretos e válidos
  por si só, independente disso). O que sobra parece ser um glitch raro e
  não-reproduzível sob demanda (talvez ruído elétrico pontual no adaptador
  CH340, ou um hiccup ocasional do driver USB do kernel) - não uma falha
  sistemática. Na configuração de produção (`config/orion.yaml`:
  `max_retries: 3`, `ack_timeout_ms: 500`), o mecanismo de retransmissão
  que o Communication Core já tem (Fase 2) deve absorver esse tipo de
  blip raro sem intervenção adicional - um ACK que demora 100-210ms tem
  folga enorme dentro de 500ms, e uma retransmissão isolada resolve o
  resto. **Item encerrado** - não fica mais como pendência aberta, mas
  sem "causa raiz" 100% identificada (aceito como characteristic
  esperado de um link serial USB real, coberto pela retransmissão
  existente).

## 2026-07-17 (Cap 12 s.8 - Fusão de Sensores / motion.position)

- Implementado `motion_core/navigation/fusao_sensores.py` (classe
  `FusaoSensores`), retomando a opção (c) deixada em aberto na entrada
  anterior desta mesma data. Módulo separado do `NavigationCore` (regra
  #9 do ARQUITETURA.txt - uma responsabilidade cada), mas seguindo o mesmo
  padrão de construtor/assinatura de eventos: recebe `event_bus` e a
  fatia `config.secao("motion")`, assina `comm.mensagem.telemetry`.
- **O que faz:**
  1. A cada telemetria com `passos_esquerda`/`passos_direita`, calcula o
     delta desde a leitura anterior e atualiza pose (x, y, orientação)
     por **odometria diferencial clássica** (fórmula padrão:
     `v = (v_dir+v_esq)/2`, `ω = (v_dir-v_esq)/wheel_base`), com
     integração por ponto médio (usa a orientação na metade do
     movimento para projetar x/y, erro menor que Euler simples).
     Publica `motion.position` com `x_m`, `y_m`, `orientacao_graus`,
     `velocidade_m_s`. Usa `motion.steps_per_meter` e
     `motion.odometry_correction_factor` já existentes (fator ajustado
     pela autocalibração do Cap 12 s.9, ainda não implementada).
  2. A cada telemetria com `imu_conectado=true`, verifica
     `inclinacao_graus` contra `motion.tilt_limit_degrees` e
     `impacto_detectado`. Em perigo, publica **`safety.safe_mode_entered`**
     (`Prioridade.CRITICA`) - usei o evento já definido no Cap 18 s.9
     para isso (mais específico que `diagnostic.error`, que é genérico
     de falha de módulo). Volta a normalizar publica
     `safety.safe_mode_exited`. Edge-triggered (só publica na mudança de
     estado) para não spammar a cada 500ms enquanto a condição persiste.
- **Config novo:** adicionado `motion.wheel_base_m: 0.30` em
  `config/orion.yaml` - distância entre as rodas, necessária pra fórmula
  de rotação diferencial e que não existia na configuração. Marcado
  explicitamente como **PLACEHOLDER** no comentário do YAML: nenhuma
  roda/encoder físico está montado ainda, então é só um valor plausível
  para um chassi pequeno - medir e ajustar quando o chassi for montado
  de verdade (regra #6 do ARQUITETURA.txt: nada de valor físico fixo no
  código - pelo menos agora está no YAML, não hardcoded).
- **Decisão de escopo (documentada no docstring do módulo):** o Cap 12
  s.8 fala em combinar "orientação... da MPU6050" na fusão, mas a
  telemetria de hoje (`telemetry_manager.h`) só expõe
  `inclinacao_graus` (ângulo de inclinação do chassi) e
  `impacto_detectado` - **não há yaw/heading nem giroscópio bruto**
  disponível no pacote. Não dá pra fazer fusão de rumo com a IMU de
  verdade com o dado que existe hoje. Por isso: odometria por encoder
  pura para (x, y, orientação), IMU usada só para a detecção de
  segurança, nunca para corrigir o rumo calculado. Mesmo padrão de
  "mínimo viável + gap documentado" já usado em FOLLOW/EXPLORE do
  `navigation_core.py`. Também não há um sinal de "tombamento"
  separado do firmware - inclinação acima do limite cobre os dois
  casos (perigosa e tombamento extremo) por ora.
- **Testes:** `tests/unit/test_fusao_sensores.py`, 12 casos novos, com
  telemetria sintética (sem motores/encoders físicos montados - mesma
  situação de sempre nesta fase): reta sem giro, giro proporcional à
  diferença de passos (fórmula conferida com `math.degrees` no próprio
  teste), fator de correção de calibração aplicado, telemetria sem
  campos de encoder ignorada, contador de passos regredindo (ex.: Mega
  reiniciou) resincroniza sem publicar posição fantasma, inclinação/
  impacto disparando e normalizando `safe_mode_entered`/`exited`, e não
  republicar a cada telemetria enquanto o perigo persiste.
- **143 + 12 = 155 testes passando + 8 pulados no Raspberry** (Vision/
  Voice, de propósito). `ruff check` limpo nos arquivos tocados.
- **Não testado com deslocamento real** (sem motores/encoders montados
  - só a parte de segurança da IMU pode ser validada com o Mega físico
  de verdade, já que a MPU6050 está conectada). Cap 12 s.8 fica com a
  fusão implementada e testada em nível de protocolo, mas **sem
  validação com movimento físico real** - normal nesta fase, mesmo
  padrão dos demais itens de Fase 7.
- **`PLANO_IMPLEMENTACAO.md` não alterado**: o "pronto quando" da Fase 7
  inteira ainda depende de patrulha real com desvio, FOLLOW mantendo
  distância e autocalibração rodando de verdade - nenhum desses itens
  está pronto (sem motores montados). Fusão de sensores é só uma peça
  do que falta.
- **Próximo passo:** autocalibração (Cap 12 s.9) quando houver motores
  montados, ou avançar pra Fase 8 (interface web) usando o que já
  existe. `motion.position` e os eventos `safety.safe_mode_*` já estão
  publicados no Event Bus para quando a interface (Cap 13) ou o Mission
  Core quiserem consumi-los.

## 2026-07-17 (Fase 8, continuação — Dashboard web do Raspberry)

- **Criado `motion_core/webui/`** (`WebUIServer`, Cap 13 s.4-5): servidor
  aiohttp rodando **no Raspberry** (diferente do avatar, que é
  Notebook-only) - primeira parte da interface web, o painel DASHBOARD.
  Diferente do avatar (repassador puro sem estado), este módulo mantém um
  pequeno cache em memória do último valor de cada coisa relevante
  (`_estado`), porque quem abre a página precisa ver o estado ATUAL na
  hora, não só esperar o próximo evento - ainda assim nenhuma lógica de
  DECISÃO mora aqui (Cap 13 s.2), só agregação pra exibição.
- **Rotas:** `GET /` (HTML), `GET /estado` (snapshot JSON agregado + últimos
  30 eventos, pra quem acabou de conectar), `GET /eventos` (SSE, mesmo
  padrão do avatar). Consome `system.*`, `motion.*`, `navigation.*`,
  `vision.*`, `voice.*`, `diagnostic.*` (Cap 13 s.7) **e `safety.*`**
  (evento novo que a Fusão de Sensores passou a publicar hoje mesmo) e
  `comm.mensagem.telemetry`.
- **Painéis implementados (Cap 13 s.5):** Sistema (modo/estado), Segurança
  (SAFE_MODE ativo/motivo), Telemetria (distância/temperatura/umidade/
  inclinação - bateria/energia mostrada como "não disponível nesta
  versão", honesto em vez de inventar), Posição estimada (x/y/orientação/
  velocidade, direto do `motion.position` da Fusão de Sensores),
  Missão/Voz/Visão, e um log dos últimos eventos.
- **`aiohttp` instalado de verdade neste Raspberry** (extra `display` do
  `pyproject.toml`) - diferente do avatar, que roda só no Notebook, o
  dashboard roda aqui, então os testes rodam de verdade aqui também (não
  pulam mais).
- **9 testes novos** em `tests/unit/test_webui_server.py`, mesmo padrão do
  `test_avatar_server.py` (`TestClient`/`TestServer`, sem mock solto).
  Acertei o fixture assíncrono (`@pytest_asyncio.fixture`) de primeira
  desta vez - já tinha caído nesse erro duas vezes antes nesta sessão.
- **168 testes passando + 7 pulados no Raspberry** (os 4 do avatar saíram
  da lista de pulados agora que `aiohttp` está instalado aqui; sobrou só
  Vision/Voice/IA, que continuam Notebook-only de propósito) - **206
  passando no Notebook** (0 pulados lá).
- **Limpeza:** removido `motion_core/src/` (scaffold morto do zip
  original - diretórios vazios `bridge/memory/navigation/webui` sem
  nenhum arquivo, nunca referenciados em lugar nenhum) - o código de
  verdade sempre esteve em `motion_core/memory/`, `motion_core/navigation/`
  e agora `motion_core/webui/`, direto, sem esse `src/` a mais.
- **Novo `tools/preview_webui.py`** (mesmo espírito do
  `preview_avatar.py`): sobe o servidor com telemetria/posição/missão/
  SAFE_MODE de mentirinha em ciclo, pra ver o dashboard funcionando sem
  precisar do robô inteiro.
- **Validado ao vivo de verdade:** aberto num Firefox separado no
  Notebook (mesma rede, `http://10.20.20.185:8080` - o Raspberry
  respondendo por IP, não só localhost, confirmando "acessível de
  qualquer dispositivo da rede local" do Cap 13 s.4) e depois colocado
  em modo kiosk **na tela principal do Notebook, no lugar do Fofão**
  (a pedido do usuário, temporariamente) - screenshots confirmam os
  painéis atualizando ao vivo via SSE.
- **Ajuste de CSS no caminho:** o layout inicial de rótulo/valor
  (`grid-template-columns: auto 1fr`) quebrava palavras longas de forma
  feia (`overflow-wrap: anywhere` cortando no meio da palavra); trocado
  por uma coluna de rótulo mais larga (`minmax(auto, 42%)`) e
  `word-break: break-word`, que quebra em pontos melhores.
- **Não implementado ainda (Cap 13 s.4):** as páginas CONVERSA, MAPA
  (radar polar), DIAGNÓSTICO e CONFIGURAÇÃO - só o DASHBOARD existe por
  ora. `PLANO_IMPLEMENTACAO.md` não alterado (o item da Fase 8 cobre as
  5 páginas + mapa do radar, nenhum dos dois checkboxes está totalmente
  atendido ainda).
- **Não commitado ainda.**
- **Próximo passo:** CONVERSA (histórico de conversa via `memory_client`/
  Fase 3), MAPA (radar polar - já dá pra usar `motion.scan_complete`),
  DIAGNÓSTICO (heartbeats/últimos erros/log, Cap 16), CONFIGURAÇÃO
  (acesso restrito) - ou trocar o Notebook de volta pro avatar quando o
  usuário quiser.
- **Simplificado a pedido do usuário logo em seguida:** removido o painel
  "Missão/Voz/Visão" (Voz e Visão sempre vazios nesta fase - Voice/Vision
  Core não rodam no Raspberry) e a linha "Bateria/energia" (sempre "não
  disponível") - "último passo da missão" foi pro painel Sistema. Ficou
  em 4 painéis (Sistema, Segurança, Telemetria, Posição) + o log de
  eventos, em vez de 5 + log. O servidor (`server.py`, `/estado`) continua
  expondo `voz`/`visao` no JSON - só a página não mostra mais, caso uma
  futura página (CONVERSA) queira usar. Testes do servidor inalterados (9
  passando) - só HTML/JS mudaram. Recarregado no kiosk do Notebook,
  confirmado por screenshot.

## 2026-07-17 (Fase 8, continuação — Mapa polar do radar)

- **Nova página `motion_core/webui/static/mapa.html`/`mapa.js`/`mapa.css`**
  (Cap 13 s.4: "MAPA — radar polar (0°–180°), posição e orientação
  estimadas") - consumidor puro via SSE, igual as outras páginas.
  Desenha em `<canvas>`: anéis de alcance a cada 50cm (até 200cm, escala
  fixa por ora), raios nos 7 ângulos de leitura, o robô como triângulo no
  centro apontando "pra frente", e o polígono conectando as leituras
  válidas da última `motion.scan_complete` (leituras inválidas viram um
  ponto apagado na borda em vez de sumir, pra deixar claro que aquele
  ângulo não tem dado, não que está livre).
- **`WebUIServer`:** adicionada rota `GET /mapa` e `motion.scan_complete`
  passou a atualizar `_estado["mapa"]["leituras"]` (já estava na lista de
  tópicos consumidos, só não gravava em lugar nenhum ainda).
- **Navegação entre páginas:** cabeçalho comum (Dashboard/Mapa) adicionado
  em `index.html` e `mapa.html`, com a página atual destacada.
- **2 testes novos** (`GET /mapa` serve HTML; `motion.scan_complete`
  atualiza o estado) - 11 no arquivo do servidor web, 170 passando + 7
  pulados no total do Raspberry.
- **`tools/preview_webui.py`** ganhou uma varredura simulada (7 ângulos,
  distância variando em seno, leitura central "some" de vez em quando pra
  testar o caso de leitura inválida) - sem isso o mapa não tinha o que
  desenhar.
- **Validado ao vivo:** kiosk do Notebook trocado pra `/mapa` a pedido do
  usuário ("cadê o mapa") - screenshot confirma o polígono desenhando
  certo, os rótulos dos anéis, e "7/7 leituras válidas" no rodapé.
- **Não commitado ainda.**
- **Próximo passo:** CONVERSA (Fase 3/`memory_client`) e DIAGNÓSTICO (Cap
  16 - heartbeats, últimos erros, log) são as duas páginas que faltam;
  CONFIGURAÇÃO (acesso restrito, Cap 13 s.4) é a última. `mapa.js` tem uma
  limitação conhecida a documentar quando tiver dado real: a escala fixa
  de 200cm é um chute razoável pra ambiente interno, não vem do
  `config/orion.yaml` ainda - revisitar se precisar de alcance maior.

## 2026-07-17 (Fase 8, continuação — Diagnóstico e Conversa)

- **Página DIAGNÓSTICO** (Cap 13 s.4-5): `GET /diagnostico` +
  `GET /log`. Passou a consumir `diagnostic.error`, `comm.link_degraded`
  (últimos erros), `comm.module_lost`/`comm.module_recovered` (saúde dos
  módulos - "ok"/"perdido" por nome, com timestamp). `GET /log` expõe as
  últimas N linhas (padrão 200, máx. 2000) de `data/logs/orion.log`
  **somente leitura** (Cap 13 s.4) - se o arquivo não existir ainda,
  devolve aviso em vez de erro.
- **Página CONVERSA** (Cap 13 s.4: "transcrição da interação por voz"):
  `GET /conversa` + `GET /api/conversas`. Diferente das outras páginas,
  não guarda o histórico em memória - consulta a **Fase 3 (Memory Core)
  direto** via `MemoryAPI.recall("conversas", ...)`, porque como o
  servidor roda no próprio Raspberry, dá pra ler o banco local sem
  precisar de rede (Cap 13 s.4: "consultas de histórico... locais ao
  SSD"). `WebUIServer` ganhou um parâmetro opcional `memory_api` -
  quando `None` (banco não montado, como agora nesta máquina de dev, sem
  SSD em `/mnt/ssd/orion/`), a página mostra aviso em vez de quebrar.
  Novos balões de conversa chegam via `memory.updated` (evento que
  `MemoryAPI.remember()` já publicava desde a Fase 3) - o evento só avisa
  "recarregue", o dado de verdade sempre vem da API, nunca duplicado.
- **9 testes novos** (diagnóstico: erro registrado, saúde de módulo
  atualizada, `/log` bem formado; conversa: HTML serve, aviso sem
  `memory_api`, histórico real com um `DatabaseManager`/`MemoryAPI` de
  teste em `tmp_path` - mesmo padrão do `test_memory_database.py`).
  **177 passando + 7 pulados no Raspberry, 215 passando no Notebook.**
  `ruff` limpo.
- **Todas as 4 páginas** (Dashboard/Mapa/Diagnóstico/Conversa) com
  navegação cruzada no cabeçalho agora.
- **Não commitado ainda.**
- **Falta só CONFIGURAÇÃO** (Cap 13 s.4: "acesso restrito") pra fechar a
  Fase 8 por completo - decisão de design pendente (que tipo de
  restrição de acesso, já que o projeto não tem autenticação em lugar
  nenhum ainda). Usuário pediu pra deixar a revisão visual de tudo
  (colocar cada página na tela) pra depois de terminar de construir.

## 2026-07-17 (Fase 8, continuação — Configuração: as 5 páginas completas)

- **Página CONFIGURAÇÃO** (Cap 13 s.4: "parâmetros do sistema, acesso
  restrito"): `GET /configuracao` + `GET /api/configuracao`. Perguntei ao
  usuário como deveria funcionar o "acesso restrito" (projeto não tem
  autenticação em lugar nenhum) - escolheu **restringir por IP: só
  responde a pedidos vindos do próprio Raspberry** (`127.0.0.1`/`::1`),
  não do resto da rede local (diferente das outras 4 páginas). Fora do
  Raspberry, devolve HTTP 403. Somente leitura por ora - editar
  configuração ao vivo (com validação/reinício) fica pra uma iteração
  futura, mesmo espírito de escopo mínimo já usado em FOLLOW/EXPLORE e no
  log do DIAGNÓSTICO.
- **`WebUIServer` ganhou parâmetro opcional `config: ConfigurationManager
  | None`** - quando presente, `/api/configuracao` devolve
  `config.bruto()` (todo o `config/orion.yaml` parseado); quando `None`,
  mostra aviso em vez de quebrar (mesmo padrão de `memory_api`).
- **7 testes novos**: checagem de acesso local isolada (aceita
  `127.0.0.1`/`::1`, rejeita IP de rede), acesso via `TestClient` (que
  conecta por loopback de verdade, prova o caminho "permitido" ponta a
  ponta), API sem/com `ConfigurationManager` real carregado do
  `config/orion.yaml` de verdade. **182 passando + 7 pulados no
  Raspberry, 220 passando no Notebook.** `ruff` limpo.
- **As 5 páginas do Cap 13 s.4 existem agora**: DASHBOARD, MAPA,
  DIAGNÓSTICO, CONVERSA, CONFIGURAÇÃO, com navegação cruzada entre todas.
- **`PLANO_IMPLEMENTACAO.md` não alterado** - o "pronto quando" da Fase 8
  ("interface reflete eventos em < 500ms acessada do celular na rede
  local") foi demonstrado com SSE (latência bem abaixo de 500ms) e acesso
  de outro dispositivo na rede (o Notebook, via IP), mas **não com um
  celular de verdade** ainda - e o item "Acesso remoto via Raspberry Pi
  Connect documentado" nem foi começado. Nenhum checkbox da Fase 8
  marcado ainda, por essas duas razões.
- **Não commitado ainda.**
- **Próximo passo:** o usuário pediu pra fazer uma revisão visual de
  tudo (colocar cada página na tela) antes de continuar - isso vem antes
  de qualquer trabalho novo.

## 2026-07-17 (marco — sistema inteiro rodando de ponta a ponta pela primeira vez)

- **Usuário autorizou trabalho autônomo** ("continue sozinho, sem
  perguntar") e retomou a decisão adiada mais cedo hoje: WiFi como link
  principal Notebook↔Raspberry, com o Motion Core rodando de verdade.
  Guardado em memória (`feedback_orion_os_dont_ask_proceed`): não usar
  mais `AskUserQuestion` neste projeto para decisões de implementação,
  só para bloqueios reais (acesso físico, credenciais, ações destrutivas
  irreversíveis como reiniciar o Raspberry).
- **Criado `motion_core/__main__.py`** - processo principal do Raspberry
  (equivalente ao `python -m orion` do Notebook), rodando com
  `python -m motion_core`: sobe o servidor TCP (Cap 14 s.2), a ponte
  serial com o Arduino (com fallback tolerante se a porta não abrir ou o
  Arduino não responder WHO_ARE_YOU), `NavigationCore` + `FusaoSensores`
  (Fase 7), o banco de dados se o SSD estiver montado, e o `WebUIServer`
  (Fase 8) - tudo num único Event Bus. Roda até Ctrl+C/SIGTERM, com
  desligamento limpo de tudo.
- **`config/orion.yaml`:** `communication.raspberry.host` trocado de
  `192.168.50.2` (sub-rede Ethernet de produção, EDR-0018, ainda não
  existe fisicamente) para `10.20.20.185` (IP real do Raspberry na rede
  WiFi atual) - comentário no YAML deixa claro que isso é o link de
  desenvolvimento, não uma mudança de arquitetura.
- **`src/orion/kernel/boot.py`:** a etapa "Detecção do Raspberry Pi"
  (Fase 2), que desde a Fase 1 só logava "não implementado", agora tenta
  de verdade conectar via TCP + `WHO_ARE_YOU` (`_conectar_raspberry`),
  com timeout de 3s e tolerância a ausência (Cap 6 s.8) - Raspberry
  desligado ou fora da rede não trava nem aborta o boot do Notebook.
  `SistemaOrion` ganhou os campos `comm` e `raspberry_conectado`.
- **VALIDADO DE VERDADE, PELA PRIMEIRA VEZ NO PROJETO:** `python -m
  motion_core` rodando aqui no Raspberry (com o Arduino real conectado
  via WHO_ARE_YOU) + `python -m orion` rodando no Notebook real, ao
  mesmo tempo, por WiFi de verdade - log do Notebook:
  `Descoberta OK: destino=motion_core nome=motion_core versao_modulo=0.1.0`
  /  `Raspberry (Motion Core) conectado: motion_core v0.1.0`. As três
  pontas do Fofão (Notebook, Raspberry, Arduino) nunca tinham
  conversado ao mesmo tempo antes de hoje.
- **Achado real, não corrigido ainda:** como `python -m orion` hoje ainda
  é um boot único que conecta e encerra na hora (não é um processo de
  longa duração - decisão registrada anteriormente no jornal), o
  `MonitorHeartbeat` do Raspberry continua tentando mandar heartbeat pro
  Notebook depois que ele já desconectou, gerando avisos repetidos
  (`Falha ao enviar heartbeat` / `socket.send() raised exception`) em vez
  de detectar o link morto e desistir. Não bloqueia nada hoje (o processo
  encerra limpo do mesmo jeito quando eu mando SIGTERM), mas é uma
  robustez real faltando em `MonitorHeartbeat` (Fase 2,
  `src/orion/communication/heartbeat.py`) - ele só detecta perda pela
  ausência de heartbeats *recebidos*, não por falha ao *enviar*. Fica
  como pendência aberta.
- **5 testes novos** de integração (`tests/integration/test_motion_core_main.py`,
  marcador `sim`): conectar no Arduino com sucesso (pty simulando o
  firmware, mesmo padrão da Fase 2), tolerar porta inexistente, tolerar
  porta existente mas sem resposta WHO_ARE_YOU, abrir memória com/sem SSD
  disponível. **192 passando + 7 pulados no Raspberry, 230 passando no
  Notebook.** `ruff` limpo.
- **Não commitado ainda.**
- **Próximo passo:** corrigir o `MonitorHeartbeat` pra detectar falha de
  envio como perda de link (achado acima); ou fazer `python -m orion`
  virar um processo de longa duração de verdade, pra a conexão com o
  Raspberry durar mais que alguns segundos; ou seguir para a Fase 9
  (Diagnóstico e Segurança, Caps 16/18).

## 2026-07-17 (correção do achado do MonitorHeartbeat + bug de import circular)

- **Corrigido o achado de robustez do `MonitorHeartbeat`** registrado na
  entrada anterior: `enviar_heartbeat()` falhando (link fechado do outro
  lado) agora também conta como perda - unificado com a detecção por
  ausência de recebimento na mesma função `_marcar_perdido` (idempotente
  via `_perdidos_atualmente`, publica `comm.module_lost` só uma vez).
  Antes, um peer desconectado gerava aviso de log pra sempre e nunca
  disparava `comm.module_lost`.
- **Teste de regressão novo:** `test_falha_ao_enviar_heartbeat_tambem_gera_comm_module_lost`
  (`tests/unit/test_heartbeat.py`) - monitora um peer sem nunca registrar
  o link (`enviar_heartbeat` sempre falha com "sem rota"), confirma que
  `comm.module_lost` é publicado uma única vez, não repetido a cada
  tentativa.
- **Bug real encontrado e corrigido no caminho:** ao adicionar os imports
  de `orion.communication.*` no topo de `src/orion/kernel/boot.py`
  (entrada anterior), isso quebrou a importação do pacote inteiro
  `orion.kernel` com um ciclo de import - `orion/kernel/__init__.py`
  importa `boot.py` de cara, `boot.py` importava `orion.communication.*`,
  que importa `orion.kernel.event_bus`, fechando o ciclo. Corrigido
  movendo esses imports pra dentro das funções que os usam (import local,
  com um comentário explicando o motivo) e usando
  `if TYPE_CHECKING: from orion.communication.service import
  ComunicacaoService` só para as anotações de tipo continuarem
  funcionando sem re-introduzir o ciclo em tempo de execução.
- **193 testes passando + 7 pulados no Raspberry, 231 passando no
  Notebook.** `ruff` limpo. Item do jornal anterior fechado - não é mais
  pendência aberta.
- **Não commitado ainda.**

## 2026-07-18 (primeiro teste do pan/tilt no hardware físico + bug real no firmware + investigação de brownout)

- **Renomeação:** todo o texto do repositório que dizia "ORION X" (nome
  provisório do robô usado nos e-mails/specs até ontem) foi trocado para
  **Fofão** - `README.md`, `ARQUITETURA.txt`, `docs/ses/`, `docs/edr/`,
  `config/orion.yaml` (`robot_name`), testes e `docs/hardware/`. Conferido
  antes que `robot_name` só é usado como texto de exibição (logs, web UI),
  sem validação de valor fixo - troca segura. 183 testes passando + 7
  pulados, `ruff` limpo depois da troca.
- **Hardware físico do pan/tilt chegou hoje** (usuário tinha avisado
  ontem que chegaria "amanhã") - primeira vez testando os dois servos
  reais, ainda sem os motores de passo (esses ainda não foram montados).
- **Bug real encontrado e corrigido no firmware:** `CommandExecutor` é um
  objeto global (`comandos`, em `main.cpp`) e fazia `pinMode()` +
  `Servo::attach()` + `Servo::write()` direto no construtor. Construtores
  de objetos globais em C++/Arduino rodam **antes** de `main()` chamar
  `init()` (que configura os timers usados pelo PWM) - então o `init()`
  pisava a configuração do timer que o `Servo::attach()` tinha acabado de
  fazer, e o servo nunca respondia fisicamente, mesmo com o firmware
  respondendo ACK normalmente (o ACK só confirma que a mensagem chegou,
  não que o comando teve efeito físico). Sintoma no usuário: "girou nada"
  mesmo com todos os `SET_PAN_TILT` sendo confirmados via protocolo.
  Corrigido movendo a inicialização pra um método `iniciar()` chamado
  dentro de `setup()` (mesmo padrão já usado por `motores.iniciar()`,
  `encoders.iniciar()`, `radar.iniciar()`, etc. - `CommandExecutor` era o
  único que fugia desse padrão).
- **Descoberta física no caminho:** depois da correção de firmware, o
  servo ainda não girava - causa era energia mesmo (fonte dedicada dos
  servos não estava ligada). Depois de conectada, confirmado visualmente
  que pan (pino 10) e tilt (pino 11) giram fisicamente de verdade -
  primeira validação física real do Cap 10 s.2 / Cap 8 s.8 nesse projeto.
- **Achado aberto, não resolvido ainda - suspeita de brownout do Mega:**
  comandos `SET_PAN_TILT` falham em receber ACK de forma intermitente e
  não-determinística (às vezes no 1º comando da sessão, às vezes no 5º),
  e logo depois até `RETURN_STATUS` simples fica sem resposta por vários
  segundos - padrão típico de o Mega resetar (bootloader + `setup()` de
  novo) no meio da comunicação. Só acontece em comandos que de fato
  chamam `Servo::write()` - nunca em `WHO_ARE_YOU`/`RETURN_STATUS`.
  Descartado RAM (instrumentei `RETURN_STATUS` com `ram_livre_bytes` via
  `__brkval`/`__heap_start` - ver `memoriaLivre()` em `main.cpp`, ainda
  marcado como diagnóstico temporário; RAM livre estava em ~5.3KB de 8KB
  no momento da falha, longe de esgotar). Descartado também tamanho do
  buffer de quadro (`DecodificadorQuadro::CAPACIDADE = 320` bytes, folga
  grande para o payload do `SET_PAN_TILT`). Descartado não ser
  proporcional ao ângulo do movimento (pan=2°/5° passaram, pan=-5° logo
  em seguida falhou). Usuário confirmou GND comum entre a fonte dos
  servos e o Mega (descarta essa causa) e fonte de 3A (descarta
  insuficiência de corrente em regime). Hipótese que sobra: pico de
  corrente **transiente** (milissegundos) no instante em que o servo
  começa a girar, rápido demais para a malha de regulação da fonte
  reagir, mesmo numa fonte de 3A nominal - fix recomendado é um
  capacitor eletrolítico (470-1000 µF, ≥10V) entre +5V e GND bem perto
  dos servos, pra absorver esse pico localmente. Usuário vai instalar o
  capacitor e retestar com `tools/testar_pan_tilt.py` (script novo,
  criado hoje, fala direto com o Hardware Core pela serial sem precisar
  subir o `motion_core` inteiro - mostra `uptime_ms` e `ram_livre_bytes`
  a cada passo pra distinguir reset real de perda de pacote pontual).
- **Confirmado nesta sessão:** kiosk do avatar (Fofão) já sobe sozinho no
  Notebook depois de um reboot - autologin + autostart configurados
  ontem (Fase 8) validados de verdade pela primeira vez.
- **Causa raiz real encontrada (não era elétrica):** capacitor instalado
  pelo usuário não mudou nada - falha idêntica, mesmo comando, mesmo
  `uptime_ms`/`ram_livre_bytes`, o que já indicava algo determinístico no
  firmware, não brownout. Testado desligando temporariamente o
  `HEARTBEAT`/`TELEMETRY` periódico do `loop()` (comentados) - com isso,
  os 10 comandos do teste passaram 100% das vezes, uptime subindo sem
  interrupção. Causa confirmada: `Serial.write()` do `HEARTBEAT`
  (a cada 1s) e do `TELEMETRY` (a cada 500ms) é **bloqueante** sempre que
  a mensagem excede o buffer de transmissão padrão do core AVR (64
  bytes) - enquanto bloqueado, o `loop()` não volta a checar
  `Serial.available()`, e o buffer de **recepção** (também só 64 bytes
  por padrão) pode estourar se um `COMMAND` estiver chegando ao mesmo
  tempo, corrompendo/perdendo bytes do quadro (descartado silenciosamente
  por CRC inválido - Cap 14 s.5) - dai o ACK nunca chegava.
- **Fix aplicado:** `firmware/hardware_core/platformio.ini` ganhou
  `build_flags` definindo `SERIAL_RX_BUFFER_SIZE=256` e
  `SERIAL_TX_BUFFER_SIZE=256` (padrão do core é 64) - RAM do Mega (8KB)
  tem sobra de sobra pros +384 bytes. `HEARTBEAT`/`TELEMETRY` religados
  no `loop()`. Instrumentação temporária de RAM (`ram_livre_bytes` no
  `RETURN_STATUS`, `memoriaLivre()`) removida do firmware depois de servir
  pra descartar a hipótese de esgotamento de heap. **Reconfirmado com o
  firmware final: 10/10 comandos do `tools/testar_pan_tilt.py` passando,
  uptime contínuo sem nenhum reset.**
- **Pan e tilt validados fisicamente de ponta a ponta, de forma
  confiável** - primeira vez no projeto. Fecha o critério de "pronto" do
  `SET_PAN_TILT` (Cap 8 s.8) no nível de hardware, faltando só conectar
  o Vision Core de verdade a esses comandos (hoje só testado
  manualmente via `tools/testar_pan_tilt.py`).
- **Renomeação incompleta corrigida:** o sed de "ORION X" -> "Fofão" de
  mais cedo hoje só cobriu `*.md`/`*.yaml`/`*.py` e deixou passar HTML/
  JS/CSS da interface web e do avatar, `tools/orion-avatar.service`/
  `.desktop`, e principalmente `config/prompt_sistema.txt` - o robô se
  apresentaria como "ORION X" numa conversa de voz de verdade (Fase 6).
  Corrigido e sincronizado também no Notebook via scp.
- **Achado real no Notebook - gap na config de "nunca dormir" (Fase 6):**
  usuário reportou a tela do kiosk entrando em descanso sozinha.
  Systemd sleep/suspend/hibernate seguiam mascarados, logind com
  `HandleLidSwitch=ignore` etc., DPMS desligado - tudo isso conferido e
  correto. O que faltava: o protetor de tela **nativo do X11** (`xset
  s`), mecanismo separado do DPMS, seguia no padrão (`timeout: 600`,
  `prefer blanking: yes`) - apagava a tela depois de 10min parado
  mesmo com o resto certo. Corrigido com `xset s off; xset s noblank;
  xset -dpms` embutido no `Exec=` do `.desktop` do kiosk, pra rodar
  toda vez que o autostart sobe (sobrevive a reboot).
- **Próximo passo:** conectar o Vision Core (rastreamento de rosto) ao
  pan/tilt de verdade; motores de passo ainda não foram montados
  fisicamente (fica para outra sessão).
- **Commitado:** histórico reconstruído em 11 commits (Fase 0 a 8 + 2
  commits de hoje) - ver `git log --oneline`.

## 2026-07-18 (continuação - motores e ultrassons chegaram fisicamente)

Usuário conectou fisicamente hoje: os 2 motores de passo (NEMA17 via
TB6600), o ultrassom traseiro (pinos 26/27, antes reservado) e o
ultrassom frontal remontado no servo do radar (pino 9, antes fixo sem
servo). Primeira vez testando esses três com hardware real.

- **`pins.h` atualizado:** `ULTRASSOM_TRAS_TRIG/ECHO` e `SERVO_RADAR`
  movidos de RESERVADO para CONFIRMADO. Comentário desatualizado do
  `RadarManager` (dizia "ultrassom fixo, sem servo") corrigido.
- **Ultrassom traseiro ganhou código de verdade:** antes só tinha os
  pinos reservados em `pins.h`, nenhum manager lia eles. Adicionado
  `SensorUltrassonico ultrassomTraseiro` global em `main.cpp`,
  `TelemetryManager` ganhou o campo `distancia_traseira_cm`/`_valida`
  (novo parametro no construtor).
- **Servo do radar (pino 9):** confirmado fisicamente girando (o
  `RadarManager::iniciar()` ja chamava `_servo.attach()` dentro de um
  metodo proprio chamado do `setup()`, entao nao tinha o mesmo bug de
  construtor global do pan/tilt de mais cedo).
- **Bug real corrigido - `ENABLE_ATIVO_EM_BAIXO`:** motores completamente
  mudos/parados (nem vibravam) com o valor original (`true`, ativo em
  baixo). Invertido para `false` - os TB6600 desta montagem só habilitam
  com o pino em HIGH. Comentário do código ja antecipava essa
  possibilidade (`motor_manager.h`).
- **Achado NÃO resolvido - ultrassons sem eco:** mesmo com energia e
  GND comum confirmados pelo usuário, `echo_frontal_ja_visto_alto` e
  `echo_traseiro_ja_visto_alto` (flag nova em `SensorUltrassonico`,
  fica `true` para sempre no primeiro ECHO valido desde o boot -
  substituiu um diagnostico anterior por amostragem via
  `RETURN_STATUS` que era pouco confiavel, dado o pulso de ECHO poder
  ser mais curto que o intervalo de amostragem) continuam `false`
  depois de mais de 8s (100+ tentativas de trigger) em ambos os
  sensores. TRIG/ECHO conferidos contra os pinos certos (22/23
  frontal, 26/27 traseiro) pelo usuário. Causa raiz não encontrada -
  precisa de multímetro (medir se o TRIG realmente pulsa no pino certo,
  se ECHO tem 5V quando deveria, GND de verdade comum).
- **Achado NÃO resolvido - motores não giram, apesar de tudo checado:**
  depois do fix do `ENABLE_ATIVO_EM_BAIXO`, motor chegou a "quase girar"
  uma vez (zumbido, sem completar o giro - padrão de corrente
  insuficiente), mas depois de ajustar a corrente (usuário mexeu de
  0.5A nominal do motor até 1.2A, depois para ~0.7A) parou de fazer até
  isso. Checklist completo tentado sem sucesso: ENABLE nas duas
  polaridades E flutuando (pino sem `pinMode(OUTPUT)`), corrente em
  varios valores, STEP/DIR reencostados, fios da bobina invertidos,
  GND comum de PUL-/DIR-/EN- confirmado, testado inclusive com um
  motor+cabo de uma CNC (conhecido bom) no lugar do NEMA17 - mesmo
  resultado. **Prova definitiva de que não é código:** com
  `tools/testar_pan_tilt.py`-style telemetria, confirmado que o
  firmware gera e conta os 200 passos certos (`passos_esquerda`/
  `passos_direita` = 200 para um `MOVE_DISTANCE` de 5cm). Escrito
  tambem um sketch minimo (`/tmp/.../motor_diag/`, fora do repo,
  descartavel) que pulsa STEP/DIR/ENABLE via `digitalWrite()` puro, sem
  nenhum codigo do protocolo, alternando a polaridade do ENABLE
  sozinho a cada 10s - motor tambem nao reagiu. Ou seja: testado em
  dois niveis de software (protocolo completo + GPIO cru) e varias
  frentes de hardware, sem sucesso. Suspeita mais forte agora: driver
  TB6600 com defeito, ou rompimento fisico de fiacao entre Mega e
  driver nao visivel sem multimetro (continuidade fio a fio). Pausado
  aqui - precisa de instrumento de medicao pra continuar.
- **Susto a parte:** o servo de tilt teve um episodio de movimento
  erratico ("ficou maluco") no meio da sessão, provavelmente ruído
  induzido pelo manuseio da fiação dos motores por perto. Mega
  confirmado vivo e saudável durante o episódio (RETURN_STATUS normal).
  Retestado depois com `tools/testar_pan_tilt.py` e voltou ao normal
  (10/10 comandos ok) - não reaconteceu, mas fica registrado caso volte.
- **Pendências físicas para retomar:** (1) multímetro para depurar os
  ultrassons (TRIG pulsando? ECHO com 5V?) e os motores (continuidade
  Mega→driver→motor, tensão real no driver); (2) considerar testar os
  TB6600 isolados (sem o Mega, com um gerador de pulso simples ou
  outro microcontrolador) para confirmar se o defeito é mesmo no
  driver.
- **Não commitado ainda** (mudanças de hoje: `pins.h`, `radar_manager.h`,
  `telemetry_manager.h`, `main.cpp`, `motor_manager.h`,
  `sensor_ultrassonico.h`).

## 2026-07-18 (fechamento da sessão - pinagem dos ultrassons confirmada correta, ainda sem sinal)

- **Motores - decisão do usuário:** depois do checklist completo sem
  sucesso (ver entrada anterior), incluindo testar com motor+cabo+driver
  **inteiros** de uma CNC (conjunto conhecido bom, mesmo resultado: nada),
  usuário decidiu não perseguir mais o NEMA17/TB6600 por agora e já
  comprou motores DC de 2 fios + ponte H. Arquitetura de controle é
  bem diferente (PWM de velocidade + pino de direção, sem STEP/DIR/
  micropasso) - `motor_manager.h` vai precisar ser reescrito do zero
  quando o hardware novo chegar. Até lá, o `MotorManager` atual
  (stepper) fica como está no código, sem mais tentativas de debug.
- **Ultrassons - pinagem confirmada correta, causa raiz ainda não
  encontrada:** usuário conferiu fio a fio e confirmou explicitamente:
  frontal TRIG=22/ECHO=23, traseiro TRIG=26/ECHO=27 - exatamente o que
  `pins.h` espera (bateu certinho depois de descartar duas hipóteses
  no caminho: um sensor temporariamente achado em pinos 24/25, que são
  do DHT/LED no nosso firmware - acabou não sendo o caso real; e uma
  inversão TRIG/ECHO no traseiro, também descartada na checagem final).
  Mesmo com a pinagem 100% batendo com o código, `echo_frontal_ja_visto_alto`
  e `echo_traseiro_ja_visto_alto` continuam `false` - nenhum pulso de
  ECHO chegou nos pinos do Mega em nenhum teste do dia. Mesma
  conclusão dos motores: sem multímetro pra medir tensão real nos
  pinos (TRIG realmente pulsando? VCC do sensor realmente em 5V? ECHO
  do sensor emitindo algo?), não dá pra achar a causa exata só
  verificando fiação visualmente.
- **Confirmado funcionando de verdade hoje:** pan, tilt e o servo do
  radar (varredura física do `SCAN_FRONT`) - os três reconfirmados
  pelo usuário no fim da sessão.
- **Resumo do estado físico ao final do dia:**
  - ✅ Pan/tilt (servos, pinos 10/11)
  - ✅ Servo do radar (pino 9) - varredura física confirmada
  - ⚠️ Ultrassom frontal e traseiro (pinos 22/23, 26/27) - pinagem
    correta, sem sinal, precisa de multímetro
  - ⚠️ Motores de passo (NEMA17/TB6600) - pausado, indo para motor DC
    + ponte H (hardware novo a caminho)
- **Próxima sessão:** (1) quando os motores DC + ponte H chegarem,
  escrever `motor_manager.h` novo pra essa arquitetura; (2) com
  multímetro em mãos, medir TRIG/VCC/ECHO ponto a ponto nos dois
  ultrassons; (3) considerar Vision Core -> pan/tilt de verdade
  (Fase 5 -> hardware), já que pan/tilt está validado.
- **Não commitado ainda.**



## 2026-07-18 (noite - diagnostico de software cravou a causa dos ultrassons: ECHO flutuando)

- **Ferramenta nova:** `tools/testar_ultrassom.py` - monitor ao vivo dos
  dois ultrassons via RETURN_STATUS (distancias, flags de validade e os
  flags `echo_*_ja_visto_alto`), no estilo do `testar_pan_tilt.py`.
  Primeira rodada: mesmo resultado da sessao anterior, nenhum ECHO
  desde o boot em nenhum sensor.
- **Sketch descartavel `ultra_diag`** (scratchpad, fora do repo), em 3
  versoes progressivas, todas com `pulseIn()` bloqueante puro (zero
  codigo do protocolo):
  - v1: ECHO lia HIGH constante *mesmo sem pullup* nos dois sensores;
    `pulseIn` sempre 0. Primeira hipotese: clone do HC-SR04 travado
    com ECHO preso em HIGH.
  - v2 (truque de destravamento - forcar ECHO LOW como OUTPUT por
    10ms): depois disso o pino passou a ler LOW constante. O "HIGH
    constante" da v1 era so a carga do pullup retida no fio - ou seja,
    o pino segue o que a gente grava nele.
  - v3 (prova do fio flutuante): carrega o pino em HIGH -> solta ->
    ainda le 1 apos 50ms; descarrega em LOW -> solta -> le 0. Nos DOIS
    sensores, em todos os ciclos. **Um pino conectado a um sensor vivo
    forcaria o proprio nivel; segurar a carga gravada e assinatura de
    pino eletricamente flutuando.**
- **Conclusao:** os pinos 23 (ECHO frontal) e 27 (ECHO traseiro) do Mega
  NAO tem conexao eletrica com uma saida ativa. Duas causas possiveis:
  (a) os fios de ECHO nao chegam eletricamente ao Mega (jumper ruim/
  trilha interrompida), ou (b) **os sensores estao sem alimentacao** -
  HC-SR04 sem VCC fica com a saida em alta impedancia, que flutua
  igualzinho. Como os DOIS sensores flutuam identico, a causa comum
  mais provavel e alimentacao (ex.: trilho de 5V da protoboard
  interrompido no meio - pegadinha classica - ou fio VCC/GND do trilho
  solto).
- **Nao e codigo, nem pinagem:** confirmado em dois niveis (firmware
  completo + sketch cru) e com a pinagem ja conferida fio a fio na
  sessao anterior.
- **Atencao:** o Mega esta com o sketch de diagnostico gravado agora -
  regravar o firmware real (`pio run -t upload` em
  `firmware/hardware_core/`) depois do conserto fisico.

## 2026-07-18 (noite - Fofao na TV e PRIMEIRA CONVERSA DE VOZ COMPLETA!)

- **Avatar na TV via HDMI do Notebook:** TV detectada como segundo monitor
  (HDMI-2, 1920x1080). `xrandr --output HDMI-2 --primary` + relancamento
  do Firefox kiosk (com `setsid -f`, senao o processo morre junto com a
  sessao SSH) colocou o avatar em tela cheia na TV. Confirmado pelo usuario.
- **Descoberta que destravou a Fase 6: a TV e a saida de audio!** O chip
  HDA do notebook so expoe audio via HDMI (journal da Fase 6) - e agora ha
  uma TV no HDMI ("HDA Intel PCH: Android TV"). Teste com Piper + aplay
  confirmado pelo usuario: "a voz saiu". O headset USB planejado deixou de
  ser bloqueante para a saida.
- **Ferramenta nova `tools/conversar_fofao.py`:** substituto ao vivo do
  preview_avatar - AvatarServer + VoiceCore + AiManager (Ollama) +
  Sintetizador num processo so, mesmo Event Bus: o avatar do kiosk reage
  aos estados reais de voz. Sem Raspberry envolvido (EDR-0018).
- **Indices de audio reconferidos e atualizados em config/orion.yaml**
  (mudam quando USB troca de porta!): saida agora e a TV (indice 2);
  mics candidatos [0, 1, 5]. Seletor escolheu o mic da PC Camera (1).
- **Bug real achado e corrigido - offset DC na captura
  (`captura_audio.py`):** em silencio absoluto o RMS media ~0,25 (quase
  identico ao RMS falando!) - o mic entrega o sinal deslocado de um nivel
  constante. Isso inflava o RMS, enganava a selecao por qualidade e
  degradava o Whisper (janelas viravam '' ou alucinacao tipo "e acalpa-lo
  no inside"). Fix: subtrair a media do trecho gravado. Depois do fix,
  silencio mede rms 0,02-0,03 e fala ~0,16-0,23 - separacao limpa.
- **Wake word na pratica:** o Whisper ouve "Fofao" mas escreve "Fafao" ou
  "furacao" (e ate completa "carreta furacao" sozinho, contexto dos
  palhacos). `conversar_fofao.py` passa um DetectorPalavraAtivacao com as
  variacoes (fofao/fafao/fufao/furacao, com e sem til) - paliativo ate um
  modelo openWakeWord treinado para "Fofao" existir (decisao ja documentada
  em wake_word.py).
- **PRIMEIRA CONVERSA COMPLETA (criterio de pronto da Fase 6 - PARCIAL,
  ver abaixo):** usuario disse "Oi, fofao!" -> wake detectado -> comando
  transcrito -> Ollama respondeu -> Piper falou pela TV, avatar reagindo:
  "Ahah, sim! Sou um robo da marca Carreta Foracao, modelo Fofao..."
  (o proprio llama3.2 entrou na brincadeira da carreta).
- **183 testes passando** (6 de voz revalidados apos o fix do DC).
- **Pendencias:** (1) o criterio completo da Fase 6 inclui "acenda a
  lanterna" acionando LIGHT_ON - precisa do link Notebook->Raspberry->Mega
  de pe (Mission Planner completo); (2) trocar o orion-avatar.service para
  apontar para conversar_fofao.py se o usuario quiser a conversa no boot;
  (3) armadilha aprendida: pkill -f com padrao que aparece no proprio
  comando ssh mata a propria sessao (aconteceu 3x) - padrao seguro e
  "[c]olchete" no inicio E nenhuma ocorrencia literal no resto do comando.

## 2026-07-18 (madrugada - ajustes de conversa ao vivo com a familia testando)

- **"Oi? Pode falar!":** VoiceCore ganhou `frase_ativacao` opcional - sem
  uma confirmacao audivel o usuario nao sabia QUANDO falar e a janela de
  comando gravava silencio (visto ao vivo). Commit 98942ce.
- **Velocidade (queixa real: "demora muito pensando"):** resposta do
  Ollama limitada a 100 tokens + keep_alive de 30min (sem isso a primeira
  pergunta apos ocioso pagava o recarregamento inteiro do llama3.2).
  Commit d309cef.
- **Experimento fracassado, revertido no mesmo commit noturno:** Whisper
  "tiny" na vigia da wake word (para acelerar as janelas de 3s). Rapido,
  porem errava demais com este mic: "Fofão" virou "Loco! Loco!"/"Fofa no"
  e o robo nao acordava. Vigia voltou ao "base" via config
  (`whisper_model_ativacao`), a infra de dois transcritores fica pronta
  para um modelo wake word de verdade no futuro. Commits d309cef/50585ab.
- **Variacoes de wake word acumuladas ao vivo:** fafao, fufao, furacao,
  falfao (grafias reais que o Whisper produziu para "Fofão").
- **Kiosk da TV:** quando o servidor do avatar reinicia, a pagina fica
  "sem conexao" - relancar o firefox resolve (sem auto-reconexao no
  frontend ainda - candidato a melhoria no avatar_server/JS).
- **RAM do notebook monitorada durante a farra toda: ~3,8GiB disponiveis**
  com 2x Whisper base + Ollama + Firefox kiosk + avatar - folga boa.

## 2026-07-18 (fechamento - cerebro novo, wake word fuzzy e conversa validada pela familia)

- **Cerebro trocado: llama3.2:3b -> gemma3:4b** (pedido do usuario: "a
  outra IA estava melhor"). Portugues visivelmente superior, ~3,6GB
  residentes no Ollama (RAM do notebook fica com ~1,6-1,7GiB disponiveis
  - apertado mas estavel; alerta automatico armado em <700MiB durante a
  sessao). Respostas do gemma3 vem com emoji/markdown - `limpar_para_fala`
  em conversar_fofao.py tira antes do Piper. Commit e047c81.
- **Wake word virou fuzzy** (DetectorFuzzy em conversar_fofao.py):
  distancia de edicao <=2 de "fofao" (sem acentos) acorda o robo, alem
  das variacoes exatas (japao entra por lista, distancia 3). Lista fixa
  tinha virado enxugar gelo: cada teste ao vivo produzia grafia nova
  (fafao, falfao, japao, vovao...). Falso positivo conhecido e aceito:
  "botao". Commit 54067e9.
- **Servico de boot agora e o modo conversa:** orion-avatar.service roda
  conversar_fofao.py (nao mais o preview de eventos falsos), e o kiosk
  espera o servidor responder (curl em loop) antes de abrir o Firefox -
  fim da pagina de erro congelada quando o Firefox abria antes da hora.
  Commit 2a84e59. Licao: o "sem conexao" persistente era isso; o
  avatar.js sempre soube reconectar sozinho.
- **Corrida boba que confundiu o teste:** um `systemctl restart` meu caiu
  exatamente entre o "Voce disse" e a resposta - o usuario viu o robo
  "nao pensar nem responder". Diagnostico: nenhum crash, so o meu
  restart. Regra nova: nao reiniciar o servico com conversa em andamento.
- **Validacao final pela familia:** wake fuzzy acordou, comando garbled
  ("Quero que a presidenta camou!") e o gemma3 respondeu na hora
  "Desculpe, nao entendi. Pode repetir?" - em segundos (keep_alive
  pagando dividendos). Usuario: "excelente".
- **Proximos passos naturais:** (1) headset/caixa USB com mic melhor
  continua sendo o upgrade mais impactante para a transcricao dos
  comandos; (2) modelo openWakeWord treinado para "Fofao" aposentaria o
  fuzzy; (3) ligar o Mission Planner ao Motion Core para "acenda a
  lanterna" fechar o criterio completo da Fase 6.

## 2026-07-19 (teste ultrassom apos religar no 5V do Arduino)

- Usuario trocou a alimentacao dos HC-SR04: fonte externa 5V -> 5V do
  proprio Arduino. Teste com tools/testar_ultrassom.py (37 leituras):
  comunicacao ok, uptime continuo (sem brownout com a carga nova), mas
  **eco zero nos dois sensores** - `echo_*_ja_visto_alto` ficou "nao" o
  tempo todo, frontal e traseiro.
- Conclusao: o problema nao era a fonte. Sensor mudo total aponta para
  causa fisica: TRIG/ECHO trocados (sintoma exato), VCC/GND sem contato
  ou jumper dupont ruim. Proximo passo: conferir ordem VCC-Trig-Echo-GND
  no corpo do sensor e medir com multimetro (TRIG pulsando = ~0,1-0,8V
  medio oscilante, nao 5V fixo).
- Isolamento hardware x firmware: gravado sketch minimo descartavel
  (pulseIn bloqueante, scratchpad) no Mega. Resultado: SEM ECO nos dois
  sensores, pino ECHO parado em baixo. Firmware do ORION inocentado -
  defeito e fisico. Os dois mudos ao mesmo tempo sugere causa comum:
  linha de 5V/GND compartilhada (trilho de protoboard, jumper) ou os dois
  modulos queimados pela fonte externa anterior. Sketch de teste segue
  gravado no Mega durante a depuracao fisica; regravar firmware oficial
  (cd firmware/hardware_core && pio run -t upload) ao terminar.
- Escaner de pinos (sketch descartavel: pulsa trigger em cada pino 22..31
  e escuta todos os outros): achou sensor VIVO em TRIG=24/ECHO=25,
  medindo distancia estavel (~273cm) - sensor bom, so que fora da posicao
  planejada (22/23). Pino 22 esta preso em ALTO no repouso (100/100
  amostras) - comportamento tipico da linha de dados do DHT (idle alto
  por pull-up), nao de ultrassom; suspeita de DHT e ultrassom trocados de
  coluna no conector duplo. Segundo ultrassom nao respondeu em nenhum par
  22..31: trig dele provavelmente solto/fora dessa faixa, ou modulo morto.
  Licao: "5V chegando" + eco zero nao significa modulo queimado - conferir
  mapeamento real dos pinos por software antes de condenar hardware.
- Identificacao ao vivo: com a mao do usuario na frente do sensor FRONTAL,
  o par 24/25 caiu de ~273cm para 2-15cm e voltou ao tirar. Confirmado:
  frontal = vivo e saudavel, plugado uma coluna deslocado (24/25 em vez
  de 22/23). Pino 22 preso em alto = provavel fio do DHT (deveria estar
  no 24) - DHT e ultrassom frontal aparentemente trocados de coluna.
  Traseiro segue invisivel no escaner 22..53 (trig solto ou modulo sem
  5V/morto) - pendente inspecao fisica.
- Usuario reposicionou os fios (frontal 24/25 -> 22/23, DHT -> 24, e
  ajustou o traseiro): sketch minimo confirmou OS DOIS sensores medindo
  (frontal ~36cm estavel, traseiro reagindo a mao). O traseiro nunca
  esteve morto - era so posicao de fio tambem.
- Firmware oficial regravado no Mega. testar_ultrassom.py mostrava
  "eco desde boot: SIM" mas distancia "---": o script pedia
  distancia_*_cm ao RETURN_STATUS, que nunca teve esses campos - eles so
  existem na TELEMETRY (Cap 5). Corrigido o tool: assina
  comm.mensagem.telemetry no Event Bus (inicia bus.iniciar() em task) e
  imprime a distancia do ultimo quadro TELEMETRY; RETURN_STATUS segue
  para uptime (vigia de reset) e flags de diagnostico.
- Validacao final com firmware oficial: frontal ~37cm estavel, traseiro
  acompanhando a mao (3-250cm), flags SIM nos dois. pytest tests/unit:
  183 passed, 7 skipped. Pendente: remover os flags DIAGNOSTICO
  TEMPORARIO (echo_*_ja_visto_alto) do firmware quando nao forem mais
  uteis.

## 2026-07-19 (motores de passo giraram pela primeira vez)

- Usuario chateado: motores de passo nao mexiam. Diagnostico guiado com
  sketch descartavel (scratchpad/teste_motores): ciclo de 6 fases variando
  ENABLE (pino 6) e pulsando cada motor (1600 passos @ 400Hz) para separar
  polaridade de ENABLE x micropasso x fiacao.
- Causa raiz: PUL-/DIR- dos TB6600 nunca foram ligados ao GND do Mega -
  sem circuito de retorno, o optoacoplador nunca ve pulso. Na CNC antiga
  do usuario "so iam dois fios" (PUL+/DIR+) porque a placa fechava o
  retorno por baixo. ENA segue solto = sempre habilitado (igual CNC).
  Fonte de potencia 12-24V isolada pelo opto - nao precisa de GND comum.
- Apos ligar PUL-/DIR- ao GND do Mega: "GIROU" (usuario, ao vivo).
  Pendente anotar: quais fases giraram, voltas por rajada (revela o
  micropasso das chaves DIP) e se o ENABLE do firmware tem efeito real.
- Usuario confirmou: "ficou otimo" - motores girando. Firmware oficial
  regravado no Mega e validado (handshake ok, ultrassons medindo).
  Pendencias para a proxima sessao de bancada: (1) contar voltas por
  rajada para confirmar o micropasso das DIP e calibrar passos/metro no
  config; (2) decidir o destino do ENABLE (pino 6): esta solto nos
  drivers (= sempre habilitado), entao o firmware aciona um pino sem
  efeito - ou ligar ENA de verdade (economia de energia/aquecimento em
  idle) ou documentar que e ignorado; (3) teste de bancada completo do
  Cap 10 (MOVE_DISTANCE, TURN, STOP via protocolo).
- Decisao do usuario: ENA fica SOLTO (drivers sempre habilitados, como na
  CNC dele). Documentado em pins.h (fiacao real dos TB6600: PUL+/DIR+ nos
  pinos 2-5, PUL-/DIR- no GND do Mega, ENA nao conectado). Firmware
  recompilado ok - sem mudanca de comportamento, so documentacao.

## 2026-07-19 (MARCO: cadeia completa viva - Notebook -> Pi -> Arduino)

- Fase 7 iniciada pela integracao. Descoberta: python -m motion_core ja
  existia completo (memoria da sessao anterior estava desatualizada).
  Primeira execucao real no Pi: TCP 5757 ouvindo, Arduino confirmado via
  WHO_ARE_YOU no serial, webui 8080 no ar, SSD ausente tolerado.
- BUG REAL achado pela sonda de integracao (cliente minimo no Notebook):
  toda resposta do Arduino encaminhada pelo Pi ao Notebook era NACKada
  ("checksum invalido"). Causa: o firmware C++ nao reproduz a serializacao
  JSON canonica do Python; o link serial ignora o checksum de mensagem de
  proposito (confia no CRC16 do enquadramento), mas o roteador repassava o
  checksum original para o enlace TCP, que valida. Correcao: _encaminhar
  reassina o checksum ao rotear (service.py) + teste de regressao
  (test_roteamento_reassina_checksum_de_mensagem_do_firmware). 10/10 no
  test_service.py; fix sincronizado ao Notebook via scp.
- CADEIA COMPLETA VALIDADA: do Notebook, WHO_ARE_YOU ao Pi (ok),
  WHO_ARE_YOU ao Arduino atraves do Pi (ok), RETURN_STATUS pela cadeia
  inteira (ok). Bonus: estado veio OBSTACLE_DETECTED - a seguranca
  reativa do Mega (Cap 18 camada 1) esta ativa sozinha na bancada, com o
  ultrassom frontal vendo obstaculo proximo. imu_conectado=False (MPU
  ainda nao ligado fisicamente - esperado).
- Motion Core segue rodando em background no Pi (webui em
  http://10.20.20.185:8080).

## 2026-07-19 (VAD: escuta do Fofao ficou ~20x mais barata)

- Medicao no Notebook: load 4.0 cravado em 4 nucleos, conversar_fofao a
  165% de CPU continuo - o Whisper de vigilancia transcrevia TODA janela
  de 3s, 24h, mesmo silencio (e alucinava: "E ai? E ai?"). RAM ok
  (gemma3 descarrega quando ocioso).
- Implementado portao VAD por energia com piso de ruido adaptativo
  (src/orion/voice/vad.py, DetectorAtividadeSonora): percentil 20 do
  historico de RMS = piso; som so passa se > fator (2.5x) o piso, com
  rms_minimo absoluto 0.003. Config em voice.vad no orion.yaml (Cap 17).
  VoiceCore ganhou detector_atividade opcional; janela silenciosa nem
  chega ao Whisper. Testes: test_voice_vad.py (4) + regressao de custo no
  test_voice_core.py; 10/10 no Notebook.
- Resultado apos restart: 8.3% de CPU instantanea (era 165%), zero
  transcricoes em silencio (eram ~26/80s), load caindo (4.0 -> 2.9).
  Ruido ambiente da sala: RMS ~0.02 - piso adaptativo aprendeu sozinho.
- PENDENTE VALIDAR: usuario dizer "Fofao" ao vivo para confirmar que o
  portao nao deixou o robo surdo (se nao acordar: baixar
  fator_acima_do_ruido para ~1.8 no orion.yaml, ou desabilitar).
- Decisao de arquitetura mantida: nada migrou do Notebook para o Pi (IA
  nao cabe no Pi 4GB; mics/audio sao fisicos do Notebook; Cap 9/EDR-0019
  preservados) - em vez de mudar ONDE roda, baixamos QUANTO custa.
  openWakeWord treinado para "Fofao" segue como evolucao futura.

## 2026-07-19 (banco de dados vivo no SSD + primeira memoria do Fofao)

- Descoberta: o Pi ja BOOTA do SSD de 500GB (sda2 = raiz, 412G livres) -
  nao ha cartao SD; a exigencia do EDR-0019 (banco no SSD) e satisfeita
  criando /mnt/ssd/orion no proprio filesystem. Criados orion/ e backups/.
- Fio solto achado no "ligar tudo": a PonteMemoria (Fase 3, testada em
  test_memory_bridge.py) nunca era registrada pelo __main__.py do Motion
  Core - com o banco aberto, os comandos memory.* do Notebook morriam sem
  resposta. Corrigido: PonteMemoria(memory_api, comm).registrar(bus)
  quando o banco abre.
- Motion Core reiniciado: Migracao 1 aplicada, orion.db em WAL no SSD,
  ponte ativa. Validacao ponta a ponta do Notebook via TCP:
  memory.remember (id=1, conhecimento/primeiro_dia_de_vida) ->
  memory.recall (voltou identica) -> memory.stats (conhecimento: 1).
  A primeira memoria persistente da vida do Fofao.
- pytest: 184 passed, 8 skipped. __main__.py sincronizado ao Notebook.
- Motion Core virou servico permanente: tools/orion-motion.service
  (systemd --user, Restart=always) + loginctl enable-linger -> sobe no
  boot do Pi e renasce se cair; nao depende mais de sessao aberta.
  Verificado apos a troca: WHO_ARE_YOU no motion_core e no hardware_core
  (via serial) e memory.stats ok pelo TCP.
- Pegadinha anotada para melhorar: _ao_conectar_notebook registra QUALQUER
  cliente TCP como "mission_core" (nome fixo) - um cliente com outro
  nome_local nunca recebe resposta (rota inexistente) e o heartbeat vaza
  para ele. Melhorar junto com autenticacao de origem (Cap 14 s.9).
- Pegadinha de shell que custou um restart: pkill -f "a\|b" NAO e
  alternacao (ERE usa "|" sem escape) - o processo antigo sobreviveu
  segurando as portas 5757/8080 e o servico novo crashava em loop.

## 2026-07-19 (Mission Planner ligado - Fase 6 fechada na integracao)

- conversar_fofao.py agora e o Mission Core completo: conecta via TCP no
  Motion Core no boot (tolerando ausencia, Cap 6 s.8), monta o
  MissionPlanner com enviar_comando_hardware (comm.send ao hardware_core)
  e MemoryClient - predefinidos e hora resolvidos sem IA, conversa livre
  vai ao gemma3, interacoes registradas em "conversas" no banco do SSD.
- Teste de texto do criterio da Fase 6 (sem falar, do Notebook):
  "acenda a lanterna" -> LIGHT_ON ACKado pelo Mega real pela cadeia;
  "que horas sao" -> resposta direta; conversa livre -> IA; 4 linhas
  gravadas em conversas. O proprio teste achou 2 bugs de regex:
  "apague" (subjuntivo) caia na IA, e "desligue a luz" LIGARIA a
  lanterna ("ligue a luz" e substring; LIGHT_ON era testado primeiro).
  Corrigidos (\w* nas conjugacoes + OFF antes de ON) com 2 regressoes.
- Servico reiniciado no Notebook: "Motion Core conectado - comandos de
  hardware e memoria ATIVOS". 16/16 testes de planner+voz no Notebook.
- PLANO atualizado: Fase 6 "[~] QUASE" (falta so a validacao FALADA,
  aguardando mic USB sex 24/07); Fase 4 "[~] DESBLOQUEADO" (bancada
  parcial de 2026-07-19 registrada).

## 2026-07-19 (git remote Pi->Notebook - fim do scp artesanal)

- Antes de mexer: checksum de TODOS os arquivos rastreados nas duas
  maquinas. Divergencias eram uniformemente o Notebook desatualizado
  (rename "ORION X"->"Fofao" nunca sincronizado + mudancas de hoje) -
  nada exclusivo do Notebook a preservar. Ancestral comum confirmado
  (Notebook = commit raiz 819456c).
- Setup: remote "notebook" no Pi (jproma23@10.20.20.195:orion-os),
  push fast-forward 819456c..f726278, git reset --hard no Notebook e
  receive.denyCurrentBranch=updateInstead - de agora em diante,
  sincronizar = "git push notebook" no Pi (o worktree do Notebook
  atualiza sozinho se estiver limpo). Historico do Notebook saiu da
  Fase 0 e agora espelha o Pi.
- Faxina: tests/unit/conftest.py orfao no Notebook (duplicata antiga do
  FakeTransporte de tests/conftest.py) removido; suite completa re-rodada
  no Notebook apos o reset.

## 2026-07-19 (faxina do firmware - flags de diagnostico removidos)

- Removidos os DIAGNOSTICO TEMPORARIO echo_*_ja_visto_alto de
  sensor_ultrassonico.h, radar_manager.h e do RETURN_STATUS (main.cpp) -
  cumpriram a missao na cacada dos fios. testar_ultrassom.py atualizado
  (sem os flags; nota nova: parar orion-motion.service antes de rodar,
  senao a serial esta ocupada - com o servico de pe, conferir distancias
  em http://<pi>:8080/estado).
- Mega regravado (servico pausado durante o upload e religado).
  Verificacao pela cadeia TCP: RETURN_STATUS sem os flags; webui mostrando
  distancia_frontal_cm ~15 e OBSTACLE_DETECTED coerente com a bancada.
  Detalhe de arquitetura confirmado de passagem: TELEMETRY e endereada ao
  motion_core e consumida la (nao e repassada ao mission_core) - clientes
  TCP nao a veem; a webui e o retrato dela.

## 2026-07-19 (Fase 7 conferida e validada - navegacao pronta menos autocalibracao)

- Levantamento: NavigationCore (HOLD/MANUAL/GOTO/PATROL completos, FOLLOW
  minimo deliberado) e FusaoSensores ja existiam com 27 testes unitarios +
  8 de integracao (sim) - todos verdes no Pi.
- Validacao ao vivo do fluxo de missao SEM movimento: do Notebook,
  comm.publish("navigation.comando", MANUAL/STOP) -> modo MANUAL na webui
  (STOP entregue ao Mega); depois HOLD -> modo HOLD. Cadeia de missao
  Notebook->Pi->Arduino operante.
- PLANO Fase 7 atualizado: 4 de 5 entregaveis concluidos; pendencias
  fisicas: autocalibracao (Cap 12 s.9), encoders/IMU nao montados, FOLLOW
  completo quando a visao rodar junto ao vivo.

## 2026-07-19 (Behavior Core + resiliencia do link Notebook<->Pi)

- EDR-0020: Behavior Core ("maestro"/consciencia) - arbitragem de
  prioridade sobre o Event Bus, roda no Pi (revisao: no estavel sobrevive
  a crash do Notebook). NAO adota ROS (so multitarefa exige coordenacao,
  nao middleware; ROS futuro so p/ SLAM). Esqueleto BehaviorCore +
  Comportamento + 3 testes.
- Guardiao de RAM do Notebook (behavior/guardiao_ram.py): Notebook publica
  diagnostic.notebook_health (/proc/meminfo) a cada 10s; guardiao no Pi
  pede behavior.reduzir_carga_ia + alerta abaixo do limiar, com histerese.
  3 testes.
- Arduino port-scan: CH340 re-enumerou p/ ttyUSB1 e o servico (so ttyUSB0)
  falhava; _conectar_arduino agora varre ttyUSB0/1/ACM0. Reconectou.
- BUG FOUNDATIONAL consertado: o Notebook NAO reconectava ao Pi apos um
  restart do Pi - ficava orfao p/ sempre (reporter so imprimia "Falha ao
  propagar"). Novo supervisor de link em conversar_fofao.py: conecta no
  inicio e reconecta em comm.module_lost (retenta 5s). Comprovado ao vivo:
  restart do Pi -> "Link caiu -> reconectando" -> reconectado em 2s.
- So DEPOIS disso o guardiao de RAM disparou ponta a ponta ("RAM do
  Notebook critica: 5110 MB < 5608"). Era o link fragil que mascarava.
- Licao: verificar features de rede ao vivo exige o link resiliente
  primeiro; meus restarts do Pi orfanavam o Notebook e davam falso
  negativo no guardiao.

## 2026-07-19 (primeiros comportamentos plugados no maestro)

- Comportamento ganhou gancho _maestro/_reavaliar (o BehaviorCore o
  preenche em registrar) para a subclasse acordar o maestro quando o
  gatilho muda.
- Dois comportamentos concretos (comportamentos.py): Repouso (prio 10,
  base) e VigilanciaObstaculo (prio 100, dispara com motion.status ==
  OBSTACLE_DETECTED, sinal real do Mega ja no Event Bus do Pi). 2 testes
  (repouso assume sem obstaculo; obstaculo preempta e libera).
- Maestro ligado no motion_core/__main__.py e ativo no robo: ao subir,
  assumiu 'repouso' (hardware IDLE). Preempcao por obstaculo coberta por
  teste; ao vivo reage quando o Mega reportar. Notebook reconectou
  sozinho apos o restart do Pi (fix da reconexao confirmado de novo).

## 2026-07-19 (Atender plugado - maestro reage ao "Fofão")

- Atender (prio 80) no maestro: dono chama "Fofão" -> robo para (HOLD) e
  atende ate a resposta terminar. Notebook encaminha voice.wake_detected
  e voice.response_finished ao Pi (comm.publish local=False, novo, evita
  eco). 3 testes: atender preempta repouso + HOLD; obstaculo (100) vence
  atender (80). Maestro no ar com repouso+atender+vigilancia; assumiu
  repouso no boot (hardware IDLE). Prova falada (dizer "Fofão") fica para
  o usuario testar no robo. Nota: journalctl --user segue flaky ("No
  journal files"); usar `systemctl --user status -n N` para ver o log.

## 2026-07-19 (família cadastrada - Fofão conhece os donos)

- Cadastrados os 3 moradores por foto (tools/cadastrar_de_fotos.py, fotos
  baixadas no Pi -> scp pro Notebook -> face_recognition no Notebook ->
  memory.remember via TCP com embedding base64): João Paulo (dono, id=1,
  2 fotos), Ana (dono, id=2, 4 fotos), Bruno (morador, id=3, 3 fotos; 1
  foto sem rosto detectavel, ignorada). Embeddings de 1024 bytes (128
  float64) confirmados na tabela pessoas do SSD. A ponte binaria base64 do
  bridge.py funcionou ponta a ponta.
- Gmail: conector achou o rascunho "fotos" mas NAO baixa anexo (sem
  permissao + sem get_attachment) - plano B (fotos direto no Pi) resolveu.
- face_recognition NAO esta no Pi (so no Notebook, Cap 8) - fotos precisam
  passar pelo Notebook para virar embedding.
- Base do Modo Sentinela pronta: rosto que nao casar com os 3 = estranho.

## 2026-07-19 (Sentinela de visão - rosto desconhecido dispara alerta)

- Transporte binario de recall (a metade que faltava): bridge
  _codificar_binarios embrulha BLOB em base64 no resultado; MemoryClient
  _decodificar_binarios desembrulha. O Notebook agora LE os embeddings da
  familia do banco. Round-trip testado.
- SentinelaVisao (src/orion/vision/sentinela_visao.py, roda no Notebook):
  carrega os conhecidos, olha um frame a cada intervalo_s; rosto que nao
  casa -> salva foto (data/sentinela) + sentinela.alerta {tipo:pessoa} ->
  encaminhado ao Pi -> Vigilia (maestro) assume. Config
  behavior.sentinela_visao. Ligado no conversar_fofao, tolera camera/link
  ausentes. Ao vivo: "3 rostos conhecidos carregados" + "Sentinela de
  visao ativa". Prova do estranho fica para o usuario (por alguem de fora
  na frente da camera).
- Detector de barulho (VAD) NAO plugado ainda: RMS sozinho nao separa
  barulho de fala (dispararia em conversa/TV) e o VoiceCore ja e dono do
  mic. So faz sentido em "modo ausente" - decisao pendente do usuario.

## 2026-07-19 (Teste do MPU6050 - sensor ok, offset de 9.3 graus)

- **Ferramenta nova:** `tools/testar_imu.py` - monitor ao vivo do MPU6050,
  mesmo molde do testar_ultrassom.py: inclinacao/impacto vem da TELEMETRY
  (`comm.mensagem.telemetry`), RETURN_STATUS so serve para o uptime (vigia
  de reset). Firmware ja publicava tudo (telemetry_manager.h), nao precisou
  mexer no Arduino.
- Rodado no Mega real (motion.service parado): handshake ok
  (0.1.0-fase2), 37 leituras em 25s, `imu_conectado` true, zero reset,
  zero falha de I2C. **I2C nos pinos 20/21 confirmado funcionando.**
- **Achado:** parado, a inclinacao le 9.3 graus constante com ruido de so
  +-0.5 grau. Ruido baixo = sensor saudavel; os 9.3 sao offset fixo
  (chassi torto ou MPU colado torto). Como LIMITE_INCLINACAO_GRAUS = 20,
  o robo trava com ~11 graus reais de um lado e aguenta ~29 do outro -
  assimetria a corrigir (calibrar zero no iniciar() ou reposicionar o
  modulo). Falta ainda validar o flag de impacto (tapinha no chassi).
- Nota de execucao: rodar os tools com `.venv/bin/python -u` - sem o `-u`
  o stdout fica preso no buffer quando a saida e canalizada.
- **Correcao do offset (mesma sessao):** decidido com o usuario que ele
  vai colar o MPU reto e o firmware zera os vetores em cima da posicao
  boa. Implementado em `imu_manager.h`: em vez de subtrair um escalar
  (errado - a inclinacao e um angulo sem direcao, subtrair furaria pro
  lado contrario), guarda o VETOR de gravidade lido nivelado e mede o
  angulo entre a leitura atual e essa referencia (produto escalar +
  acos). Referencia default (0,0,1) = comportamento antigo.
- Calibracao persiste na EEPROM (endereco 0, magico 0x0110, com sanidade
  de norma contra EEPROM corrompida), entao nao precisa regravar firmware
  se o modulo sair do lugar. Comando novo `CALIBRATE_IMU` (main.cpp) e
  flag `imu_calibrado` na TELEMETRY e no RETURN_STATUS.
- `tools/testar_imu.py --calibrar` dispara a calibracao e sai.
- **Efeito colateral util:** depois de calibrado, leitura longe de 0 com o
  robo nivelado = o modulo se mexeu. Diagnostico de graca (ideia do usuario).
- Compilado e GRAVADO no Mega: RAM 33.4%, flash 16.2%. Falta o usuario
  colar o modulo reto e rodar o --calibrar.
- Nota de ambiente: o PlatformIO NAO esta no PATH nem no .venv - fica em
  `/home/jproma23/.platformio-venv/bin/pio`.
- **CALIBRADO ao vivo:** usuario colou o modulo, rodado `--calibrar`, EEPROM
  gravada. Verificacao logo depois: **9.3 graus -> 0.3 graus**, ruido de
  +-0.2 grau. Zero no lugar, limite de 20 graus agora simetrico nos dois
  lados. Servico orion-motion religado.
- **PENDENCIA aberta:** durante a verificacao (20s, ~14 leituras) UMA
  resposta de RETURN_STATUS se perdeu (timeout de 3s). Nao foi reset - o
  uptime seguiu subindo (4463ms -> 8584ms), so a resposta sumiu. Suspeita:
  mesmo ACK perdido de 2026-07-18 que motivou SERIAL_*_BUFFER_SIZE=256 no
  platformio.ini - os buffers reduziram mas nao eliminaram. Investigar
  antes da Fase 4 fechar; frequencia observada ~1 em 14.
- Flag de impacto ainda NAO validado (precisa de tapinha no chassi).
- **Recalibrado:** usuario mexeu no modulo durante o teste de tapa (era a
  explicacao dos 6.8 graus - o diagnostico "de graca" funcionou na
  pratica, primeira vez que ele pegou um deslocamento real). Ajeitado e
  recalibrado: de volta a 0.4 grau.
- **Bug encontrado e corrigido - flag de impacto invisivel:** o teste de
  tapa deu ZERO deteccoes. Causa nao era o tapa: `_impactoDetectado` era
  INSTANTANEO. Tranco dura ~10-20ms, IMU e lido a cada 50ms e a TELEMETRY
  so sai a cada 500ms - o evento quase sempre morria entre amostras.
  Enfraquecia tambem o SafetyManager.
- Correcao: janela de tempo (`JANELA_IMPACTO_MS = 1000`, maior que os
  500ms da telemetria) em vez de "zerar na leitura". Zerar-na-leitura foi
  DESCARTADO de proposito: SafetyManager le todo loop e telemetria a cada
  500ms - quem lesse primeiro roubaria o evento do outro e a telemetria
  voltaria a nao ver nada. A janela apaga sozinha, sem disputa.
- Regravado e testado ao vivo: impacto apareceu como SIM em 2 quadros
  seguidos (a janela de 1s cobrindo dois quadros de 500ms, exatamente o
  desenhado). **Flag de impacto validado.**
- Confirmado tambem que a calibracao da EEPROM SOBREVIVE a regravacao do
  firmware (leituras seguiram em 0.3-0.6 grau depois do upload).
- **PENDENCIA nova:** so 1 evento detectado em ~3-4 tapas. O mecanismo
  agora funciona, mas o limiar de 2.5 G parece alto para tapa de mao.
  Baixar exige cuidado: 2.5 G dispara parada de emergencia, e limiar baixo
  demais gera falso positivo passando por soleira/desnivel. Calibrar com o
  robo andando de verdade, nao na bancada.

## 2026-07-19 (CAUSA RAIZ das respostas perdidas: o DHT fantasma)

- Investigada a pendencia da resposta perdida. **Causa raiz achada e
  corrigida** - e nao era o que o platformio.ini dizia.
- **O mecanismo:** a biblioteca Adafruit DHT faz bit-banging no pino COM
  AS INTERRUPCOES DESLIGADAS. Com elas desligadas a ISR que enche o buffer
  serial nao roda; a 115200 baud chegam ~57 bytes em 5ms e o hardware do
  ATmega so segura 2-3. O resto se perde e corrompe o quadro em transito.
  **Buffer maior nao ajuda** - o problema e a ISR parada, nao o tamanho.
  Isso explica por que SERIAL_*_BUFFER_SIZE=256 (18/07) reduziu mas nunca
  eliminou o ACK perdido: tratamos sintoma, nao causa.
- **Agravante:** o DHT NAO ESTA INSTALADO (usuario confirmou). Sensor
  ausente e PIOR que presente - em vez de responder em ~5ms, a leitura
  gasta o timeout inteiro esperando um pino que nunca muda.
- **Como foi provado (metodo vale registrar):** as falhas pareciam
  aleatorias. Script probe_dht.py rodou 200 requisicoes e imprimiu o
  uptime de cada falha MODULO 5000ms (o intervalo do DHT). Deu 4757, 4763
  e 4760 - 6ms de espalhamento. Aleatorio nao faz isso. Hipotese confirmada.
- **Correcao:** chave `ORION_DHT_INSTALADO` (dht_manager.h), em 0 enquanto
  o sensor nao existe - nao encosta no pino. Quando instalar, trocar para
  1 e regravar. Fica tambem uma auto-deteccao (3 falhas seguidas ->
  `_ausente`, retentativa a cada 60s) para o caso do sensor cair depois.
- **Resultado medido:** antes 3 falhas/200 (1,5%). Depois **0 falhas/200**.
  Teste longo de 3000 requisicoes em andamento.
- Impacto real disso: ~1,5% de TODO comando se perdia silenciosamente,
  incluindo STOP. Nao era ruido estatistico, era o freio falhando 1 em 66.

## 2026-07-19 (Bateria + integracao cognitiva: modelo de mundo)

- **Sensores termicos aproveitados (`src/orion/diagnostics/sensores_termicos.py`):**
  le /sys/class/thermal, existe no Pi e no Notebook, sem dependencia nova.
  Medido: Pi cpu-thermal 66,7 C | Notebook x86_pkg_temp 50,0 / pch 46,0 /
  acpitz 27,8 C. Cobre o item "temperatura da CPU/SoC" do Cap 16.
  **NAO substitui o DHT** - esses sensores medem o proprio chip, nao o
  ambiente, e nenhum mede umidade. Sao complementares.
- **Notebook NAO TEM BATERIA:** /sys/class/power_supply so lista ADP1 (a
  fonte, online=1); upower nao enumera nenhum device de bateria. O plano
  de ler saude da bateria pelo notebook nao tinha como funcionar.
- **Alimentacao definida pelo usuario:** pack de parafusadeira 18V (max)
  alimenta motores e notebook; buck reduz para 5V para Pi e sensores. Ele
  ja tem os modulos buck e buck-booster. (Alertei sobre o notebook em
  barramento nao regulado; ele disse que os modulos resolvem.)
- **Bateria no firmware (`bateria_manager.h`):** divisor 100k/27k em A0.
  18V -> 3,83V no pino; 21V -> 4,46V (ainda seguro); consumo 0,14mA.
  Limiares 5S Li-ion: aviso 16,5V (3,3V/celula), critica 15,0V (3,0V).
  Media de 8 amostras. analogRead NAO desliga interrupcoes (ao contrario
  do DHT), entao pode rodar no loop sem risco para a serial.
  Chave `ORION_BATERIA_INSTALADA = 0` enquanto o divisor nao existe -
  mesma licao do DHT: pino solto flutua e reportaria tensao inventada.
  **ATENCAO na montagem:** exige GND comum Mega<->bateria (os motores sao
  opto-isolados e nao precisavam; a medicao de tensao precisa).
- Campos novos na TELEMETRY: bateria_lida / bateria_tensao_v /
  bateria_percent / bateria_nivel. Firmware gravado no Mega.
- **Integracao cognitiva - usuario quer as 3 camadas** (modelo de mundo,
  memoria na decisao, IA decidindo). Elas se empilham; comecei pela base.
- **Modelo de mundo (`motion_core/behavior/estado_do_mundo.py`):** funde
  telemetria, estado do hardware, visao, sentinela e voz num RetratoDoMundo
  unico que os comportamentos consultam, em vez de cada um assinar seus
  proprios eventos e ver so um pedaco.
  **Decisao de projeto central:** todo dado tem PRAZO DE VALIDADE. Vencido
  vira None ("nao sei"), nunca continua sendo servido como atual - senao,
  com o cabo solto, o quadro diria "obstaculo a 34cm, tudo ok" com dado de
  5 minutos atras e o maestro decidiria em cima de ficcao. Respeita tambem
  os flags *_valida do firmware.
- 12 testes novos (tests/unit/test_estado_do_mundo.py), com foco no
  envelhecimento. Suite completa: 208 passed, 8 skipped.
- **PENDENCIA:** teste longo anterior (3000 req) nao concluiu - o timeout
  cortou antes do resumo. Zero falhas impressas em ~15min, mas sem numero
  final. Refazendo sobre o firmware definitivo com progresso visivel.

## 2026-07-19 (Serial 3000/3000; comparacao de modelos de IA)

- **Teste longo concluido: 3000 requisicoes, 3000 ok, ZERO falhas**, sobre
  o firmware definitivo. Antes da correcao do DHT: 1,5%. Causa raiz das
  respostas perdidas esta enterrada.
- **Comparacao de modelos (bench com prompts reais do Fofao):**
  | modelo | RAM | conversa | decisao | alucinou? |
  |---|---|---|---|---|
  | gemma3:1b | 880 MB | 10,5s | 10,6s | SIM - "vi a Ana passar" |
  | llama3.2:3b | 2,6 GB | 34,5s | 25,6s | SIM (pior) - inventou registro as 14h30 |
  | gemma3:4b | 2,9 GB | 20,2s | 51,5s | NAO - disse que nao teve noticia |
- **Contraria a percepcao inicial:** o gemma3:4b foi o mais RAPIDO na
  conversa e o UNICO honesto. O llama3.2:3b ("o que funcionava") foi o
  mais lento e o que mais inventou. O 1b tambem errou o nome do
  comportamento ("vigilancia" em vez de "vigilia").
- **Quantizacao: nao ha ganho a buscar.** Os tres ja sao Q4_K_M (padrao do
  ollama). Ja estamos em 4-bit.
- **Diagnostico de fundo:** a alucinacao NAO e problema de modelo. O prompt
  do bench perguntava da Ana sem dar dado nenhum sobre ela - sem
  informacao qualquer modelo chuta. Conserto e grounding (entregar os
  fatos + instruir a dizer "nao sei"), nao troca de modelo.
- Decisao com o usuario: construir o CONTEXTO primeiro, remedir os modelos
  depois - com os fatos na mao a tarefa muda e os numeros de hoje (feitos
  sem contexto) deixam de valer. qwen2.5:3b baixado para entrar no
  proximo comparativo.
- **PENDENCIA (achado lateral):** ninguem assina `behavior.reduzir_carga_ia`.
  O GuardiaoRamNotebook publica o pedido de alivio e o evento morre - so
  os testes escutam. O guardiao nao protege nada hoje.

## 2026-07-19 (Grounding: a correcao real da alucinacao + troca para gemma3:1b)

- **`src/orion/mission/grounding.py`:** transforma o que o robo sabe num
  bloco de fatos para o prompt. A ideia central nao e listar o que se sabe,
  e **dizer em voz alta o que NAO se sabe** - campo ausente vira
  "nao sei (sem registro)", e lista de observacoes vazia vira "nao tenho
  NENHUM registro hoje... devo dizer isso". Silencio convida invencao.
- **Resultado medido - mesma pergunta que fez todos mentirem:**
  | modelo | SEM contexto | COM contexto | tempo |
  |---|---|---|---|
  | gemma3:1b | "Sim, vi! A Ana passou" | "Nao vi." | 16,5s |
  | llama3.2:3b | inventou registro "as 14h30" | "Nao vi nada, Joao Paulo!" | 41,6s |
  | qwen2.5:3b | (nao testado sem) | "Nao vejo registros de ninguem hoje." | 43,2s |
  | gemma3:4b | "nao tive noticia" (ok) | "Nao vi, Joao Paulo." | 48,8s |
  **Os QUATRO pararam de inventar.** Confirma: nunca foi problema de
  modelo, era falta de contexto (o usuario tinha razao).
- **TROCA DE MODELO: gemma3:4b -> gemma3:1b.** So virou possivel por causa
  do grounding: com os fatos prontos a IA nao deduz, so le e formula.
  Ganho: 880MB em vez de 2,9GB (RAM do notebook e apertada) e o mais
  rapido dos quatro com contexto (16,5s contra 48,8s do 4b).
  Config comentada com o historico e com o aviso: **se voltar a inventar,
  o suspeito e o grounding ter parado de entregar os fatos, nao o modelo.**
- `config/prompt_sistema.txt` ganhou a regra anti-invencao explicita.
- `AiManager._montar_prompt_sistema` agora usa o grounding e injeta o bloco
  SEMPRE - inclusive vazio, porque contexto vazio ("nao tenho registro de
  nada") e justamente o que impede a invencao.
- 11 testes novos (tests/unit/test_grounding.py). Suite: 219 passed, 8 skipped.
- **RESSALVA:** validado em UM caso (recusar o que nao sabe). Falta testar
  o caso oposto - quando ele TEM o dado e precisa usa-lo direito. O 1b
  errou o nome de comportamento antes ("vigilancia" em vez de "vigilia"),
  entao a camada 3 (IA decidindo) precisa de validacao de saida.

## 2026-07-19 (Camada 3: IA aconselha, regra manda)

- **Saida estruturada testada no gemma3:1b** (ollama 0.32.1 suporta schema
  JSON com `enum`): 4/4 respostas com nome de comportamento VALIDO. O erro
  "vigilancia" (que nao existe) acabou - a gramatica nao permite gerar
  valor fora da lista.
- **MAS o teste revelou coisa pior: as ESCOLHAS estao erradas.** O 1b
  escolheu `repouso` em 3 de 4 situacoes:
  | situacao | escolheu | correto |
  |---|---|---|
  | obstaculo a 34cm | repouso | vigilancia_obstaculo |
  | rosto desconhecido 03:00 | vigilancia_obstaculo | vigilia |
  | Ana chamou "Fofao" | repouso | atender |
  | tudo calmo | repouso | ok |
  **Schema garante resposta valida, nao resposta certa.**
- **Consequencia de projeto:** a IA virou CONSELHEIRA, nao decisora
  (`src/orion/mission/conselheiro_comportamento.py`):
  - seguranca ativa -> a IA NEM E CONSULTADA (regra determinística vence);
  - `COMPORTAMENTOS_DE_SEGURANCA` nunca entram na lista oferecida a ela -
    entrar em seguranca e condicao fisica medida, nao opiniao;
  - toda resposta e validada de novo em Python (cinto e suspensorio, caso
    o schema falhe);
  - timeout de 8s: conselho atrasado nao serve, o maestro nao espera.
  No pior caso (IA burra/lenta/fora do ar) o robo se comporta exatamente
  como se comportava sem ela.
- Import de `ollama` ficou preguicoso (dentro do __init__): a lib so existe
  no Notebook, e o modulo precisa ser importavel no Pi e nos testes.
- 8 testes novos, todos sobre "a IA nao pode atrapalhar". Suite: 227 passed.
- **Modelos:** removidos llama3.2:3b e qwen2.5:3b (superados). MANTIDO o
  gemma3:4b - com o julgamento fraco do 1b, ele e o candidato natural para
  a camada de decisao. Disco tem 176GB livres, nao ha pressao.
- **Quantizar o 4b: NAO.** Ja e Q4_K_M. Descer para Q3/Q2 exige gerar com
  llama.cpp, economiza ~1GB de disco que sobra, e degrada justamente a
  qualidade de julgamento que e o unico motivo de manter o 4b. Modelo
  parado tambem nao ocupa RAM - so o carregado ocupa.
- **PENDENCIA:** conselheiro criado e testado, mas ainda NAO plugado no
  BehaviorCore. Falta a ponte Pi<->Notebook (o maestro roda no Pi, a IA no
  Notebook) e decidir em que situacoes vale consultar.

## 2026-07-19 (Conselheiro plugado no maestro - a IA opina, a regra manda)

- **Problema descoberto ao plugar:** os 4 comportamentos existentes sao
  todos disparados por condicao concreta (voz, alerta, obstaculo). Quando
  nenhum quer rodar sobra so `repouso` - pedir conselho com UMA opcao e
  decoracao. Conselho so significa algo se houver escolha real.
- **`Ronda` (prio 20), primeiro comportamento DISCRICIONARIO:** dar uma
  olhada em volta quando nao ha nada acontecendo. So olha (pan/tilt +
  SCAN_FRONT), **NAO anda** - movimento de rodas por iniciativa da IA seria
  arriscado demais no primeiro passo; olhar e reversivel.
- **Ponte Pi<->Notebook** (`motion_core/behavior/ponte_conselho.py` +
  `src/orion/mission/conselho_protocolo.py`): pedido e resposta casados por
  `id`, senao resposta atrasada de pedido antigo seria confundida com a
  atual. Timeout de 6s; None significa "decida pela regra", nao erro.
- **Correcao de arquitetura no meio do caminho:** eu tinha posto o
  atendente em `motion_core/` e feito o Notebook importar de la. Errado -
  eles tem deploy separado e a dependencia so anda num sentido
  (`motion_core` importa `orion`). Protocolo + atendente mudaram para
  `src/orion/mission/conselho_protocolo.py`.
- **Gancho no maestro** (`_pode_consultar_ia`): a IA so e consultada na
  AMBIGUIDADE - ninguem de gatilho concreto no controle e nenhum
  discricionario ja querendo rodar. Resposta que nao seja o nome exato de
  um discricionario registrado e ignorada (inclui "repouso", que e um
  conselho legitimo de nao fazer nada).
- 9 testes novos, todos sobre "a IA nao pode atrapalhar": Notebook mudo,
  lento, explodindo, resposta invalida, resposta atrasada de pedido velho,
  e maestro sem ponte nenhuma. Suite: **236 passed, 8 skipped**.
- **VALIDADO NO SERVICO REAL:** orion-motion reiniciado, log mostra
  `comportamentos: vigilancia_obstaculo, atender, vigilia, ronda, repouso`
  e depois `sem conselho em 6.0s - seguindo pela regra` (o Notebook nao
  esta rodando conversar_fofao). Degradacao graciosa confirmada em campo,
  nao so em teste.
- Limpeza: a lista de comportamentos no log era escrita a mao e ja estava
  desatualizada; agora vem de `maestro.nomes_registrados`.
- **PENDENCIA:** falta ver o conselho ACEITO ao vivo - precisa do
  conversar_fofao rodando no Notebook ao mesmo tempo. E o julgamento do
  gemma3:1b e fraco (escolheu repouso com obstaculo a 34cm), entao vale
  medir se ele acerta a escolha ronda-vs-repouso ou se o 4b e necessario
  aqui.

## 2026-07-19 (Teste ao vivo do conselheiro: DESLIGADO, e por que)

Sequencia de falhas encontradas ao ligar o conselheiro ao vivo, na ordem:

1. **Bug meu:** em conversar_fofao.py escrevi `event_bus` onde a variavel
   local se chama `bus`. O processo subia mudo e nunca conectava no Pi.
2. **Evento nao atravessa o link sozinho** (Cap 14 s.7): quem quer mandar
   um evento ao outro no precisa reencaminhar com `comm.publish(...,
   local=False)`. Faltava nos dois lados - o Pi perguntava no vazio.
   Adicionado: Pi repassa TOPICO_PEDIDO, Notebook repassa TOPICO_RESPOSTA.
3. **Tempestade de requisicoes (causada por mim):** o maestro repergunta a
   cada timeout (6s) e a inferencia levava >8s. Pedidos empilhavam mais
   rapido que as respostas; o link TCP caia e reconectava em laco, com
   **37 conexoes vazadas** na porta 5757. Corrigido com
   `INTERVALO_CONSULTA_IA_S = 120` (ociosidade nao e urgencia).
4. **Timeouts invertidos:** Pi esperava 6s, conselheiro do Notebook 8s - o
   Pi desistia antes de a resposta poder existir. Agora Pi 25s /
   conselheiro 20s (o de fora tem que ser maior que o de dentro).
5. **A causa que nao tem conserto por parametro:** cada consulta derrubava
   o link. Padrao no log: heartbeat perdido -> IA estoura o timeout ->
   "Link com o Motion Core caiu". A inferencia do gemma3:1b satura os
   nucleos do notebook por ~20s; o processo Python e preterido, os
   heartbeats atrasam e o Pi declara o link morto. **asyncio.to_thread
   protege o event loop de BLOQUEAR, mas nao de FALTA DE CPU.**

- **DECISAO: conselheiro DESLIGADO por padrao** (`behavior.conselho_ia.
  habilitado: false`). Entregava zero conselhos (a inferencia estourava o
  timeout) e custava uma queda de link a cada 2 minutos - pior que nao ter.
  Codigo pronto e testado (236 passed); religar e trocar a flag, depois de
  resolver a causa. Verificado apos desligar: log em silencio, link estavel.
- **Heartbeat e o no do problema:** intervalo 1s, limite 3 perdidos = link
  morto em 3s. Tolerancia curta demais para uma maquina que roda inferencia
  local. Subir exige config POR LINK - tolerante com o Notebook, rigida com
  o Mega (onde detectar falha rapido importa de verdade). Trabalho real,
  nao troca de numero.
- **Proposta do usuario (mover visao e voz para o Pi, notebook so com IA):
  NAO recomendada.** O problema nao e onde as coisas rodam, e CPU saturada
  convivendo com laco sensivel a atraso. Mover para o Pi faz saturar a
  maquina MAIS FRACA (4GB vs 8GB) e MAIS CRITICA - a que fala com o Arduino
  e roda a seguranca tatica. Notebook engasgado e chato; Pi engasgado e o
  robo perdendo o corpo. O caminho e o inverso: o forte fica com o peso, o
  Pi passa a TOLERAR o notebook engasgar.
- **PENDENCIA (achado lateral, importante):** vazamento de conexoes TCP -
  a reconexao do Notebook abre socket novo sem fechar o antigo (37 vivas no
  pico, 4 depois de estabilizar). Investigar o supervisor de link.

## 2026-07-19 (Vazamento de conexoes TCP: causa e conserto)

- **Duas fugas, nao uma.** `ComunicacaoService.adicionar_link` so
  sobrescrevia `self._links[nome_peer]`:
  1. o transporte antigo NUNCA era fechado -> socket vazado;
  2. `_tarefas_recepcao` era uma LISTA e so crescia -> a tarefa antiga
     seguia rodando sobre o socket velho.
- **Por que vazava de verdade (e nao so em teoria):** o link e declarado
  morto por HEARTBEAT ATRASADO, nao por socket fechado. Quando o Notebook
  engasgava de CPU (inferencia da IA), o socket continuava perfeitamente
  vivo - cada "reconexao" deixava mais uma conexao ESTABLISHED para tras.
  O comentario no conversar_fofao.py afirmava que o antigo era
  "descartado"; era descartado do dicionario, nunca fechado. Comentario
  corrigido para nao mentir de novo.
- **Conserto:** `_tarefas_recepcao` virou dict por peer e
  `_descartar_link_anterior()` cancela a tarefa e fecha o transporte antes
  de registrar o novo. Fechamento vai em tarefa propria porque
  `adicionar_link` e sincrono e `fechar()` e assincrono.
- **4 testes de regressao** (tests/unit/test_service_reconexao.py), com
  transporte falso que e GERADOR assincrono (como os de verdade). Validados
  dos dois lados: reintroduzi o bug de proposito, os 4 falharam; restaurei,
  os 4 passaram. Teste de regressao que nao falha sem o conserto nao vale.
  Um deles cobre o risco de o conserto ser agressivo demais: fechar o link
  antigo de um peer NAO pode derrubar os outros (Arduino intacto).
- **Provado em campo:** 5 ciclos de restart do Motion Core forcando
  reconexao - contagem de sockets oscilou entre 0 e 1, sem acumular
  (antes: 37 no pico). Log do Notebook agora mostra "Link anterior com
  'motion_core' descartado (socket e tarefa fechados)" a cada reconexao.
- Suite: **240 passed, 8 skipped**.

## 2026-07-19 (Heartbeat por enlace: paciencia com o Notebook, rigor com o Mega)

- **Problema:** um unico `heartbeats_lost_threshold: 3` governava TODOS os
  enlaces = link morto apos 3s sem heartbeat. Curto demais para o Notebook
  (que trava a CPU dezenas de segundos rodando IA local sem estar
  quebrado), mas afrouxar o global afrouxaria tambem o enlace com o
  Arduino, que e o caminho da seguranca reativa.
- **Conserto:** limite POR MODULO.
  - `_EstadoModuloMonitorado.limite_proprio` (None = herda o global);
  - `HealthMonitor.timeout_de(nome)` substitui o `timeout_s` fixo em
    `modulos_com_heartbeat_perdido`;
  - `MonitorHeartbeat.monitorar(..., heartbeats_perdidos_limite=...)`;
  - config `communication.heartbeats_lost_threshold_por_link`.
- **Valores:** mission_core 45 / motion_core 45 / hardware_core no padrao 3.
  O Arduino fica curto DE PROPOSITO - descobrir tarde que o Mega sumiu e
  pior que um falso alarme (Cap 18).
- **Descoberta durante o teste ao vivo (a parte nao obvia):** com apenas
  `mission_core: 45`, o PI parou de derrubar o link - mas o NOTEBOOK
  continuava derrubando o dele. Motivo: a maquina afogada nao so ATRASA os
  proprios heartbeats, ela tambem deixa de LER os que chegam. O Notebook
  concluia que o Pi tinha morrido sendo que o culpado era ele mesmo.
  **Tolerancia precisa valer nos DOIS lados** - dai `motion_core: 45`.
- 8 testes novos (tests/unit/test_heartbeat_por_link.py), incluindo o
  cenario exato do bug (pausa de 20s: o rigido cai, o tolerante nao), que
  tolerancia nao e imortalidade (46s derruba o tolerante tambem), e que dar
  paciencia a um enlace NAO afrouxa os outros. Suite: **248 passed**.
- **Validado ao vivo, em duas etapas:**
  1. CPU do notebook travada 15s -> link sobreviveu (antes, 3s matavam);
  2. conselheiro religado temporariamente: a consulta estourou o timeout de
     25s e **o link NAO caiu** - a cascata original (consulta -> heartbeat
     perdido -> link morto -> reconexao) esta eliminada. Log do notebook
     confirma: "Heartbeat de 'motion_core': tolerancia propria de 45
     perdidos (45s)".
  3. Conselheiro desligado de volta; 2 minutos de observacao: ZERO perdas
     de heartbeat nos dois lados, 1 conexao estavel.
- **O conselheiro continua desligado e sem entregar conselho** - a
  inferencia ainda estoura o timeout de 20s. Mas agora o custo dele e zero
  em vez de derrubar o link. As duas coisas eram problemas separados.
- **PENDENCIA (pequena):** no boot do Notebook aparece um "Heartbeat
  perdido: modulo='motion_core'" antes do primeiro heartbeat chegar -
  falso positivo de partida, seguido de reconexao imediata. Inofensivo,
  mas sujo: o monitor devia dar carencia inicial ao modulo recem-registrado.

## 2026-07-19 (Falso positivo de heartbeat no boot)

- **Causa (nao era o que eu supus):** a perda aparecia IMEDIATAMENTE apos o
  registro, com 45s de tolerancia configurados - logo nao vinha do caminho
  do timeout. Vinha do outro: `iniciar()` chama `enviar_heartbeat` desde a
  primeira volta, mas no boot o laco comeca ANTES de o supervisor TCP abrir
  o link. `_resolver_link` levanta "sem rota", e o codigo tratava isso como
  PERDA. **"Ainda nao conectei" e "perdi a conexao" sao estados
  diferentes** - o codigo confundia os dois.
- **Conserto:** `_ja_estabelecidos` (peers com quem a comunicacao ja
  funcionou pelo menos uma vez, por envio OU por recebimento).
  `_marcar_perdido` ignora quem nunca conectou; o nivel do log de falha ao
  enviar acompanha (debug antes do primeiro sucesso, warning depois).
  Efeito colateral bom: para de disparar reconexao para um link que ja
  estava sendo aberto.
- **Teste antigo precisou mudar** (`test_falha_ao_enviar_heartbeat_tambem_
  gera_comm_module_lost`): ele simulava "link que morreu" simplesmente NAO
  registrando o link - usando "sem rota" como atalho. O atalho virou
  ambiguo, porque "sem rota" e tambem o estado do boot; era exatamente a
  confusao sendo corrigida. Agora o teste usa um transporte que funciona
  uma vez e depois quebra - o cenario que ele sempre disse cobrir. A
  INTENCAO do teste foi preservada, so o setup mudou.
- 4 testes novos (tests/unit/test_heartbeat_boot.py), incluindo o inverso
  (a carencia acaba no primeiro sucesso - nao pode virar mordaca) e que
  RECEBER heartbeat tambem estabelece o peer. Validados dos dois lados:
  bug reintroduzido de proposito -> 2 falharam; restaurado -> passaram.
- Suite: **252 passed, 8 skipped**.
- **Confirmado em campo:** boot do Notebook agora vai direto de
  "tolerancia propria de 45 perdidos (45s)" para "Motion Core conectado",
  sem "Heartbeat perdido" no meio. Pi: zero falsos positivos.

## 2026-07-19 (behavior.reduzir_carga_ia: alguem finalmente escuta)

- **O buraco:** o GuardiaoRamNotebook (no Pi) publicava
  `behavior.reduzir_carga_ia` desde que foi escrito, mas NINGUEM assinava -
  so os testes. A protecao contra travamento por falta de memoria existia
  so no papel. Passou despercebido porque **publicar evento nao falha**: o
  guardiao logava "pedindo alivio de carga" e parecia estar funcionando.
- **Faltavam DUAS pecas, nao uma:**
  1. o evento nao atravessava o link (mesma armadilha do conselheiro) -
     Pi agora repassa `behavior.reduzir_carga_ia` e `diagnostic.recuperado`
     ao Notebook com `local=False`;
  2. ninguem sabia COMO aliviar - criado `src/orion/mission/alivio_carga.py`.
- **O que se sacrifica, nessa ordem:**
  1. modelo de IA sai da RAM (`AiManager.descarregar`, keep_alive=0) - maior
     ganho pelo menor prejuizo: a proxima pergunta recarrega sozinha;
  2. Sentinela de visao pausa (`SentinelaVisao.pausar/retomar`, novo) -
     reconhecimento facial e a parte mais cara; ficar cego alguns minutos e
     melhor que travar a maquina (travada, fica cego do mesmo jeito).
  **A VOZ nao se desliga**: se o dono chamar "Fofao" durante o aperto, o
  robo tem que responder - e a funcao mais basica dele.
- Cada acao e isolada: falha ao descarregar o modelo NAO impede pausar a
  visao (alivio parcial > nenhum). Idempotente: pedido repetido pelo link
  nao alivia duas vezes.
- 9 testes novos (tests/unit/test_alivio_carga.py), incluindo o ciclo
  repetido, recuperacao de outra origem sendo ignorada, e o caso de visao
  desabilitada. Suite: **261 passed, 8 skipped**.
- **VALIDADO AO VIVO** (limiar critico elevado a 5000MB temporariamente
  para forcar o disparo, depois restaurado a 700):
  ```
  PI:   RAM do Notebook critica: 4152 MB livres (< 5000) - pedindo alivio
  NOTE: ALIVIO DE CARGA acionado (RAM livre: 4152 MB)
  NOTE: modelo de IA descarregado da RAM
  NOTE: Sentinela de visao PAUSADA (alivio de carga)
  NOTE: RAM do Notebook recuperada (5237 MB) - retomando carga normal
  NOTE: Sentinela de visao retomada
  ```
  **O alivio liberou ~1,1 GB** (4152 -> 5237 MB). O efeito e mensuravel,
  nao simbolico. Ciclo completo (aliviar + recuperar) confirmado.
- Cuidado que quase repeti: `sentinela` so existe dentro do `if` de visao
  habilitada - inicializada como None antes, senao NameError com visao
  desligada (mesmo tipo de erro do `event_bus`/`bus` de mais cedo).

## 2026-07-19 (Limiar de impacto: instrumentado, nao adivinhado)

- **Nao escolhi um numero novo.** O usuario nao pode ir ate o robo agora, e
  sem medida trocar 2,5 G por outro valor seria so trocar de chute. O que
  dava para fazer sem dado eu fiz - e e mais do que parecia.
- **Instrumentacao (`aceleracao_g` e `pico_g` na TELEMETRY):** antes o
  `impacto_detectado` era um booleano derivado de um limiar invisivel. Agora
  da para VER a aceleracao. `pico_g` guarda o maior valor desde o quadro
  anterior e zera na leitura - o instantaneo quase nunca pega o topo de um
  tranco de 10-20ms (IMU lido a cada 50ms, telemetria a cada 500ms).
- **Medido com o robo parado (93 amostras, 60s):** min 1,01 / mediana 1,01 /
  max 1,02 G. Baseline exatamente na gravidade (confere com a fisica) e
  **ruido de +-0,01 G**. Ou seja: o limiar nao precisa ser alto por causa de
  ruido - 2,5 G esta ~150x acima da faixa de ruido parado. Falta medir o
  piso com o robo ANDANDO (vibracao levanta) e o pico de uma batida real.
- **BUG ENCONTRADO E CORRIGIDO - o acelerometro estava em ±4 G.** Com teto
  de 4 G e limiar em 2,5 G sobrava quase nada de faixa util: uma batida de
  verdade SATURAVA o sensor e o pico saia menor do que foi - esbarrao e
  pancada forte viravam ambos "4 G". **Isso tornava impossivel escolher
  limiar por medida**, entao tinha que ser consertado antes de qualquer
  ajuste. Trocado para ±8 G.
- Custo da troca: ZERO na pratica. Medido depois: baseline 1,01 G e ruido
  +-0,01 G, identicos ao de ±4 G (ADC de 16 bits sobra). A inclinacao
  tambem nao sofre - usa a DIRECAO do vetor normalizado, nao a magnitude.
- **Limiar agora e ajustavel sem regravar firmware:** comando
  `SET_IMPACT_THRESHOLD` + `tools/testar_imu.py --limiar 3.0`, gravado na
  EEPROM em endereco SEPARADO da calibracao do vetor (32, magico 0x0111) -
  ajustar o limiar nao pode apagar a calibracao feita na bancada. Faixa
  aceita 1,05 a 7,5 G (abaixo dispararia com a gravidade parada, acima
  satura). Testado: gravado 3,2 G, resetado o Mega, releu 3,2 G da EEPROM.
  Restaurado ao default 2,5 G - mudar sem medida seria outro chute.
- `limite_impacto_g` tambem vai na telemetria (a interface mostra contra o
  que o pico esta sendo comparado).
- **PENDENCIA (a que realmente fecha o assunto):** com o robo ANDANDO,
  rodar `tools/testar_imu.py` e anotar (a) o pico de vibracao normal
  trafegando, (b) o pico de um esbarrao leve, (c) o pico de uma batida
  real. Escolher o limiar entre (a) e (b), e gravar com --limiar.

## 2026-07-19 — Fechamento da sessão

Começou como "testar o sensor MPU" e virou uma caçada a bugs escondidos.
O MPU estava bom desde o início; o teste é que destravou o resto.

**Consertado (com teste de regressão e validação em campo):**
1. Offset de 9,3° do MPU — vetor de referência na EEPROM (não subtração).
2. Flag de impacto invisível — janela de 1s em vez de instantâneo.
3. **DHT fantasma corrompendo a serial** — bit-banging com interrupções
   desligadas comia ~1,5% de TODO comando, silenciosamente. 3000/3000 ok
   depois. Era a causa real do "ACK perdido" de 18/07.
4. Vazamento de conexões TCP — socket e tarefa não fechados na reconexão.
5. Heartbeat único para todos os enlaces — agora por link.
6. Falso positivo de heartbeat no boot — "ainda não conectei" ≠ "perdi".
7. `behavior.reduzir_carga_ia` que ninguém escutava — proteção contra
   travamento por memória existia só no papel. Libera ~1,1 GB medidos.
8. Acelerômetro em ±4 G saturando em impactos reais — agora ±8 G.

**Construído:**
- Integração cognitiva em 3 camadas: modelo de mundo (com prazo de
  validade em todo dado), grounding (fez 4 modelos pararem de inventar),
  conselheiro de IA (opina, não manda).
- Bateria no firmware (divisor 100k/27k, chave desligada até montar).
- Sensores térmicos de Pi e Notebook.

**Testes: 261 passed, 8 skipped** (eram 208 no início do dia).

**PENDÊNCIAS ABERTAS — em ordem de bloqueio:**

1. **Divisor de tensão da bateria** (físico): montar 100k/27k no A0 com
   GND comum, trocar `ORION_BATERIA_INSTALADA` para 1 e regravar.
2. **Limiar de impacto** (físico): medir com o robô ANDANDO — piso de
   vibração, esbarrão leve, batida real — e gravar com `--limiar`.
3. **DHT** (físico): quando instalar, `ORION_DHT_INSTALADO` para 1.
4. **Conselheiro de IA desligado**: a inferência do gemma3:1b não cabe no
   orçamento de tempo (>20s). Religar exige modelo/hardware mais rápido ou
   rodar a inferência com nice deixando um núcleo livre. Código pronto.
5. **Julgamento do gemma3:1b é fraco** para decisão (escolheu repouso com
   obstáculo a 34cm). Se a camada 3 for religada, avaliar o 4b só para
   isso — foi por essa razão que ele não foi apagado.
6. **Camada 2 da cognição (memória na decisão) não foi construída** — o
   grounding existe, mas quem o alimenta com observações reais ainda não.

**Lição do dia que vale além deste projeto:** dois bugs (o conselheiro e o
alívio de carga) sobreviveram porque **publicar evento não falha**. O
código logava que estava trabalhando e ninguém escutava do outro lado.
Vale desconfiar de todo `publish` cujo consumidor não se consegue apontar.

## 2026-07-20 (Camada 2 da cognicao: o diario - e a licao de testar os DOIS lados)

- **`src/orion/mission/diario.py`:** escuta `vision.person_detected` e
  `sentinela.alerta`, grava na tabela `eventos` (categoria memoria
  "eventos", origem "diario") e le de volta em
  `observacoes_de_hoje()` no formato que o grounding espera. Plugado no
  MissionPlanner (`contexto["observacoes"]`) e no conversar_fofao.
  Ate aqui o bloco de observacoes chegava SEMPRE vazio - o robo era
  honesto por nao ter memoria, nao por ter olhado.
- **Cuidado principal - nao inundar:** `vision.person_detected` dispara a
  cada verificacao. Sem janela de silencio (600s por pessoa) seriam
  centenas de "vi o Joao Paulo" por hora, enchendo o prompt de repeticao -
  a melhor forma de um modelo pequeno perder o que importa. Teto de 8
  observacoes no contexto pelo mesmo motivo.
- 16 testes (tests/unit/test_diario.py).

### O achado que muda a conclusao de ontem

Testei os DOIS lados (com registro e sem) e o 1b **falhou**:

| caso | 1b (enfase em nao inventar) | 1b (enfase em usar fatos) | 4b |
|---|---|---|---|
| A) diario TEM a Ana | "Nao vi" ERRADO | "Sim, as 14:30" ok | ok |
| B) diario VAZIO | "Nao vi" ok | "Sim, a Ana passou" ERRADO | ok |
| C) diario so com BRUNO | "Nao vi" ok | "Sim, as 09:12" ERRADO | "Sim, vi a Ana as 09:12" **ERRADO** |

- **Ontem eu validei o grounding com UM caso** (o de recusa) e declarei
  vitoria. Errado da minha parte: testando o caso oposto, o 1b so troca de
  modo de falha conforme a enfase da instrucao - ele segue o TOM, nao os
  dados. Reescrever o prompt nao conserta.
- **O caso C e o pior e derruba os DOIS modelos:** existe um registro com
  hora, entao respondem "sim, as tal hora" sem conferir DE QUEM e. Atribuir
  a visita do Bruno a Ana e afirmacao falsa sobre a familia.
- **Conclusao de projeto: isso nao e tarefa de modelo de linguagem, e
  consulta a banco.** Perguntas "voce viu o fulano hoje?" passaram a ser
  respondidas DETERMINISTICAMENTE no MissionPlanner
  (`_responder_sobre_quem_viu`), por comparacao de nome, seguindo o padrao
  que ja existia para "que horas sao". A IA fica com conversa livre, que e
  o que ela faz bem. 18 testes (tests/unit/test_planner_diario.py),
  incluindo o caso C que os dois modelos erraram.
- Grounding tambem foi rebalanceado (a regra so punia inventar; agora
  tambem diz que negar o que esta no diario e erro) e separa
  "neste exato momento" de "hoje" - o 1b lia "nao estou vendo ninguem
  agora" e respondia sobre o dia inteiro.
- **Correcao lateral:** `ai_manager` importava `ollama` no topo, o que
  tornava o `mission_planner` inimportavel no Pi e nos testes (nenhum teste
  o importava, por isso ninguem viu). Import preguicoso, igual ao
  conselheiro.
- Suite: **295 passed, 8 skipped**.
- **PENDENCIA:** o modelo continua sendo o gemma3:1b para conversa livre -
  com o grounding ele nao inventa, e a parte factual agora nao passa por
  ele. Mas a validacao "os quatro modelos pararam de inventar" do journal
  de ontem vale so para o caso de RECUSA; nao repetir essa conclusao sem
  testar os dois lados.

---

## 2026-07-20 (blog publico do projeto no GitHub Pages)

- **Repositorio publicado.** Ate hoje o `~/orion-os` so tinha o remote
  `notebook` (via ssh) e o `origin` do GitHub estava vazio, sem nenhum
  branch. Primeiro push do `master` feito hoje: todo o historico foi para
  https://github.com/jproma23/Orion-Os (repo publico).
- **Blog criado.** `tools/build_blog.py` gera um site estatico a partir
  deste proprio journal: quebra o arquivo nas entradas `## <data>`, uma
  entrada = um post, e escreve `docs/index.html` + `docs/posts/*.html`.
  53 posts na primeira geracao.
  - Sem dependencia externa (o conversor de Markdown cobre so o que o
    journal usa: titulos, listas aninhadas, negrito, codigo inline e
    blocos). Nada de Jekyll - por isso o `docs/.nojekyll`.
  - Tema claro/escuro automatico, layout de leitura em coluna unica.
  - O cabecalho das entradas aparece em dois formatos no journal
    (`## data (assunto)` e `## data - assunto`); o parser aceita os dois.
    Sem isso, uma entrada some silenciosamente do indice.
- **GitHub Pages ligado** em `master` + pasta `/docs`. Site no ar:
  https://jproma23.github.io/Orion-Os/
- **Fluxo daqui pra frente:** escrever no journal como sempre, rodar
  `python3 tools/build_blog.py` e commitar. O blog nao e escrito a mao -
  ele e uma projecao do journal, entao nao ha dois lugares para manter.
- **Nota de credencial:** o push exigiu um Personal Access Token
  *fine-grained* com `Contents: Read and write` de fato marcado em
  "Permissions" - marcar so "All repositories" em "Repository access"
  deixa o token somente-leitura, e o erro que aparece e um 403 generico
  no `git push`, que nao diz qual permissao falta.
- **Proximo passo:** seguir na missao de audio (mic USB + caixinhas
  chegando dia 23/07); o blog atualiza junto com o journal.

---

## 2026-07-20 (blog: ilustracoes e as primeiras fotos do hardware)

- **Ilustracoes SVG** dos tres computadores (`docs/assets/`): notebook com o
  rosto do Fofao na tela, Pi 4B com a barra de 40 pinos, Mega com o canto
  chanfrado, mais o diagrama `corrente.svg` mostrando quem fala com quem.
  Sao embutidos no HTML (nao via `<img>`) de proposito: assim herdam
  `currentColor` e acompanham o tema claro/escuro sozinhos.
- **Fotos reais do hardware** publicadas em `docs/assets/fotos/`: a torre
  (webcam no pan/tilt + HC-SR04), o chassi por dentro (Mega, servos,
  driver) e o avatar rodando no notebook.
- **Tratamento das fotos antes de subir** (o script esta no journal porque
  vale repetir): `ImageOps.exif_transpose` para respeitar a orientacao,
  depois copiar para uma imagem nova - assim nenhum metadado e herdado -
  redimensionar para 1400px e salvar em WebP q=82. As originais do celular
  tinham **EXIF com GPS**; o repo e publico, entao isso sai antes do
  commit. 9,8 MB viraram 286 KB.
- **Nao publicar rosto.** As fotos de cadastro facial da familia
  (`~/Downloads/joao paulo|marah|kamall.*`) ficaram de fora por decisao
  explicita: repo publico, e o que entra no git nao sai do historico.
  Regra para as proximas sessoes: foto so do projeto.
- **Proximo passo:** missao de audio (mic USB + caixinhas, 23/07). As fotos
  do resultado entram na galeria do blog.

---

## 2026-07-20 (perfil do GitHub e capas)

- **Perfil do GitHub montado.** Criado o repo `jproma23/jproma23` (branch
  `main`) - o README dele e o que aparece em github.com/jproma23. Texto do
  "sobre mim" escrito pelo proprio Joao Paulo; o README so organizou em
  secoes, tabela do ORION OS e badges.
- **Capa** (o poster do projeto) no topo do perfil e tambem no topo do
  blog, em `docs/assets/capa.webp`.
- **Limite da API, para nao perder tempo numa proxima vez:** um PAT
  consegue criar repo e dar push, mas **nao** troca a foto de perfil (o
  GitHub nao tem endpoint para isso, em nenhum nivel de permissao) nem a
  bio/status (precisa de permissao de perfil, que o token nao tinha).
  Essas tres coisas sao manuais em github.com/settings/profile.
- **Fotos de rosto da familia continuam fora** do repo publico. O poster
  do perfil e foto do proprio dono, em pagina dele - caso diferente.
- **Proximo passo:** missao de audio (23/07). Journal como sempre, depois
  `python3 tools/build_blog.py` e push.

---

## 2026-07-20 (wake word "Fofão" - decisao adiada para o mic novo)

- **Achado:** `src/orion/voice/wake_word.py` faz casamento EXATO da palavra
  (`\b` sobre a transcricao). O Whisper erra fala curta com frequencia
  ("fofao" -> "fofo", "fofa", etc.), entao match exato deixa passar quem
  chamou. A memoria do projeto dizia "wake fuzzy", mas o codigo nunca teve
  fuzzy - ficou so intencao.
- **Descartado o fuzzy por similaridade** (difflib com limiar). Motivo: o
  caso "fogao" da 0.80 de parecenca com "fofao", igual a mishears legitimas
  como "fofau" - nao da pra separar por limiar sem ou perder acerto ou
  acordar o robo toda vez que alguem fala em fogao (palavra de cozinha,
  dita toda hora).
- **Decisao (do dono):** manter match exato e apenas ACRESCENTAR a uma
  lista as formas que o Whisper realmente cospe errado. Controle palavra a
  palavra; "fogao"/"fulano" nunca entram na lista de proposito.
- **Adiado para 24/07 (missao de audio):** a forma como o Whisper erra
  depende da voz + mic + ambiente. Montar a lista OUVINDO de verdade com o
  mic USB novo, nao no chute. Fluxo: robo erra ao vivo -> ver o que apareceu
  na transcricao -> adicionar aquela forma.
- **Aberto:** decidir se a lista fica em `config/orion.yaml` (editavel sem
  mexer no codigo) ou fixa no codigo.

---

## 2026-07-20 (panorâmica por pan/tilt + conserto: repouso agora nivela a cabeça)

- **Modo visão validado no hardware, de ponta a ponta.** Montei uma
  panorâmica por varredura: o Pi gira o pan de -60° a +60° (a webcam está
  montada em cima do pan/tilt, então gira junto) e o Notebook fotografa em
  cada ângulo; o OpenCV costura. Três scripts novos: `tools/panorama.py`
  (Pi, orquestra), `tools/capturar_frame.py` e `tools/montar_panorama.py`
  (Notebook). Serial é exclusiva: parar `orion-motion.service` antes,
  religar depois.
  - Pegadinha 1: chamei `python3` do sistema no Notebook para capturar;
    o cv2 está no `.venv`. Corrigido para `~/orion-os/.venv/bin/python`.
  - A costura funcionou (não caiu no fallback), mas saiu torta: metade da
    varredura é parede lisa, sem textura para casar bordas. Os frames
    individuais estão ótimos e nivelados. Fiação do servo está CORRETA -
    tilt=0 dá visão nivelada, pan varre no sentido certo. NÃO inverter fios.
- **Bug real encontrado no maestro:** o `Repouso` (comportamentos.py) só
  publicava status e dormia - nunca comandava pan/tilt. Como toda
  reconexão serial reseta o Mega, e no reset os servos vão para o padrão do
  firmware (cabeça baixa), depois de qualquer restart a cabeça ficava
  apontada para baixo até algo mandá-la subir - e nada mandava.
  - **Correção:** `Repouso.executar` agora publica `motion.pan_tilt {0,0}`
    ao assumir. Prontidão é com a cabeça no nível. Sobrevive a resets.
    Confirmado ao vivo: frame após o restart voltou nivelado. 5 testes de
    comportamento seguem passando.
- **Aberto:** panorâmica bonita pede mirar o lado da bancada (textura) ou
  passos de 10° com mais overlap. Fica para quando quiser.

---

## 2026-07-20 (bug do pan/tilt trocado: diagnóstico limpo + conserto por fio)

- **Sintoma:** ao rodar a panorâmica, o dono viu a cabeça mexer "cima e pra
  baixo" quando o comando era de pan (lado). Suspeita: pan/tilt trocados.
- **Diagnóstico controlado, um eixo por vez** (`tools/testar_so_pan.py`,
  agora aceita `pan|tilt`): SÓ pan → cabeça subia/descia; SÓ tilt → cabeça
  virava pro lado. Confirmado: os dois canais estavam cruzados.
- **Confirmação que EU consigo ler** (`tools/diag_pan.py`): captura a vista
  em pan=-60 e pan=+60. Antes do conserto a vista mudava na vertical;
  depois, na horizontal (dois pedaços diferentes da sala, mesma altura).
- **Correção (decisão do dono):** inverter os fios de SINAL dos servos nos
  pinos 10 e 11 do Mega (`SERVO_PAN=10`, `SERVO_TILT=11` em pins.h). O
  firmware NÃO mudou; a troca física fez a realidade bater com o código -
  agora pino 10 aciona o servo horizontal e o 11 o vertical, como o nome diz.
  Ou seja: o "CONFIRMADO 2026-07-18" do pins.h estava na verdade cruzado;
  só apareceu agora porque exige mover um eixo isolado pra notar.
  IMPORTANTE: como foi conserto por fio, NÃO inverter também no firmware -
  seria dupla inversão, voltaria ao errado.
- **Panorâmica boa validada** após o conserto: varredura de pan -50°..+20°
  com tilt -15° mirando a bancada, 8 frames, costurados nos dois modos
  (`montar_panorama.py` agora gera cilíndrica E plana). Cilíndrica é a certa
  pra câmera que gira. Captura ganhou 2ª tentativa contra travamento da
  webcam (`select() timeout` quando o USB é esbarrado).
- **Privacidade:** os frames/panorâmicas pegaram o dono e o cômodo -
  mantidos SÓ local (Pi + Notebook), nada publicado no blog.

---

## 2026-07-20 (pins.h corrigido + nível do tilt medido)

- **pins.h:** comentário dos servos pan/tilt atualizado - explicava
  "CONFIRMADO 2026-07-18", mas naquela data os fios estavam cruzados.
  Agora registra: pinos certos, fios de sinal invertidos em 2026-07-20,
  pino 10 = pan (horizontal) e pino 11 = tilt (vertical), validado.
- **Nível do tilt medido** (`tools/diag_nivel.py`, pan=0 em tilt 0/-10/-20):
  tilt=0 olha reto (porta e interruptor no centro, bancada abaixo); valores
  negativos apontam pra cima (o -20 pega o teto). Como o repouso já manda
  tilt=0, a cabeça no repouso JÁ fica nivelada agora que os fios estão
  certos. O "olhando pra cima" de antes era o efeito dos canais cruzados,
  não uma pose errada. Nada a mudar no código.

---

## 2026-07-20 (varredura por voz + ultrassom frente/trás trocado + telemetria na web)

- **Comando de voz "varredura"** adicionado (`mission_planner.py`): padrão
  varredura|varrer|escanear|scan → SCAN_FRONT, enviado ao Mega. O
  motion.scan_complete resultante preenche o mapa polar da web.
- **Bug real na navegação:** `_ao_receber_comando` não tratava SCAN_FRONT -
  caía no else como "ação desconhecida". A Ronda pede exatamente isso, então
  a varredura autônoma nunca funcionou. Adicionado o caso → `_escanear_a_frente()`.
  Conserta a Ronda e habilita o comando de voz. +2 testes.
- **Ultrassom frente↔trás estava trocado no código** (achado do dono: tapar
  o sensor traseiro mexia na distancia_frontal_cm - a navegação desviava
  olhando o sensor errado, que é segurança). Corrigido em `pins.h`: frontal
  agora 26/27, traseiro 22/23. Comentário do main.cpp atualizado.
  **Firmware recompilado e regravado no Mega** (pio, RAM 36%, flash 16,6%).
- **Telemetria que o firmware já mandava mas a web ignorava**, agora exposta
  na dashboard: distância traseira, aceleração (G) e impacto. Sanity check
  ao vivo: aceleracao_g ≈ 1,01 parado (= gravidade), traseira lendo 33 cm.
- **Aceleração:** confirmado que já é medida (MPU6050) - usada para detecção
  de impacto (limiar 2,5 G, ajustável, EEPROM) e inclinação; agora também
  visível na tela.

---

## 2026-07-20 (distâncias frente/trás no mapa + cache-bust da web)

- **Mapa da web** agora mostra distância frontal e traseira ao vivo, ao lado
  do radar polar (o dono fica mais nessa página). `mapa.js` passou a escutar
  o evento SSE `comm.mensagem.telemetry` (payload direto do Mega) além de
  motion.position/scan_complete.
- **Cache-bust** (`?v=20260720`) nos includes de `dashboard.js` e `mapa.js`:
  o navegador guardava o JS antigo e os campos novos (traseira, aceleração,
  impacto) não apareciam mesmo com o servidor entregando certo. Agora troca
  sozinho sem precisar limpar cache.
## 2026-07-23 (recuperacao pos-reinstall do Pi + fio da replica ligado + reles)

- **Raspberry reinstalado do zero** (usuario fez backup antes). Projeto
  restaurado em ~/orion-os (sem .venv/caches, recriado do zero - venv +
  `pip install -e ".[dev,display]"`, 169 testes passando). `orion-motion.service`
  reinstalado (systemd --user + linger). Raspberry Pi Connect ativado
  (login feito). Claude Code nativo instalado no Pi.
- **`orion.db` original perdido de verdade** - a replica cruzada nunca
  tinha rodado em producao antes do reinstall (so em teste), so nao tinha
  copia no Notebook. Familia recadastrada via `tools/cadastrar_de_fotos.py`
  a partir das fotos que sobreviveram no Notebook (data/fotos/): João
  Paulo (dono, id=1), Marah (dona, id=2), Kamal (morador, id=3) - mesmos
  IDs de antes.
- **Causa provavel da corrupcao original**: Raspberry na mesma linha de
  5VCC dos servos - queda de tensao (brown-out) ao ligar tudo pode ter
  travado o firmware da VideoCore (achado um deadlock real: `rpi-fw-crypto`
  preso, `vcgencmd` sem responder, timeouts -110 em GPIO/clock/hwmon,
  kworkers travados 240s+). Resolvido com `apt upgrade` (129 pacotes) +
  reboot limpo - confirmado limpo depois (`throttled=0x0` instantaneo,
  load normal). Usuario vai colocar capacitores (1000-2200uF baixa ESR)
  perto da entrada de 5V como correcao de hardware.
- **`tools/desligar_tudo.sh` criado**: desliga Raspberry + Notebook juntos
  com seguranca (`systemctl poweroff` via SSH, chaves dedicadas
  `~/.ssh/id_ed25519` autorizadas cruzado, nomes mDNS `pi-os.local`/
  `Joao.local` em vez de IP). Notebook precisou de
  `/etc/sudoers.d/desligar_tudo` (NOPASSWD so pra `systemctl poweroff`,
  Pi ja tinha NOPASSWD:ALL). Testado de verdade - funcionou nos dois
  sentidos, confirmado por ping/porta 22 caindo.
- **Fio da replica cruzada finalmente ligado** (existia desde a Fase 3 so
  em teste, nunca rodava em producao - `TarefaManutencao` nunca era
  instanciada em `motion_core/__main__.py`, e ninguem registrava
  `ReceptorReplica` no Notebook):
  - `motion_core/__main__.py`: `_iniciar_manutencao()` cria a
    `TarefaManutencao` e assina `database.backup_completed` ->
    `replicar_backup(..., destino="mission_core")`, tolerando o Notebook
    desconectado (so avisa, tenta de novo no proximo backup).
  - `tools/conversar_fofao.py` (processo real que ja segura o link TCP
    com o Pi): registra `ReceptorReplica(notebook_replica_dir)` uma vez,
    no boot - NAO um processo separado, porque so pode existir UM link
    "mission_core" por vez do lado do Pi (`_ao_conectar_notebook`
    sobrescreve por nome).
  - Bug real encontrado e corrigido: `TarefaManutencao.iniciar()` checava
    a hora IMEDIATAMENTE ao subir (antes de esperar o link do Notebook
    reconectar) - corrida perdida sempre que o backup_hour bate com a
    hora do boot. Corrigido com uma espera inicial de
    `intervalo_verificacao_s` antes do loop comecar.
  - **Testado ponta a ponta de verdade** (backup_hour ajustado
    temporariamente pra hora atual, depois revertido pra 3): "Replica
    enviada: orion_20260723T225746Z.db (4 blocos)" no Pi, arquivo de
    100KB confirmado em `~/orion-os/data/backups_replica/` no Notebook.
  - 2 testes de `tests/integration/test_motion_core_main.py` corrigidos
    (`_abrir_memoria` passou a retornar tupla `(memory_api, db)`) + 1
    teste novo cobrindo a integracao (`_iniciar_manutencao` disparando
    replica de verdade). 206 passed, 8 skipped no Pi.
- **Reles de energia adicionados** (usuario decidiu usar rele em vez de
  so o pino ENA dos TB6600 - mais seguro, corta energia de verdade):
  - `RELE_MOTORES` (pino 13, ativo em LOW): HIGH por padrao/parado
    (motores sem energia), LOW so enquanto anda de verdade
    (`MotorManager::_ligarRele()`/`_desligarRele()`, chamado em cada
    metodo de movimento e em `parar()`). Nivel escrito ANTES do
    `pinMode(OUTPUT)` pra nao ter glitch LOW no boot (tecnica padrao
    AVR: pre-carrega o registrador PORT antes de virar DDR pra saida).
  - `RELE_VENTILADOR` (pino 12, ativo em LOW, logica "reversa"): comeca
    LIGADO por padrao (fail-safe, resfria ate prova em contrario).
    Comandos novos `FAN_ON`/`FAN_OFF` no `command_executor.h`, mesmo
    padrao de `LIGHT_ON`/`LIGHT_OFF` - a decisao de limiar de
    temperatura fica pro lado Python (Raspberry le a propria temperatura
    e manda o comando ja resolvido; Arduino so executa, Cap 10: nunca
    decide sozinho). Ainda falta o lado Python que le a temperatura do
    Pi e manda FAN_ON/FAN_OFF - proximo passo.
  - Compilado com sucesso via PlatformIO (instalado num venv dedicado no
    Notebook, `~/.venv-pio`, ambiente Debian bloqueava pip --user
    direto): RAM 32.5%, Flash 15.4%, sem erros. Nao gravado no Mega real
    ainda - falta o usuario flashar e testar fisicamente.
- **Pendente para depois**: firmware do fan ainda sem o lado Python
  (ler temperatura do Pi, decidir limiar via config/orion.yaml, mandar
  FAN_ON/FAN_OFF); explorar IA remota via API do usuario em vez do Ollama
  local (tensao com o principio "100% offline" do CLAUDE.md, precisa EDR
  se for mudanca de arquitetura de verdade); ideia de aprendizado hibrido
  online/aprende da internet quando disponivel, offline segue a base de
  dados local; usuario quer validar voz real ouvindo/falando ao vivo.

## 2026-07-23 (continuacao - IA remota de verdade + IA estrategica no maestro)

- **EDR-0021 aprovado e implementado**: IA remota (OpenRouter, API
  compativel com a OpenAI - `base_url` configuravel) com fallback
  automatico pro Ollama local se falhar (sem internet/erro/sem chave).
  `AiManager` virou fabrica com `_chamar_openai`/`_chamar_ollama` atras da
  mesma interface `responder()`. Chave em `config/secrets.env`
  (gitignored, `chmod 600`), carregada via `EnvironmentFile=-...` no
  `orion-avatar.service`. **Testado de verdade**: resposta real da IA em
  portugues via OpenRouter ("Oi! Como posso ajudar voce hoje?").
  - Ordem das tentativas ate funcionar: Gemini direto (chave sem cota,
    `limit: 0` no free tier), OpenAI direto (mesma coisa,
    `insufficient_quota` mesmo apos adicionar credito - suspeita de
    descompasso projeto/organizacao), OpenRouter (funcionou de primeira
    com credito real).
  - `google-generativeai` foi tentado e abandonado (biblioteca
    descontinuada pela Google em 2026, aviso explicito) antes de decidir
    por OpenAI/OpenRouter.
- **Vault de conhecimento (Obsidian) criado**: `motion_core/memory/vault.py`
  (`VaultConhecimento`) - notas `.md` com wikilinks em
  `/mnt/ssd/orion/obsidian_vault/` (mesmo SSD do `orion.db`, ainda NAO
  entra no backup/replica diario - pendente). Exposto via
  `memory.nota_escrever`/`memory.nota_buscar` na `PonteMemoria`. IA
  consulta o vault antes de responder (`MissionPlanner._consultar_contexto`
  busca `notas_relevantes` pelo texto do usuario).
- **EDR-0022 aprovado e implementado**: IA estrategica no maestro. Novo
  comportamento `IaEstrategica` (`motion_core/behavior/comportamentos.py`,
  prioridade 40 - a vaga da "Patrulha agendada" do EDR-0020, nunca
  implementada). Consulta a IA em ritmo proprio (`intervalo_s=30`, task de
  fundo iniciada em `__main__.py`, NUNCA no tick de 200ms do maestro) via
  novo comando `mission.decidir` (comm.request Pi->Notebook, ponte nova
  `src/orion/mission/decisao_estrategica.py::PonteDecisao` do lado
  Notebook, `AiManager.decidir()` novo). Vocabulario FECHADO pra v1:
  `descansar` (nao disputa controle) e `observar_ambiente` - a IA nunca
  aciona hardware direto. Continua abaixo de VigilanciaObstaculo (100),
  Atender (80) e Vigilia (60) - segurança nunca e arbitrada, garantia do
  EDR-0020 mantida. Prompt de decisao e o `prompt_sistema.txt` atualizados
  para o papel de "cuidador do lar e da familia" (pedido do usuario) - mais
  vocabulario (ex.: frases motivacionais, alerta de estranhos) fica para
  quando o "psicologo da familia"/simulacao de sentimentos for desenhado
  (conversa futura, usuario mencionou querer isso).
- **Bug real corrigido**: `TarefaManutencao.iniciar()` (achado testando a
  replica) verificava a hora IMEDIATAMENTE ao subir, perdendo a corrida
  contra a reconexao do Notebook sempre que `backup_hour` batia com a hora
  do boot - corrigido com espera inicial de `intervalo_verificacao_s`.
- **PENDENTE - bug nao resolvido, investigar amanha**: apos a ultima leva
  de mudancas (IA estrategica), o `orion-avatar.service` do Notebook trava
  no boot logo depois de "VAD ligado" (nunca chega a "Motion Core
  conectado" nem a "Pronto!") - processo fica vivo mas ocioso (~4% CPU,
  estado sleeping), nao e crash. Confirmado reproduzivel em 2 restarts
  seguidos, mesmo ponto exato. Nao foi possivel usar `strace` (nao
  instalado) para ver a syscall exata. Hipoteses a testar amanha: algo
  entre `VoiceCore(...)`, `SentinelaVisao` (camera - ja falhava antes,
  tolerado) ou `sintetizador.falar(...)` (Piper/audio) bloqueando de
  verdade em vez de so demorar; testar comentando pedacos do boot pra
  isolar. NAO parece ligado ao `PonteDecisao` em si (so faz
  `event_bus.subscribe`, sem I/O).
- **Achado (nao relacionado a hoje)**: `git status` no Notebook mostra
  varios arquivos nao commitados de 2026-07-19/20 (antes deste incidente):
  `src/orion/mission/conselheiro_comportamento.py`, `conselho_protocolo.py`,
  `diario.py`, `grounding.py`, `alivio_carga.py`, `src/orion/vision/gestos.py`,
  `src/orion/heartbeat.py`, `src/orion/watchdog.py`, e copias soltas de
  `conversar_fofao.py`/`orion.yaml` na raiz do repo - trabalho antigo nunca
  commitado, provavelmente experimentos anteriores (os nomes sugerem
  tentativas antigas de ideias parecidas com o que foi feito hoje -
  conselheiro de comportamento, diario). Nao mexido, decisao do usuario
  pendente (commitar, terminar ou descartar).
- Testes: 239 passed/7 skipped no Pi, 298 passed/1 failed (unico teste
  `test_heartbeat.py` flaky por timing, ja confirmado independente das
  mudancas de hoje - passa limpo no Pi) no Notebook.
- **Proximos passos**: resolver o travamento do boot do Notebook; expandir
  o vocabulario de `IaEstrategica`; incluir o vault no backup/replica
  diario; decidir o que fazer com os arquivos nao commitados de
  2026-07-19/20; conversa de design pendente sobre "IA com simulacao de
  sentimentos"/psicologo da familia.
﻿

## 2026-07-24 (kiosk nao abria + IP do Pi mudou - dois bugs reais corrigidos)

- **Sessao rodando na maquina Windows de controle** (nao Notebook nem Pi),
  via SSH com a chave dedicada `orion_pi_ed25519` (autorizada tanto no Pi
  quanto no Notebook). Usuario reportou: tela kiosk do avatar nao abriu
  hoje, e o IP do Raspberry mudou de novo (agora `10.20.20.196`, era
  `10.20.20.188`).
- **Causa raiz 1 (nova, nao e o hang de ontem):** `orion-avatar.service`
  no Notebook caia com `ErroAudio: Nenhum microfone candidato respondeu`
  segundos apos subir - `microfones_candidatos_indices: [0, 1, 5]` em
  `config/orion.yaml` estava desatualizado; a 2a webcam (USB Composite
  Device) foi desconectada fisicamente e a enumeracao de audio mudou -
  indices 0/1/5 viraram saidas HDMI (0 canais de entrada). Confirmado com
  `sd.query_devices()` + `lsusb`: so sobraram "PC Camera" (indice 3) e
  "USB Audio Device" (indice 4) como entradas reais. Corrigido para
  `[3, 4]`.
- **Causa raiz 2 (essa SIM e o mesmo travamento de ontem, "trava depois de
  VAD ligado" - so que hoje deu erro em vez de hang):** com o mic
  corrigido, o processo chegava ate "VAD ligado" e cai com
  `KeyError: 'pasta_fotos'` em `conversar_fofao.py:372`, montando a
  `SentinelaVisao`. A secao `behavior.sentinela_visao` do
  `config/orion.yaml` (recriada no reinstall do Pi / restauracao) estava
  sem a chave `pasta_fotos`, que o construtor exige sem default. Corrigido
  adicionando `pasta_fotos: "data/sentinela"` (mesmo caminho ja usado em
  outras partes do codigo). **Isso fecha, com causa raiz de verdade, a
  pendencia de ontem** ("orion-avatar.service trava logo depois de VAD
  ligado") - nao era hang silencioso, era essa excecao nao tratada
  matando a task principal; parecia hang porque o systemd ficava
  reiniciando e o log rolava rapido.
- **IP do Pi atualizado:** `communication.raspberry.host` em
  `config/orion.yaml` (Notebook) de `10.20.20.188` para `10.20.20.196`.
- **Validado de ponta a ponta:** `systemctl --user restart
  orion-avatar.service` no Notebook ficou estavel (sem mais
  auto-restart), log mostrou "Pronto!" e depois "Motion Core conectado".
  Firefox kiosk (rodava desde antes do fix, preso numa pagina de erro de
  quando o servidor ficava caindo) foi morto e relancado
  (`DISPLAY=:0 XAUTHORITY=~/.Xauthority firefox-esr --profile
  .../orion-avatar-kiosk --kiosk http://127.0.0.1:8090`). Screenshot via
  `scrot` (copiado para a maquina Windows via scp) confirmou o avatar
  renderizando com status "ouvindo..." e "Motion Core conectado ao Event
  Bus".
- **Nao mexido:** `camera_indice_principal: 2` (Cap 8) tambem ficou
  desatualizado pela 2a webcam desconectada (so resta 1 camera fisica,
  `/dev/video0`/`/dev/video1`), mas isso e tolerado pelo codigo (nao
  derruba o processo) - so revisar se o usuario for usar Vision de verdade
  de novo com a 2a webcam ainda desconectada.
- **Proximos passos:** usuario reconectando o Pi na internet manualmente
  (Wi-Fi caiu, exige senha na tela fisica dele) - fora do escopo desta
  sessao Windows, acompanhar se o Pi volta a responder normalmente depois
  disso.
﻿

## 2026-07-24 (continuacao - vistoria de codigo completa + 5 criticos + parte dos altos + calibracao fisica)

- **Vistoria de codigo pedida pelo usuario** ("quero fazer uma vistoria em
  todo codigo... ver o que precisa fazer para nao acontecer erros no
  sistema"). Copiado o codigo completo (src/, motion_core/, firmware/,
  tests/, tools/) pra maquina Windows de controle e revisado em paralelo
  por 4 agentes (Kernel+Comunicacao, Motion Core, Vision/Voice/Mission +
  arquivos nao commitados, Display+Firmware) + auditoria manual de
  chaves de config vs orion.yaml + investigacao do teste "flaky" do
  heartbeat. **28 achados no total** (5 criticos, 6 altos, 7 medios, 4
  baixos, 6 decisoes de arquitetura pendentes nos arquivos nao
  commitados de 2026-07-19/20). Relatorio publicado como artifact HTML
  pro usuario consultar.
- **Achado que atravessa varios modulos**: `VisionCore` completo (YOLO,
  rastreamento, pan/tilt) nunca e instanciado em producao - so nos
  testes. `vision.resolution/fps/yolo_model/confidence_threshold` sao
  configs mortas hoje; FOLLOW nunca recebe `vision.person_detected` real.
- **Teste `test_heartbeat.py::test_falha_ao_enviar_heartbeat_tambem_gera_comm_module_lost`
  NAO e flaky** (rodado 5x seguidas, falhou as 5): o guard que evita
  alarme falso pra quem "ainda nao conectou" tambem impede, pra sempre,
  que um peer que nunca conseguiu conectar gere `comm.module_lost` -
  ainda sem correcao (fica pra depois, catalogado como Medio).

### 5 CRITICOS corrigidos, testados e commitados (Pi + Notebook):
1. **Firmware**: `SafetyManager::avaliar()` so parava o motor na
   transicao livre->perigo, nao continuamente - um comando de movimento
   atrasado do Raspberry podia religar o motor com o perigo ainda ativo.
   Corrigido: reforca `motores.parar()` todo ciclo enquanto o perigo
   persistir + `CommandExecutor` recusa comando de movimento novo
   enquanto `safety.pararAtivo()`. Leitura invalida do ultrassom agora
   conta como obstaculo (fail-safe) em vez de "livre pra andar".
2. **motion_core**: `TarefaManutencao.iniciar()` matava a task pra
   sempre na primeira falha de backup - agora sobrevive e tenta de novo
   no proximo ciclo, ainda na mesma hora configurada.
3. **kernel**: `communication.raspberry.host` sem validacao no schema -
   podia sumir do yaml sem a config falhar, so quebrando depois com
   KeyError cru no boot. Agora validado (`_ESQUEMA`).
4. **comunicacao**: `TcpTransport`/`SerialTransport`/`ConexaoTcp.enviar()`
   nao convertiam `OSError`/`SerialException` em `ErroTransporte` -
   queda de rede no meio do envio derrubava processo/boot inteiro.

### Parte dos 6 ALTOS corrigidos:
5. **Firmware**: ponto cego do radar durante SCAN_FRONT (~2,1s, servo
   apontado pro lado) - `SafetyManager` so confia na leitura frontal
   quando `radar.apontandoParaFrente()`.
6. **Firmware**: `Wire.setWireTimeout(25ms, reset automatico)` +
   watchdog de hardware (`wdt_enable` 2s + `wdt_reset()` todo loop) -
   travamento do I2C do IMU nao para mais a seguranca reativa inteira
   junto. `imu_manager.h::atualizar()` tambem passou a checar o retorno
   de `getEvent()`.
7. **voice**: `sintetizador.falar()` (frase de ativacao e resposta) sem
   protecao nenhuma - erro transitorio de audio de saida derrubava
   avatar+sentinela+chat juntos. Corrigido com `_falar_com_protecao()`.
- Faltam pra fechar os Altos: FOLLOW travar se comm falhar durante perda
  de alvo, SQLite concorrente (webui + manutencao sem lock), discovery
  KeyError em payload incompleto.

### Calibracao fisica do robo (sessao ao vivo, achados que so aparecem
na pratica, nao em codigo):
- **Servo do radar com horn colado torto** (nao da pra reencaixar
  fisicamente) - calibrado ao vivo com o usuario usando a webcam externa
  (DV20 USB, `/dev/video2`) pra confirmar visualmente: comandar o angulo
  LOGICO 90 (frente) precisa escrever 45 de verdade no servo. Adicionado
  `RADAR_OFFSET_GRAUS = -45` em `radar_manager.h`, aplicado so na hora de
  escrever (`_paraFisico`) - toda a logica interna continua em angulo
  logico. Novo comando de protocolo `RADAR_SET_ANGLE` (calibracao manual)
  e `PAN_TILT_SOLTAR` (`Servo::detach()` pra girar a webcam na mao).
- **Arco de varredura reduzido pra 120 graus (30-150)**: o braco do
  servo colide fisicamente com o suporte da webcam ao lado fora desse
  arco - confirmado suficiente pelo usuario pra "enxergar o mundo a
  frente".
- **RELE_MOTORES mudou do pino 13 pro pino 12** (`pins.h`): achado real
  - pino 13 e o mesmo do LED embutido do Mega, que o bootloader pisca em
  TODO reset (upload de firmware, watchdog, power-on) antes do setup()
  rodar - o rele "tremelicava" fora do controle do codigo nesses
  instantes. `RELE_VENTILADOR` foi desconectado fisicamente pelo usuario
  (consumo de energia) e a constante realocada pro pino 7 no codigo so
  pra nao colidir com o motor - `FAN_ON`/`FAN_OFF` continuam no
  protocolo, sem efeito fisico ate reconectar. Fio do motor confirmado
  fisicamente movido de 13 pra 12 pelo usuario antes da gravacao.
- **Novo `tools/gravar_firmware_seguro.sh`**: acha que orion-motion.service
  fica com a porta serial do Arduino aberta o tempo todo - gravar
  firmware por cima disso fazia dois processos brigarem pela porta
  (heartbeat falhando depois da gravacao, servo reagindo de forma
  imprevisivel). O script para o servico antes de gravar e religa depois
  sempre (trap), mesmo se a gravacao falhar.
- **5 gravacoes reais no Mega hoje**, todas compilando e verificando
  limpo via `pio run -t upload` a partir do Pi (nunca do Notebook -
  regra arquitetural: Arduino so fala com o Raspberry).

### Pendencia caracterizada, nao corrigida (decisao consciente - hora
errada pra mexer em codigo de seguranca/wake-word sem poder testar com
calma):
- **Deteccao da palavra de ativacao "Fofao" perdendo variantes reais do
  Whisper**: testado ao vivo repetidas vezes hoje (ambiente com ruido E
  depois em silencio total), transcricoes obtidas: `''` (vazio, RMS
  0.13-0.20 - som havia, transcricao nao), `'Fulfo'`, `'FAMO'`,
  `'Ei, oui!'`, `'FO FÃO'` (com espaco no meio - o mais proximo, ainda
  assim nao bate no regex `\bfofao\b` por causa do espaco). O robo nunca
  avanca de LISTENING pra WAKE_DETECTED nessas tentativas.
  `wake_word.py::DetectorPalavraAtivacao.verificar()` exige "fofao"
  contiguo, sem tolerancia. **Proximo passo**: considerar normalizar
  removendo espacos internos antes de comparar, e/ou tolerancia fonetica
  (distancia de edicao) - decidir com cuidado e teste, nao as pressas.

## 2026-07-25 (git em 3 linhas reconciliado + ciclo de reconexao TCP corrigido e validado no Pi)

- **Descoberta que motivou a sessao:** o repositorio tinha TRES linhas de
  desenvolvimento divergentes desde 2026-07-19, nenhuma subconjunto da
  outra: GitHub `73bf557` (20/07, +25 exclusivos - tem a correcao do
  ultrassom frente/tras trocado, `CLAUDE.md`->`ARQUITETURA.md`, blog e
  README que ninguem mais tem), Notebook `127701e` (+29) e Pi `d017986`
  (+18). A copia Windows usada na vistoria estava 23 commits atras, e o
  relatorio gerado a partir dela apontava problemas ja resolvidos (o IP
  `10.20.20.188`, por exemplo, ja era `.196` desde 24/07).
- **Tudo preservado antes de qualquer merge**, como branches no GitHub:
  `linha-notebook-2026-07-25`, `linha-pi-2026-07-25`,
  `backup-windows-2026-07-25`. `master`/`main` do GitHub nao foi tocado.
  A linha do GitHub segue **pendente de integracao**.
- **O Pi tinha perdido a chave SSH para o Notebook** na formatacao - o
  erro do `git fetch` era `Host key verification failed` (known_hosts com
  identidade antiga), nao falta de chave: a publica do Pi ja estava
  autorizada la. Corrigido com `ssh-keygen -R` + `ssh-keyscan`.
- **Merge Pi x Notebook (`cc900e3`):** 18 blocos de conflito, mas so 5
  reais - os outros 13 eram codigo do Pi em regiao vazia do Notebook.
  Resolucoes: `openai` fica no pyproject (os DOIS lados fazem `from openai
  import OpenAI`; o `google-generativeai` do Pi era dependencia orfa, sem
  nenhum import); microfones ficam `[1,5,0]` do Notebook (os `[3,4]` do Pi
  viraram saidas HDMI); `avancar` do Pi entra junto com o Mentor do
  Notebook; `motion_core/__main__.py` fica com o lado do Pi, que e
  superconjunto (traz o guarda-rail de prioridade).
- **Heartbeat, dois defeitos corrigidos (`214db61`):** `monitorar()` so
  fazia `append`, mas e chamado do callback de conexao TCP - que roda a
  CADA reconexao; virou idempotente. E o guard de "nunca conectou"
  ignorava a perda **para sempre**, cegando `comm.module_lost` de um link
  que morreu de vez; virou carencia de N falhas.
- **A correcao do TCP (`_descartar_link_anterior`) existia no Notebook e
  NUNCA tinha chegado ao Pi** - por isso os `socket.send() raised
  exception` nos logs. Nao era bug novo, era codigo desatualizado.
- **Validacao em producao no Pi (`af5a91d`, tag `v1.0.0-tcp-fix`):** 3
  reconexoes seguidas -> sempre 1 socket ESTABLISHED (antes acumulava; 37
  observadas em 19/07); a porta antiga sumiu de todos os estados e o inode
  mudou, provando troca real; "Heartbeat perdido" publicado UMA vez por
  perda. Os erros de envio agora aparecem so na janela real de queda e
  cessam no instante da reconexao - comportamento correto, nao regressao.
- **Testes:** 329 unitarias + 11 integracao no Notebook, 286 (7 skipped)
  no Pi, `compileall` exit=0. O poder de deteccao do teste novo foi
  comprovado desativando `_descartar_link_anterior` (falha no ponto certo)
  e reativando (passa).
- **Auditoria do backup:** nao e bug de codigo. O agendamento e por hora
  fixa (`backup_hour: 3`) e **o Pi nao fica ligado as 03:00** - os backups
  de 23/07 sao das 19h, manuais. Falha de replica NAO bloqueia o backup
  local (`executar_backup_agora` so faz backup+retencao; a replica roda
  depois, por evento, e trata `ErroComunicacao`). SSD escrevivel, 432GB
  livres, relogio sincronizado por NTP (o Pi nao tem RTC). **Proposta
  pendente:** trocar o gatilho por "no startup, se passou >24h".
- **Mudanca de arquitetura de disco no Pi (pos-formatacao):** ele agora
  BOOTA do SSD (`sda2` = `/`). `/mnt/ssd` virou diretorio comum - os dados
  seguem fisicamente no SSD, mas o caminho ficou enganoso.
- **Codigo orfao encontrado, nao mexido:** existe um `conversar_fofao.py`
  na RAIZ (versionado, de um commit `[wip]`), diferente do de `tools/` que
  e o de producao. Ele importa `AlivioCarga` e `DiarioObservacoes` -
  modulos que existem em `src/orion/mission/` e que **nenhum outro arquivo
  usa**. `escuta_habilitada` tambem nao existe no `orion.yaml`. Decidir se
  entram em producao ou saem.
- **Pendencias:** integrar a linha do GitHub; ACK intermitente do
  `SET_PAN_TILT`; `OBSTACLE_DETECTED` com o robo suspenso impedindo teste
  de motores; vault do Obsidian vazio; wake word ("FO FÃO" com espaco
  escapa do `DetectorFuzzy`, que filtra tokens com `len < 4`).

## 2026-07-27 (as 3 linhas finalmente iguais + bussola identificada + odometria em projeto)

- **Sessao rodando na maquina Windows de controle**, com SSH para o Notebook
  (`10.20.20.195`) e o Pi (`10.20.20.196`). O IP do Pi voltou a ser o `.196`.
- **O merge do GitHub estava na maquina ERRADA.** Uma sessao anterior o
  resolveu no Windows, que **nao consegue rodar a suite** - o `.venv` da
  pasta e Linux. Ou seja, 12.677 linhas foram fundidas sem um unico teste
  executado, contrariando a regra "nenhuma resolucao sem compilar".
  Preservado em vez de refeito: commitado como `3b031fc`, empurrado ao
  Notebook por `git push notebook` (SSH, sem passar pelo GitHub) e validado
  la. Nenhum marcador de conflito ficou no codigo (verificado arquivo a
  arquivo antes de commitar).
- **Achados soltos na arvore do Windows, commitados como `04ec281`**: o
  pan/tilt com sinal invertido nos DOIS eixos (a camera FUGIA do alvo) e o
  `SERIAL_RX_BUFFER_SIZE` de 256 para 512. Este segundo **fecha o "ACK
  intermitente do SET_PAN_TILT"** que estava pendente: a mensagem ocupa 255
  bytes com enquadramento e o buffer tinha 256 - cabia exatamente uma, sem
  margem. Em rajada a segunda perdia bytes, o CRC falhava e o quadro era
  descartado em SILENCIO. Falta validar no hardware.
- **As 5 falhas de teste nao eram bugs de producao** (`007101f`): eram testes
  vindos do GitHub descrevendo versoes anteriores do codigo.
  - `test_conselheiro_comportamento` (3): o duble `_ClienteFalso` imitava o
    Ollama (`cliente.generate`), mas o conselheiro usa a API da OpenAI desde
    o EDR-0021. O `AttributeError` caia no `except Exception` e virava "IA
    indisponivel". Efeito colateral que so apareceu agora: os testes de
    FALHA **passavam por acidente** - esperavam `None` e recebiam `None`
    pelo motivo errado, sem testar nada.
  - `test_heartbeat_boot` (2): exigiam silencio eterno para quem nunca
    conectou. A versao atual da carencia de N falhas e so depois reporta -
    estritamente melhor. Entrou um teste novo provando que a carencia
    termina, parte que nenhum teste cobria.
  - **Nenhuma linha de codigo de producao foi alterada** para zerar as
    falhas: 431 passando no Notebook.
- **O Pi tinha uma QUARTA linha divergente**, criada depois de tudo que
  havia sido reconciliado: branch `fix-trig-echo-2026-07-26` com 2 commits
  que nao existiam em lugar nenhum (`790c41e` validacao fisica do ultrassom
  frente/tras, `c5419c7` saida do SAFE_MODE). Puxar o master por cima os
  teria apagado. Integrados por merge (`1ff4456`); unico conflito foi de
  COMENTARIOS no `pins.h` - os valores dos pinos ja eram identicos dos dois
  lados, porque a correcao do GitHub (20/07) e a validacao do Pi (26/07)
  chegaram ao mesmo resultado por caminhos diferentes. Resolvido preservando
  as DUAS evidencias (o teste de tapar o sensor e a leitura simultanea mais
  a cacada da fiacao TRIG/ECHO invertida).
- **Resultado: Windows, Notebook e Pi todos em `1ff4456`.** A divergencia
  aberta em 2026-07-19 acabou. Firmware compila (RAM 41%, Flash 17,1%),
  431 testes no Notebook e 387 (7 skipped) no Pi. Redes de seguranca:
  `backup-master-antes-merge-2026-07-27` (Notebook) e
  `backup-pi-antes-merge-2026-07-27` (Pi).
- **Dependencia faltando no Pi:** as 3 falhas que sobraram la eram
  `ModuleNotFoundError: No module named 'openai'` - o merge adicionou o
  pacote ao `pyproject` mas o venv do Pi nao tinha. Instalado; 0 falhas.
- **Saude do Pi apos desligamento abrupto de ontem a noite:** integro. O
  "letras subindo na tela preta" que o usuario viu era o ext4 se recuperando
  (`orphan cleanup on readonly fs`), o mecanismo funcionando. Nenhum
  `EXT4-fs error`, nenhum remount read-only, nenhum I/O error. 432GB livres.
- **Modulo desconhecido tirado de um drone, identificado:** e uma **bussola
  QMC6310** (I2C `0x1C`, CHIPID `0x80`), NAO uma IMU - nao tem giroscopio,
  acelerometro nem barometro. Testada numa WeMos D1 R1 a parte, lendo o campo
  terrestre com valor fisicamente correto (~25 uT de amplitude na
  calibracao, contra ~23 uT reais do Brasil). Projetos de teste em
  `~/Desktop/i2c_detetive` e `~/Desktop/bussola_orion`. Aprendizados que
  valem para a montagem no Fofao: motor DC/de passo perto distorce mais que
  o campo da Terra e a distorcao MUDA com a rotacao (montar longe, tipo
  mastro); a calibracao e especifica da instalacao, entao so vale a definitiva
  com tudo montado; e sem acelerometro nao ha compensacao de inclinacao - por
  isso ela deve dividir o barramento I2C com o MPU6050 (pinos 20/21 do Mega),
  que ja esta la.
- **EDR de odometria recebido do usuario** (encoder indutivo LM12-3004NA, 1
  por roda, 6 chapas com 1 larga de indice). **Erro grave encontrado na
  especificacao**: ela manda usar D2/INT0 e D3/INT1, que no `pins.h` sao
  `STEP_ESQUERDO` e `DIR_ESQUERDO` - seguir o documento quebraria a tracao.
  Correcao: **D18 e D19**, os unicos pinos de interrupcao livres do Mega
  (2/3 sao motor, 20/21 sao I2C) - e que o proprio `encoder_manager.h` ja
  reservava. Registrado tambem que 1 sensor por roda **nao mede sentido de
  giro**, so conta pulsos; o sentido segue inferido do comando de motor, como
  o proprio EDR admite na limitacao 1. Sentido real exige quadratura (2
  sensores por roda), que e a v2.0 do documento.
- **Proximos passos:** validar no hardware o buffer RX e o pan/tilt quando a
  parte eletrica for remontada; implementar `encoder_manager` de verdade em
  18/19; criar o `bussola_manager` para o QMC6310; os 3 bugs ALTOS ainda
  abertos (FOLLOW travando, SQLite concorrente, discovery com KeyError); e o
  `master` do GitHub, que segue em `73bf557` e ainda nao recebeu nada disso.

## 2026-07-27 (continuacao - bussola no barramento, dois bugs de sensor e o baud)

- **Sessao na maquina Windows de controle**, com SSH para o Pi e o Notebook.
  Firmware gravado 4 vezes no Mega pelo `gravar_firmware_seguro.sh`, sempre
  a partir do Pi (regra 2).

- **Odometria: decidido NAO usar o encoder indutivo.** O EDR de odometria
  (LM12-3004NA) fica de fora; a odometria continua sendo a contagem dos
  pulsos de STEP/PUL que o proprio firmware gera, assinados pelo pino DIR
  que ele mesmo comanda (`motor_manager.h`: `passosAcumulados +=
  sentidoFrente ? 1 : -1`). Decisao do usuario. Consequencia registrada: e
  malha aberta - conta o que MANDOU, nao o que a roda FEZ. Patinacao, roda
  travada ou fio de driver trocado passam despercebidos, e por isso a
  validacao externa (ultrassom + bussola) deixa de ser conferencia extra e
  passa a ser a unica checagem de realidade que o robo tem.

- **EDR-0024 escrito: varredura de horizonte.** Ideia do usuario. O robo
  gira em torno do proprio eixo em incrementos, e a cada parada registra
  rumo magnetico + distancia frontal + traseira. Dois produtos de uma
  varredura: `PASSOS_POR_GRAU` medido (graus da bussola contra passos
  emitidos) e um mapa polar local em coordenadas absolutas. Sacada do
  usuario que entrou no EDR: os dois ultrassons apontam a 180 graus um do
  outro, entao **girar 180 graus cobre os 360** - metade do giro, metade do
  erro. Gira-PARA-mede de proposito: motor de passo ligado distorce o campo
  magnetico e a vibracao estraga a leitura do ultrassom.

- **BUG REAL 1 - `sensor_ok` acusava sensor bom de mudo.** No
  `sensor_ultrassonico.h`, o caminho de sucesso (eco recebido) ligava
  `_leituraValida` e `_jaRespondeu` mas **nunca** `_sensorRespondeu` - esse
  so era ligado dentro de `_foraDeAlcance()`. Resultado: sensor funcionando
  perfeitamente reportava `sensor_ok: false` para sempre. Apareceu na
  telemetria como contradicao (frontal 173cm e traseiro 102cm, leitura real,
  os dois marcados como mudos). Corrigido e validado ao vivo com o usuario
  pondo a mao na frente: frontal 174 -> 61 -> 172cm, `sensor_ok: true` nos
  dois. **Confirma tambem que o sensor traseiro esta lendo** - duvida do
  usuario.
- **O sensor traseiro nao bloqueia o robo** (outra duvida do usuario):
  conferido no `SafetyManager::avaliar()`, ele so consulta `radar.*` (o
  frontal); o `ultrassomTraseiro` nem e passado para a funcao. Manobra de re
  ja estava livre.

- **BUG REAL 2 (parcial) - o valor inventado que o usuario suspeitava
  existir, existe.** `_foraDeAlcance()` reporta o teto de alcance (517cm)
  como leitura VALIDA quando nada reflete. Ninguem mediu 517cm. NAO foi
  removido: tratar silencio como obstaculo travava o robo numa sala vazia
  (medido em 2026-07-25). Em vez disso ganhou nome - campo `sem_eco` novo na
  telemetria (`frontal_sem_eco`/`traseiro_sem_eco`), porque de fora "517cm
  valido" era indistinguivel de uma parede realmente a 517cm. Quem montar
  mapa (EDR-0024) tem que tratar isso como DESCONHECIDO, nunca como LIVRE.

- **Bussola QMC6310 ligada de verdade no Mega** (`bussola_manager.h`, novo),
  portada de `~/Desktop/bussola_orion`. Mudou na portagem: sem
  `Serial.print` (serial e exclusiva do protocolo), calibracao NAO
  BLOQUEANTE (o `while` de 25s do original seria reset garantido pelo
  `wdt_enable(WDTO_2S)`) e EEPROM sem `commit()` (coisa de ESP8266).
  Compensacao de inclinacao reaproveita o acelerometro que a `ImuManager` ja
  le a cada 50ms - sem segunda leitura I2C. Calibracao na EEPROM em 64 (a
  IMU usa 0 e 32).
  - **Correcao propria durante a sessao:** a checagem exigia amplitude nos
    TRES eixos, herdada do projeto de bancada onde o modulo era girado na
    mao. Robo terrestre gira so em torno do eixo vertical e o Z fica
    constante - ela recusaria toda calibracao feita com o robo montado, que
    e justamente a que importa (o que se quer medir e o ferro do proprio
    Fofao). Passou a exigir so X e Y; Z so e corrigido se realmente girar.

- **Scanner de I2C no boot** (`escanearI2c()` em `main.cpp`, resultado na
  telemetria). Motivo: IMU e bussola apareceram mudas juntas e, sem enxergar
  o barramento, "modulo quebrado", "endereco diferente" e "barramento nao
  sobe" sao indistinguiveis - cada um pede conserto diferente. **Pagou-se no
  mesmo dia:**
  - Primeira hipotese minha estava ERRADA: achei que a bussola de 3,3V
    estivesse derrubando o barramento de 5V do Mega. Nao era - o usuario
    tinha soltado o fio do MPU.
  - Com o fio solto: `i2c_total: 0`, barramento vazio. Depois do conserto:
    `[0x1C, 0x68]`, os dois presentes.
  - **Achado bom:** o campo magnetico caiu de **71uT para 18,9uT** no
    instante em que o MPU voltou ao barramento. Os 71uT eram dado corrompido
    por barramento sem os pull-ups que a placa do MPU carrega, nao leitura
    real. 18,9uT ja e a ordem de grandeza certa (~23uT no Brasil); o resto da
    diferenca e o hard-iron, que so a calibracao remove.
  - Estado atual: `bussola_conectada: true`, `rumo_graus` lendo,
    `rumo_valido: false` porque ainda **nao foi calibrada** - correto, o
    driver se recusa a afirmar rumo confiavel antes disso.

- **Serial do Hardware Core: 115200 -> 250000 baud** (firmware +
  `communication.arduino.baud_rate`). O pacote de TELEMETRY passa de 600
  bytes e o buffer de saida do AVR e de 64, entao `Serial.write` bloqueia
  esperando drenar. A 16MHz, 250000 cai exato no gerador (UBRR=3, erro 0,0%)
  contra 2,1% do 115200 - a taxa mais alta e tambem a mais confiavel aqui.
  - **PREVISAO MINHA ERRADA, registrada de proposito:** previ o loop cair
    de 84ms para ~24ms. Medido: **84ms -> 68ms**. A serial era parcela, nao
    causa principal. Nao inventar explicacao para os ~40ms restantes;
    suspeito seguinte e o `Wire.setWireTimeout(25000)` sendo estourado nas
    leituras da bussola (2 transacoes x 25ms = 50ms, bate com a sobra).
    Teste barato proposto: baixar o timeout para ~3ms (transacao legitima
    nesse barramento leva menos de 1ms) e ver se o `loop_max` cai para perto
    de 30ms.
  - Por que isso importa: o eco de um obstaculo a 30cm dura 1,7ms. Com o
    loop em 68-84ms o robo fica **cego em rajadas**, e nenhum mapa por
    ultrassom se sustenta assim. Vem ANTES da varredura do EDR-0024.

- **Estado do git ao fim da sessao:** 3 commits no Windows (`06cf811`,
  `b92e46c`, `4063339`), arvore limpa. **Pi e Notebook NAO sincronizados** -
  o usuario desligou as duas maquinas antes do push. Nada se perdeu: os
  arquivos copiados por `scp` para o Pi sao identicos aos commitados. O Pi
  ficou com a arvore suja (esses mesmos arquivos) - ao religar, limpar
  (`git stash -u` ou `checkout --`) ANTES de puxar o master. GitHub segue em
  `73bf557`, agora 68 commits atras.

- **Proximos passos:** sincronizar as 3 maquinas; fechar os ~40ms de loop
  que sobraram (comecar pelo timeout do I2C); calibrar a bussola de verdade
  com o robo montado (`CALIBRATE_COMPASS`, girar 25s - o campo tem que ficar
  PARADO por mais que o robo gire, esse e o teste de qualidade); so entao a
  varredura de horizonte do EDR-0024; e os 3 bugs ALTOS ainda abertos
  (FOLLOW travando, SQLite concorrente, discovery com KeyError).

## 2026-07-27 (noite - interface web ganha corpo, e dois bugs achados por ela)

- **Bussola no barramento, enfim.** O scan de I2C do boot (feito na sessao
  anterior) pagou: com o fio do MPU solto deu `i2c_total: 0` - barramento
  VAZIO, nao modulo defeituoso. Apos o usuario refazer a ligacao,
  `[0x1C, 0x68]`. Estado atual: `bussola_conectada: true`, rumo lendo,
  `rumo_valido: false` porque ainda NAO foi calibrada.
  - **Correcao de uma conclusao minha errada:** eu tinha afirmado que a
    queda de 71uT para 18,9uT provava dado corrompido por falta de pull-ups.
    Nao e isso. O `campo_ut` e calculado sobre o valor CRU, entao ele mede
    "campo da Terra + offset do ferro" - e essa soma MUDA com a direcao. 18,9
    a rumo ~63 graus e 70,7 a rumo ~217 sao a assinatura classica de
    hard-iron grande, nao corrupcao. Offset estimado ~45uT contra ~26uT da
    Terra.
  - **O chassi de aluminio NAO e a fonte** (o usuario informou o material):
    aluminio nao e ferromagnetico. A fonte sao os imas permanentes dos
    NEMA17, que produzem campo mesmo desligados. Bom: offset fixo no
    referencial do robo = calibravel. Ruim: aluminio tambem nao BLINDA, entao
    o unico remedio contra o campo dos motores energizados e distancia
    (mastro).

- **BUG REAL - microfone: o robo ficou surdo SEM dar erro.** Sintoma: "IA nao
  responde, robo nao ouve". O log mostrava OpenRouter em 200 OK o tempo todo
  e ZERO linhas `ouvi (rms=...)`. Causa: a DV20 (webcam USB, que tem
  microfone) saiu durante a montagem e os indices do sounddevice mudaram pela
  TERCEIRA vez. Os candidatos `[1, 5, 0]` ficaram invalidos - so que o 5 e o
  `default` do PulseAudio, que EXISTE e aceita ser aberto mas nao captura
  nada. Sem excecao, sem crash-loop, servico `active`, robo surdo.
  **Muito pior de diagnosticar que a falha de 24/07**, que ao menos derrubava
  o servico. Corrigido para `[3, 4, 5]` (3 = USB Audio Device real) e o
  padrao das 3 ocorrencias documentado no proprio yaml.

- **Legenda no avatar**: `voice.response_started/finished` ja carregavam o
  texto desde sempre, sem nenhum consumidor. Nao reusa o balao de frase curta
  (some em 3,5s e resposta longa demora mais que isso) - a legenda persiste e
  quem a encerra e o fim real da fala, com 2,5s de folga para ler.

- **Interface web:** rosa dos ventos com seta vetorial (sem numeros, pedido
  do usuario); mapa virou radar 360 com o arco frontal REAL de 30-150 graus
  (era rotulado 0-180, prometendo cobertura que o servo nao alcanca) mais o
  cone traseiro fixo desenhado com a abertura do feixe do HC-SR04; senha na
  pagina CONFIGURACAO lida de `ORION_SENHA_CONFIG` em `config/secrets.env` -
  NUNCA do codigo, porque o repositorio e publico; e joystick de controle
  manual.
  - Regra adotada nas telas: **nunca mostrar valor inventado como medida.**
    `sem_eco` aparece como "sem eco" e nao como 517cm; rumo sem calibracao
    aparece em vermelho.
  - O `/comando` DESCARTAVA `velocidade_percent` - o firmware caia sempre em
    50% e o joystick seria inutil. Corrigido, limitado por
    `motion.max_speed_percent`.

- **Telemetria em dois regimes** (ideia do usuario): parado manda o pacote
  completo a cada 1s; andando, um pacote reduzido a cada 200ms. Motivo
  medido: o pacote completo bloqueia `Serial.write` por 74ms e nesse tempo o
  loop nao le o pino ECHO - o eco de um obstaculo a 30cm dura 1,7ms e cabe
  inteiro dentro da cegueira, fazendo o sensor concluir "caminho livre".
  **O regime de movimento ainda NAO foi validado**: o robo nunca se moveu.

- **Instrumentacao: duas hipoteses minhas morreram, registradas para ninguem
  repetir.** (1) Subir o I2C de 100k para 400kHz nao mudou nada (99,4 ->
  99,3ms), logo nao era tempo de transferencia. (2) Aqueles 99ms eram em boa
  parte ARTEFATO DO PROPRIO INSTRUMENTO - o timer envolvia a chamada da
  bussola, que roda em toda volta do loop, somando o custo de se medir. Com
  contadores separados: envio 74ms, IMU 3,6ms/leitura, bussola 3,0ms/leitura.
  **O bloqueio e a telemetria, sozinha.**

- **PENDENTE - achado do usuario no fim da sessao, nao corrigido:** o rele
  dos motores (pino 12) fecha e o firmware pulsa STEP quase no mesmo
  instante (`_configurarRoda` faz `ultimoPassoUs = micros()`). Mas o contato
  do rele quica (~5-10ms) e o TB6600 leva alguns ms para acordar - entao os
  primeiros passos de CADA movimento sao emitidos e nao executados, e o
  firmware conta todos. Erro sistematico, sempre no mesmo sentido, pior que
  patinacao. Correcao proposta: adiar o primeiro pulso em ~20ms apos
  `_ligarRele()`, sem `delay()`. Medir depois comandando 1000 passos e
  conferindo com o ultrassom contra a parede.

- **Joystick nao moveu o robo** no primeiro teste (rodas no ar): `estado`
  ficou em IDLE, `em_movimento: false`, nenhum pacote reduzido em 40s, e a
  seguranca NAO estava bloqueando (frente livre a 174cm). O comando nao
  chegou ao Mega. Diagnostico rapido para a proxima: o rele do pino 12 clica
  ao arrastar o joystick? Se NAO clica, o problema e antes do Mega
  (navegador / `/comando` / TCP / serial); se clica e a roda nao gira, e
  alimentacao ou fiacao.

- **Proximos passos:** atraso do rele (afeta toda a odometria); descobrir por
  que o joystick nao moveu; tela dormindo + acordar por voz (e baixar a
  cadencia da visao, que pesa mais na bateria que o backlight); calibrar a
  bussola de verdade (exige motores); chat de duas vias; botao de desligar;
  e o ar-condicionado Tuya/SmartLife via `tinytuya` local, esperando a
  `Local Key` do iot.tuya.com - com carencia de 3 min no religamento, porque
  compressor nao pode partir logo apos desligar.

## 2026-07-27 (vistoria das 3 maquinas + arquitetura em camadas do 1.0)

- **Sessao na maquina Windows de controle**, por SSH no Pi (`10.20.20.196`) e no
  Notebook (`10.20.20.195`). Nenhuma linha de codigo de producao alterada -
  sessao de leitura, medicao e decisao de arquitetura.

- **As 3 maquinas continuam iguais.** Pi e Notebook os dois em `c618237`,
  `master`, arvore limpa. Medido, nao suposto: **424 testes passam no Notebook**;
  **380 passam e 7 pulam no Pi**. Os 7 pulados sao `numpy`, `cv2` e `ollama`
  ausentes - esta CERTO, visao e voz rodam no Notebook (regra 4). Firmware
  compila: **RAM 45,5%, Flash 19,3%** (a bussola custou ~4,5 pontos de RAM
  contra os 41% de 26/07).

- **Tres pendencias "ALTAS" do journal ja estavam mortas.** Fui conferir no
  codigo antes de caca-las:
  - `discovery` com KeyError - `discovery.py:53` ja usa `.get()` com default;
  - SQLite concorrente - `database.py:53` ja tem `asyncio.Lock()` compartilhado
    mais modo WAL;
  - FOLLOW travando - `navigation_core.py:403-422` ja tem `try/except/finally`
    e sai do modo mesmo se o envio falhar.
  Licao: pendencia em journal envelhece. Conferir no codigo antes de abrir
  sessao de conserto.

- **JOYSTICK: sao DOIS problemas, nao um.**

  **(1) O comando e aceito, confirmado e jogado fora em silencio.** O firmware
  manda o ACK ANTES de executar (`main.cpp:272-273`). Depois,
  `command_executor.h:74` recusa todo comando de movimento enquanto
  `safety.pararAtivo()` - e essa recusa nao avisa ninguem. O navegador recebe
  `200 OK`, o joystick nao mostra erro, o robo nao anda. **O `/comando` da webui
  tambem nao registra nada em log** - nao ha rastro de comando manual nenhum.
  - A seguranca ESTAVA ativa: o `motion_core.log` tem **423 ciclos** de
    "obstaculo a frente" / "obstaculo liberado", perfeitamente pareados, e
    **ZERO varreduras** - entao nao e o ponto cego do SCAN_FRONT.
  - **Hipotese (nao medida ainda, mas testavel barato):** o driver do HC-SR04 le
    o pino ECHO por sondagem. A media do loop e 79us (otima), mas o envio da
    telemetria BLOQUEIA o loop por ~24ms a cada 500ms. Se essa cegueira cair no
    meio de um eco, o codigo perde a borda de SUBIDA e marca `_inicioEchoUs`
    atrasado (`sensor_ultrassonico.h:38`) - a duracao medida sai CURTA DEMAIS e
    174cm viram poucos centimetros, disparando `dist < 25`. O driver nao tem como
    saber que perdeu a borda.
  - **Correcao proposta:** comparar `_inicioEchoUs` com o instante do trigger e
    DESCARTAR a leitura se a subida veio tarde demais (o HC-SR04 sobe em ~460us;
    limite de ~1ms e seguro). Coerente com a regra ja adotada nas telas: nunca
    tratar valor inventado como medida.

  **(2) O modo manual nao existe de verdade** (achado a partir da observacao do
  mantenedor de que o joystick e do modo manual e que nos demais o maestro deve
  assumir). O `/comando` chama `self._comm.send("hardware_core", payload)` DIRETO
  (`webui/server.py:271`) e pula inteiramente o `NavigationCore.executar_manual()`
  - que existe (`navigation_core.py:257`) e faz o certo: muda o modo para MANUAL,
  checa obstaculo na camada tatica e espera o robo ficar ocioso antes de empilhar
  movimento. Consequencia: **o modo nunca vira MANUAL e o maestro nao sabe que
  alguem assumiu o controle.** Conferidas as prioridades - nao existe
  comportamento "manual" nenhum: vigilancia_obstaculo 100, atender 80, vigilia 60,
  ia_estrategica 40, mentor 35, ronda 20, repouso 10. Falta um comportamento
  manual de prioridade alta (abaixo so da seguranca) que preempte os autonomos
  enquanto o joystick estiver em uso e devolva o controle ao solta-lo.

- **Hipotese minha DESCARTADA, registrada para ninguem repetir:** suspeitei que a
  geracao de passos estivesse limitada pela frequencia do loop (`motores.atualizar()`
  roda 1x por volta e emite no maximo 1 pulso por roda). Com loop de 68-84ms isso
  daria ~15 passos/s contra os 2000 pedidos. **Errado:** os 68-84ms sao o PICO
  (`loop_max_us`); a MEDIA e 79us, que da teto de ~12.600 passos/s. Nao e gargalo.
  O que sobra e gagueira: durante os ~24ms da telemetria nenhum passo sai, entao a
  velocidade real fica um pouco abaixo da comandada - mas a distancia do
  MOVE_DISTANCE nao se perde, porque o firmware conta os passos EMITIDOS.

- **Arquitetura em camadas do 1.0 recebida do mantenedor** (Kernel / Core / API /
  Desktop), mais uma cadeia de boot e uma tela de inicializacao. Registrada como
  **EDR-0025**. Decisoes:
  - **MQTT fora, definitivamente** (decisao do mantenedor: "ja temos uma
    comunicacao criada que funciona"). Ele resolveria transporte de mensagens -
    problema que `src/orion/communication/` ja resolve com CRC16 validado byte a
    byte nas duas linguagens, ACK com 3 retransmissoes e heartbeat, e sem exigir
    broker no ar (que empurraria contra o "100% offline"). Consequencia: a tela de
    inicializacao **nao mostra `✓ MQTT`** - um ✓ verde num subsistema inexistente
    e a mesma falha do "517cm valido", na tela mais visivel do robo.
  - **ROS2 fora do 1.0**: traz event bus e transporte proprios, que o ORION ja
    tem. Se entrar um dia, entra como ponte OPCIONAL na camada API.
  - **"Orion Boot" e servico de aplicacao, nao bootloader.** No diagrama ele
    aparecia entre a BIOS e o kernel do Linux - nessa posicao so cabe um GRUB, e
    escrever um seria outro projeto sem ganho para o robo. O `boot.py` que ja
    existe roda depois do Linux. A sensacao de "ORION OS inicializando" sai de um
    tema de Plymouth mais os `.service` que ja estao em `tools/`.
  - **A splash e dirigida por evento real**: cada ✓ acende quando o modulo
    registra no Service Registry; modulo que falha aparece em vermelho em vez de
    sumir. Barato, porque `boot.py` e o `ServiceRegistry` ja publicam isso.
  - **Colisoes de nome a resolver**: "Mission Core" e o Notebook inteiro
    (EDR-0018/0019) E um modulo do Core; "Vision Core" rotula o Notebook E e um
    arquivo. Os nomes de hardware sao lei e nao mudam - modulo se cita pelo
    arquivo, maquina pelo papel.
  - **`src/orion/memory/` sai**: contem so um `__init__.py` de 0 byte; a memoria
    real vive em `motion_core/memory/`, no Pi (regra 5). O stub manda quem segue o
    diagrama para o lugar errado.

- **Proximos passos:** (1) modo manual de verdade - e o que destrava o teste do
  joystick; (2) descarte de leitura com borda perdida no HC-SR04, que para o falso
  obstaculo; (3) atraso do rele (~20ms apos `_ligarRele()`), que afeta toda a
  odometria; depois calibrar a bussola com o robo montado e so entao a varredura
  do EDR-0024. GitHub segue em `73bf557`, agora bem atras.

## 2026-07-27 (gravacao no Mega + teste no hardware do joystick + bug real de desligamento)

- **Sessao na maquina Windows de controle.** Aplicado o fluxo de 7 etapas
  pela primeira vez de ponta a ponta: firmware gravado no Mega
  (`tools/gravar_firmware_seguro.sh`, RAM 46,3%/Flash 19,4%, compilando e
  verificando limpo), depois testes no hardware de verdade contra o
  `orion-motion.service` real - nao simulado.

- **Novo `tools/testar_joystick.py`**: bate no HTTP da webui (mesmo caminho
  do joystick do navegador), em fases de risco crescente - LIGHT_ON/OFF
  primeiro (prova o pipeline inteiro sem tocar motor nenhum), STOP depois,
  giro real so com `--com-motor` explicito. **LIGHT_ON/OFF/STOP confirmados
  funcionando ponta a ponta em producao**: log real mostra
  `maestro: 'controle_manual' assume (prio 95)` - a primeira prova de que o
  `ControleManual` desta sessao funciona com o Mega de verdade, nao so em
  teste unitario. Giro real com motor NAO testado ainda - decisao consciente,
  sem confirmacao visual de que o robo estava em posicao segura (rodas no ar
  ou espaco livre).

- **BUG REAL GRAVE encontrado: `systemctl --user restart orion-motion.service`
  travava por ~90s, toda vez**, preso em `deactivating (stop-sigterm)` ate o
  systemd desistir do SIGTERM e mandar SIGKILL (sem `TimeoutStopSec`
  configurado, o padrao aplica). Reproduzido e resolvido em tres camadas,
  cada uma provada com teste antes de seguir para a proxima (desativando a
  correcao, confirmando que o teste FALHA, reativando):

  1. **`SerialTransport.fechar()`** (`transport.py`): o `close()` da porta
     entrava na fila do MESMO executor de 1 thread que `receber()` usa para
     o `read()` bloqueante (pyserial e sincrono) - se o read() estivesse em
     andamento, `close()` ficava preso atras dele. Corrigido com
     `self._serial.cancel_read()` (API do pyserial feita exatamente pra
     interromper um read() de outra thread) ANTES do close(). Teste novo em
     `test_transport.py` reproduz com um pty sem ninguem escrevendo.

  2. **`ComunicacaoService.encerrar()`** (`service.py`): cancelava e
     ESPERAVA a tarefa de recepcao terminar ANTES de fechar o transporte -
     ordem que so funciona pra TCP (`StreamReader.read()` e nativo do
     asyncio, cancelavel de verdade). Este mesmo arquivo ja documentava a
     licao no simulador (`test_fase2_comunicacao.py`, comentario de
     `arduino_simulado`: "um read() bloqueante numa thread do executor nao e
     interrompido por task.cancel()") mas nunca tinha sido aplicada ao
     encerrar() de producao. Corrigido invertendo a ordem: fecha os links
     primeiro, so depois cancela+espera as tarefas. Teste novo de integracao
     (`test_encerrar_nao_trava_com_leitura_serial_pendente`) com pty real e
     timeout de leitura de 5s.

  2b. **Achado honesto, registrado para ninguem repetir:** o teste de
      integracao do item 2, isolado, passou mesmo com a correcao revertida -
      o `cancel_read()` do item 1 sozinho ja bastava naquele cenario de
      teste. A necessidade da correcao 2 em ISOLAMENTO ficou tecnicamente
      NAO PROVADA por teste (so a 1 e a 3 tem prova direta de deteccao).
      Mantida mesmo assim por ser ordem mais robusta objetivamente (fechar
      recurso antes de esperar quem o consome), sem custo.

  3. **A causa real e dominante: `WebUIServer._handler_sse` / `encerrar()`**
     (`webui/server.py`). O handler de `/eventos` (SSE, usado pelo dashboard
     no navegador) e um `while True` de proposito, pra manter a conexao viva
     enquanto o cliente quiser. `encerrar()` so chamava
     `AppRunner.cleanup()`, que ESPERA os handlers em andamento TERMINAREM
     SOZINHOS antes de retornar - com um cliente de verdade conectado (o
     proprio dashboard aberto numa aba), esse handler nunca termina sozinho.
     **Esta e a causa dominante do travamento de 90s**, confirmada de forma
     definitiva: um teste unitario com `TestClient`/`TestServer` NAO
     discriminou o bug (a ferramenta de teste nao reproduz fielmente o
     `cleanup()` do `AppRunner`+`TCPSite` real de producao) - so o teste com
     hardware/producao real provou. Corrigido com um `asyncio.Event`
     (`self._encerrando`) que o loop do SSE escuta via `wait_for` no lugar
     de um `sleep` puro - acorda na hora quando `encerrar()` sinaliza, em
     vez de esperar o proximo ping (15s) ou o cliente desconectar sozinho.

  - **Medido, nao suposto:** com um cliente SSE real conectado (`curl -sN`
    simulando a aba do navegador), `systemctl restart` foi de **~91s para
    1s**. Confirmado no log ponta a ponta: a sequencia completa de
    desligamento (`webui.encerrar` -> `comm.encerrar` -> `servidor_tcp.close`
    -> cancelar+esperar heartbeat/maestro/event_bus/ia_estrategica/manutencao)
    ate `Motion Core encerrado`, sem travar em lugar nenhum.

  - **Impacto real:** qualquer sessao anterior que reiniciou o servico com o
    dashboard aberto numa aba do navegador sofreu este travamento sem saber
    a causa - so parecia "o Pi demora pra religar o servico". Ninguem tinha
    percebido porque as sessoes anteriores nao tinham motivo pra medir o
    tempo de restart com precisao.

- **Contadores `frontal_descartes`/`traseiro_descartes` (correcao de
  sensor_ultrassonico.h da sessao anterior) confirmados vivos em producao**:
  27-29 descartes frontais e 10 traseiros acumulados em poucos minutos de
  uptime - a hipotese de que o envio da telemetria cega o loop durante o eco
  do ultrassom, com frequencia suficiente pra importar, esta CONFIRMADA por
  dado real, nao mais so por raciocinio. (Achado colateral desta mesma
  sessao: os dois campos nao apareciam no `/estado` porque a webui tinha uma
  lista fixa de campos repassados da telemetria e eu tinha esquecido de
  adicionar os dois novos nela - corrigido tambem.)

- **Metodologia que vale registrar:** a ferramenta de teste unitario
  (`aiohttp.test_utils.TestServer`/`TestClient`) NAO reproduz fielmente o
  comportamento de desligamento do `AppRunner`+`TCPSite` real. Um teste que
  passa com `TestClient` nao prova que o mesmo cenario esta resolvido em
  producao - only o teste contra o servico rodando de verdade (ou contra
  hardware real) e prova final. Ficou provado ao vivo nesta sessao: o mesmo
  teste "passava" com a correcao ativada E desativada quando rodado via
  `TestClient`.

- **Estado do git ao fim da sessao:** Pi e Notebook sincronizados,
  arvore limpa, `docs/ESTADO_DOS_MODULOS.md` atualizado com a validacao de
  hardware de hoje (ainda sob o teto de politica: nada vira verde ate o robo
  se mover de verdade).

- **Proximos passos:** testar o giro real do joystick (`--com-motor`), com o
  robo em posicao segura e alguem observando; calibrar a bussola com o robo
  montado; camera do pan/tilt segue sem transmitir; varredura de horizonte
  do EDR-0024, que depende dos itens anteriores.

### Fechamento da sessao de 2026-07-27 (noite) + missao pronta para amanha

- **Giro real testado com o robo** (`tools/testar_joystick.py --com-motor`,
  usuario confirmou posicao segura): `TURN_LEFT` a 25% por ~0,4s seguido de
  `STOP`, aceito e executado - log confirma o `ControleManual` assumindo o
  maestro a cada comando (`'controle_manual' assume (prio 95)`) e devolvendo
  o controle sozinho depois. **Confirmacao visual de que o robo girou de
  verdade ainda pendente** - o teste prova o pipeline (HTTP -> Event Bus ->
  maestro -> NavigationCore -> serial -> Mega -> ACK), nao o movimento
  fisico em si.
- **Sessao encerrada com Pi e Notebook sincronizados em `1803624`**, arvore
  limpa nas duas, `orion-motion.service` ativo e saudavel no Pi (restart
  agora leva ~1s, era ~90s antes da correcao desta sessao).
- **Limpeza de fim de sessao:** removidos os arquivos de diagnostico
  temporarios que a investigacao do travamento deixou em `/tmp` do Pi
  (`sse_out.txt`, `strace_out.txt`, `st.txt` - da tentativa de `strace` que
  nao teve permissao de ptrace). Nada do codigo do projeto foi tocado nesta
  limpeza, so arquivos soltos fora do repositorio.

#### Missao de amanha (em ordem, cada item depende do anterior)

1. **Confirmar visualmente o giro do item acima.** Se o robo girou de
   verdade na direcao certa (TURN_LEFT = sentido anti-horario visto de
   cima), fechar o item como validado no hardware (mapa de saude:
   `controle_manual`/`webui /comando` sobe de "giro nao testado" para
   "giro confirmado"). Se nao girou ou girou errado, e um bug novo -
   comparar com o firmware (`command_executor.h`, TURN_LEFT/TURN_RIGHT)
   antes de qualquer outra coisa.
2. **Testar translacao real** (`MOVE_DISTANCE` para frente/re, ou o
   joystick de verdade no navegador) - so giro foi testado ate agora. Rodas
   no chao ou no ar, dependendo do que for seguro medir (contagem de
   passos vs distancia real, por causa da carencia do rele desta sessao).
3. **Observar os contadores `frontal_descartes`/`traseiro_descartes`**
   por uma janela mais longa (a sessao de hoje so confirmou que crescem -
   27/07 22h: 29/10; depois do teste de giro: 1789/1171). Correlacionar
   com o log de `vigilancia_obstaculo`: se os ciclos de obstaculo falso
   (423 registrados antes da correcao) sumirem ou cairem muito, a hipotese
   do EDR desta sessao (cegueira do loop durante o envio da telemetria)
   fica confirmada de vez.
4. **Calibrar a bussola** (`CALIBRATE_COMPASS`) com o robo montado - exige
   girar o robo em torno do proprio eixo por ~25s continuos, so possivel
   depois do item 2 confirmar que o movimento comandado bate com o
   fisico.
5. **Investigar a camera do pan/tilt** que nao transmite (pendente desde
   26/07, sem diagnostico ainda - nem se e cabo, driver, indice errado ou
   coisa fisica).
6. **So depois de 1-4:** varredura de horizonte do EDR-0024 (precisa de
   giro confiavel + bussola calibrada + os dois ultrassons lendo sem
   distorcao do proprio movimento).
7. **Continuar o mapa de saude** (`docs/ESTADO_DOS_MODULOS.md`): os modulos
   de infraestrutura (comunicacao, webui, joystick) ganharam evidencia real
   forte nesta sessao; os modulos de percepcao/navegacao real ainda nao
   foram tocados por teste de hardware nenhum - e onde a proxima rodada
   deveria focar.

**Regra para amanha, reforcada por esta sessao:** todo restart de servico
durante teste deve ser cronometrado (`date +%s` antes/depois) - foi assim
que o bug do desligamento apareceu. Um restart que demora mais que uns
poucos segundos NAO e normal, mesmo que systemd eventualmente resolva
sozinho.


## Sessao de 2026-07-28 - camera do pan/tilt resolvida + FOLLOW so reconhecidos

Retomando o bloqueio deixado em 26/07 ("a camera do pan/tilt nao transmite").

**Diagnostico:** hardware da DV20 estava bom (`v4l2-ctl` capturava frames
MJPEG reais direto do `/dev/video0` no Pi) - o problema real e novo, ainda
nao documentado antes de hoje: **o cabo da DV20 so alcanca fisicamente o
Raspberry**, mas o Vision Core (Cap 8) roda no Notebook. Antes disso nunca
tinha sido confirmado ponta a ponta com os dois presos na mesma bancada.

**Solucao: stream de rede em vez de indice local.** Novo servico
`orion-camera-stream.service` no Pi (`tools/orion-camera-stream.service`,
`systemctl --user`) - ffmpeg le o MJPEG nativo da DV20 via
`/dev/v4l/by-id/usb-Jieli_Technology_USB_Composite_Device-video-index0`
(por-id, nao `/dev/video0` - mesma licao do CH340 mudando de porta) e
serve HTTP multipart (`-f mpjpeg -listen 1`, porta 8091) sem recodificar
(`-c:v copy`). `config/orion.yaml` (`vision.camera_indice_principal`) virou
a URL (`http://pi-os.local:8091/stream`) em vez do indice `2` -
`cv2.VideoCapture` aceita os dois formatos, entao `captura.py`/
`vision_core.py` so precisaram soltar o type hint pra `int | str`.

**Latencia medida (nao suposta):** ~36 fps efetivo lendo o stream, gap
medio 28ms entre frames, sem acumulo ao longo de 8s continuos - dentro da
margem do `fps: 15` configurado e do rate-limit de 150ms do
`_mover_servos`.

**Validado com pessoa real, nao so com script:** YOLO detectando ate 0.96
de confianca; pan/tilt calculado bateu com a posicao real na imagem
(pessoa a direita do centro -> pan positivo, testado e fotografado);
comandos `SET_PAN_TILT` chegando no Arduino e o servo fisico girando de
verdade acompanhando a pessoa (log temporario confirmou envio bem
sucedido, sem falha de ACK).

**Achado colateral grave - `FOLLOW` (andar atras de alguem) estava morto
em producao:** `NavigationCore._ao_detectar_pessoa` esperava
`evento.dados["centro_x"]` (0..1), mas `vision.person_detected` nunca
publicou esse campo (so `caixa`) - o `.get(..., 0.5)` sempre caia no
default "centralizado", entao o robo NUNCA girava atras de ninguem em modo
FOLLOW, mesmo com o Vision Core funcionando perfeitamente. O teste
existente (`test_follow_gira_em_direcao_a_pessoa_descentralizada`) nunca
pegou isso porque publicava um evento sintetico com `centro_x` direto,
sem passar pelo VisionCore real - dublê e produtor real tinham contratos
diferentes.

**Corrigido, e junto com um pedido explicito do usuario:** o robo so deve
ANDAR atras de gente que ele RECONHECE, nunca de qualquer pessoa
detectada (a camera pode olhar/seguir qualquer um, isso e seguro; as
rodas nao). `VisionCore.processar_um_frame` agora inclui `centro_x`
normalizado no payload de `vision.person_recognized` (que so publica
quando `alvo.pessoa_id is not None`), e `NavigationCore` trocou a
assinatura de `vision.person_detected` para `vision.person_recognized`
(`_ao_reconhecer_pessoa`). Teste novo
(`test_follow_ignora_pessoa_nao_reconhecida`) cobre o gating.

**Mitigacao de falso positivo do YOLO:** observado ao vivo o pan/tilt
saltando pra um angulo sem ninguem na frente ("angulo aleatorio").
Hipotese: deteccao espuria de um unico quadro virando alvo imediatamente.
`Rastreador` ganhou `frames_confirmacao` (default 1, sem mudar
comportamento de quem nao passar o parametro) - so promove uma deteccao
nova a alvo depois de N quadros seguidos na MESMA regiao (IoU > 0.5).
`ModuloVisao` liga isso com `frames_confirmacao_alvo=3`. Nao reproduzido
de novo apos a correcao, mas a causa raiz exata (qual objeto/sombra virou
"pessoa") nao foi identificada - se voltar a acontecer, logar a
`caixa`/confianca da deteccao fantasma ajudaria a achar o objeto real.

**Estado final:** 444 testes passando no Notebook, 400 passando + 7
skipped no Pi (mesma suite, itens que exigem hardware especifico do Pi).
Mudancas feitas nas DUAS maquinas (Notebook e Pi tem copia do monorepo),
AINDA NAO COMMITADAS em nenhuma das duas - ficou pendente pra quem revisar
antes.

**Pendencia para a proxima sessao:** achar a causa raiz do falso
positivo do YOLO (hoje so mitigado, nao eliminado); confirmar
visualmente que o FOLLOW anda de verdade atras de pessoa reconhecida
(hoje so testado por unidade, nao na bancada com o robo se movendo);
commitar as mudancas de hoje.


## Sessao de 2026-07-28 (continuacao) - "o pan/tilt nao segue ninguem": tres fios soltos, a mesma familia de bug

Sintoma do usuario, cru: *"o tilt e pan nao ta seguindo ninguem, simplesmente
nao funciona"*. E, depois de meia correcao: *"ele ate parece funcionar, depois
se perde"*. As duas frases descrevem bugs DIFERENTES - foi preciso separar.

Nada disso aparecia em teste: 412 testes passavam no inicio e no fim da
sessao, sem um unico vermelho. Os tres bugs sao **da mesma familia**:
alguem publica um evento e ninguem escuta - e publicar evento NAO FALHA.
Esta e a terceira vez que este sistema e mordido por isso (as anteriores:
guardiao de RAM ate 19/07, pan/tilt da visao ate 25/07). Vale como padrao a
procurar, nao como acidente.

**Bug 1 - `ffmpeg -listen 1` servia UM cliente e morria.** O servico da
camera criado mais cedo hoje media "36 fps, gap 28ms" - medicao real, porem
de UMA conexao continua de 8s. Na operacao o Vision Core reconecta, e a
PRIMEIRA reconexao derrubava o ffmpeg:

    Error submitting a packet to the muxer: Connection reset by peer
    Task finished with error code: -104

`Restart=always` subia outro, que reabria a camera do zero; depois de alguns
ciclos rapidos a Jieli travava no meio da abertura (`VIDIOC_QBUF: Bad file
descriptor`, 17 vezes no log) e o processo ficava VIVO segurando
`/dev/video0` **sem nunca abrir a porta 8091**. `systemctl` dizia `active`
com a porta morta. A camera nunca teve defeito - fora do servico ela
capturava 30 quadros e saia com codigo 0.

Trocado por **ustreamer** (`apt install ustreamer`), feito para este uso:
mantem o dispositivo aberto, aceita cliente entrando e saindo quantas vezes
for, nao recodifica, e serve em `/stream` - o mesmo caminho, entao o
`orion.yaml` nao mudou. Validado com 3 conexoes seguidas com desconexao no
meio e pela rede a partir do Notebook (1,89MB / reconexao 1,91MB). Depois,
**em producao**, o log registrou um `Disconnected ... clients now: 0`
seguido de `Registered client ... clients now: 1` - exatamente o evento que
matava o ffmpeg, agora sobrevivido.

**Bug 2 - `motion.pan_tilt` nao tinha assinante no Pi.** Os comportamentos
publicavam no vazio: `Repouso` manda `pan=0,tilt=0` ("cabeca nivelada") e
`Ronda`/`observar_ambiente` varre `-60 -> +60 -> 0`. Nenhum desses movimentos
NUNCA aconteceu. O log ainda mentia junto - `Repouso` escreve "cabeca
nivelada" com `logger.info` ao lado do publish que ninguem consumia.

Consequencia que fechou o circulo: com a camera apontada para uma parede o
robo **nao tinha como se desvirar**. O unico emissor real de `SET_PAN_TILT`
era o Vision Core do Notebook, que so manda quando JA rastreia alguem - e
nao via ninguem por estar olhando para a parede. A varredura que existia
para resolver isso publicava no vazio.

Ligado em `motion_core/__main__.py` com o mesmo limitador de taxa do lado da
visao (150ms / 1 grau) e pelo mesmo motivo medido: um comando por quadro
afoga a serial e atrasa os heartbeats. **A falha vai em WARNING, nao em
debug** - de proposito: foi um `logger.debug` que escondeu esta classe de
falha do lado da visao. Prova fisica: foto do stream antes (parede branca
lisa) e depois do restart (o comodo, prateleira e janela) - o servo girou.

**Bug 3 - eventos de visao nunca atravessavam para o Pi.**
`tools/conversar_fofao.py` encaminhava so 4 topicos (`TOPICO_RESPOSTA`,
`voice.wake_detected`, `voice.response_finished`, `sentinela.alerta`).
`vision.person_detected` e `vision.person_lost` nao. Mas o `EstadoDoMundo`
do Pi ASSINA os dois - ou seja, `pessoa_presente` era sempre falso e o
conselheiro de IA decidia como se a sala estivesse permanentemente vazia.

**Custo metodologico deste bug, registrado para ninguem repetir:** usei
`estado.visao.ultima_deteccao` como sonda para saber se o YOLO estava vendo
o usuario, e pedi que ele ficasse na frente da camera com base nela. A sonda
media um canal morto - `None` ali nunca significou "nao detectou". Sonda que
nunca mudou de valor nao e sonda; antes de usar uma, provar que ela SABE
mudar.

Encaminhado, **estrangulado a 1/s de proposito**: `vision.person_detected` e
publicado uma vez POR PESSOA POR QUADRO (ate 15x/s) e despejar isso no link
TCP repetiria o erro que a telemetria completa cometeu com o ultrassom
(27/07). `vision.person_lost` nao e estrangulado - ja e unico, e atrasar
"perdi" e pior que atrasar "ainda esta aqui".

**Bug 4 (o "parece funcionar e se perde") - dois comandantes, zero
arbitragem.** Com o bug 2 corrigido, `Repouso` passou a recentrar a cabeca
DE VERDADE toda vez que assume - e o maestro volta ao repouso a cada
preempcao da vigilancia de obstaculo, que pisca sozinha com qualquer coisa a
frente. A visao centralizava na pessoa, o repouso arrancava a cabeca de
volta para 0,0, a pessoa saia do quadro, alvo perdido. A visao comandava o
servo DIRETO do Notebook, por fora do maestro; os comportamentos comandavam
pelo barramento do Pi. Ninguem arbitrava.

Novo comportamento **`AcompanharPessoa` (prio 45)**: nao comanda pan/tilt -
so SEGURA o controle enquanto a visao rastreia, impedindo outro
comportamento de mexer na cabeca. 45 fica acima do discricionario (ronda 20,
mentor 35, IA estrategica 40 - esta ultima varre -60/+60 em
`observar_ambiente`, o que arrancaria a camera de cima da pessoa) e abaixo
do que deve vencer: vigilia 60, atender 80, manual 95, seguranca 100.

Tem **timeout proprio de 3s** e isso e essencial: `vision.person_lost` so e
publicado se o rastreador chegou a CONFIRMAR um alvo (3 quadros), entao uma
deteccao que nunca confirma nao gera "perdi" nenhum - sem o timeout o
comportamento seguraria a cabeca para sempre e o `Repouso` nunca mais
nivelaria. Seria trocar um bug por outro pior.

**Uma hipotese minha morreu, registrada para ninguem repetir.** Vendo o
centro da caixa ir 132 -> 176 -> 429 -> 459 -> perdido, afirmei que o laco
de controle passava do ponto (ganho alto ou eixo invertido). Errado: o
usuario estava ANDANDO porque eu tinha pedido, e o movimento dele estava
misturado ao erro do controle. Medido depois com ele parado: `centro_x=317`
(erro -3) e `centro_x=290` (erro -30) num quadro de 640. **O controle
centraliza.** Regra: nao diagnosticar laco de controle com o alvo em
movimento.

**Estado ao final:** cadeia inteira viva pela primeira vez - camera -> YOLO
no Notebook -> evento atravessa -> maestro entrega a cabeca a visao -> servo
centraliza a pessoa. Log de producao com `conf=0.91`, `'acompanhar_pessoa'
assume (prio 45)` e o timeout soltando a cabeca sozinho. 412 testes passam.

**PENDENTE:**

- **Obstrucao fisica (nao e software).** A camera aponta para o verso de um
  monitor a poucos centimetros - metade do quadro e um painel preto e a
  pessoa so entra numa fresta. E a causa da deteccao vir em rajadas. Nenhum
  ajuste de codigo conserta lente bloqueada; precisa reposicionar a bancada.
- **`Send-Q` com 259KB entupidos** na conexao 8091 Pi->Notebook: o Vision
  Core nao drena o stream na velocidade que ele chega, entao processa quadro
  velho. Nao bloqueia, mas explica latencia e merece medicao (a carga do
  Notebook estava em 2,87 com whisper + YOLO + chamadas ao OpenRouter).
- **`AcompanharPessoa` nao tem teste unitario.** Coberto so por teste em
  campo - exatamente o buraco que mordeu o FOLLOW ontem (o dublê nao
  refletia o produtor real).
- Continua valendo do inicio do dia: causa raiz do falso positivo do YOLO
  (hoje so mitigado com 3 quadros de confirmacao).


## Sessao de 2026-07-28 (parte 3) - o pan/tilt SEGUE pessoa pela primeira vez

Fecha o "nao segue ninguem" das partes 1 e 2. Foram CINCO bugs empilhados, e
nenhum aparecia nos 456 testes - cada um sozinho ja bastava para quebrar o
seguimento, o que explica por que a descricao do usuario era simplesmente
"nao funciona".

**Medicao final, com a pessoa parada:**

    10:22:26  erro=  -1   conf=0.97
    10:22:27  erro=  -2   conf=0.98
    10:22:28  erro=  -1   conf=0.98
    ... oito leituras seguidas a +-2px do centro num quadro de 640

E 25 deteccoes novas em 32s. Na medicao imediatamente anterior, mesmo teste:
UMA. O laco da visao estava sufocado, nao a deteccao.

### Bug 4 - meu proprio cache mentindo sobre o hardware

A primeira versao do assinante de `motion.pan_tilt` (parte 2) descartava o
comando quando pan/tilt nao tinham mudado 1 grau **em relacao ao ultimo que o
PI mandou** - copiei do lado da visao, onde faz sentido porque la o mesmo
emissor manda tudo. Aqui nao: quem move o servo na maior parte do tempo e o
Vision Core, DIRETO do Notebook, sem passar por este barramento.

Sintoma: a visao levava a camera para um lado atras de uma deteccao, o alvo
sumia, o `Repouso` pedia (0,0) para nivelar, e o `return` do cache engolia o
comando por achar que a cabeca "ja estava" la. A camera ficava presa apontada
para o verso de um monitor, **sem nunca mais voltar**, e o robo nao tinha como
se desvirar. Removido: limitador so por TEMPO. Cache de estado de hardware que
enxerga METADE dos comandos e pior que nenhum cache.

### Bug 5 - quadro atrasado: a causa real do "fica maluco"

Num stream MJPEG/HTTP o produtor nao espera o consumidor. O `cv2.VideoCapture`
le a fila EM ORDEM, entao com o Notebook processando mais devagar que o Pi
produz, cada `read()` devolvia o quadro MAIS VELHO da fila. Medido: **280KB
parados na Send-Q, ~2s de atraso permanente**.

O efeito nao e video travado, e pior: o Pan/Tilt vira controle proporcional com
ATRASO MORTO grande e passa do ponto SEMPRE. Medido com a pessoa parada, antes
da correcao:

    +202 -> +120 -> +21 -> 0 -> -57 -> -156 -> -80 -> +4 -> +150 -> +197

varrendo de ponta a ponta sem nunca assentar. O usuario relatou como "fica
maluco indo pra outra direcao" - a direcao estava CERTA, faltava informacao
nova. Baixar o ganho so deixaria lento e ainda oscilando; a causa era o atraso.

Corrigido em `captura.py`: `descartar_atrasados` usa `grab()` (que nao
decodifica, e barato) para pular o que envelheceu e so decodifica o quadro
atual. O criterio de parada e o TEMPO do proprio `grab()` - um que volta na
hora veio da fila, um que demora esperou o quadro nascer, logo estamos na ponta
viva. Ligado por padrao so quando a fonte e URL. Resultado: Send-Q de 280KB
para **0**.

Tambem se baixou o `--desired-fps` do ustreamer de 30 para 6: produzir mais
rapido do que o consumidor consegue processar so serve para encher fila.

### O espelhamento: eu errei duas vezes antes de medir

`camera_frontal_espelhada: true` estava certo para a camera INTEGRADA do
notebook (efeito selfie). A camera principal virou a DV20 no pan/tilt, servida
pela rede - webcam em suporte nao espelha. Troquei para `false` raciocinando
sobre o tipo de camera; a foto do stream pareceu contradizer (a placa de
"CUIDADO" saiu invertida) e eu recuei, dizendo que era rotacao fisica. Nenhuma
das duas conclusoes foi medida.

Quem resolveu foi o teste de eixo isolado, pedido pelo usuario: pan e tilt
passaram os dois, provando que firmware e fiacao estavam certos. Com o hardware
descartado, a convergencia do erro (+202 -> 0) provou que `false` era o valor
certo desde o inicio.

**LICAO: a flag descreve a FONTE da imagem, nao "a camera do robo". Trocar
`camera_indice_principal` obriga a revisitar essa linha.**

### As 9 ferramentas de bancada estavam quebradas desde 27/07

Todas abriam a serial em `115200` fixo, mas o baud virou `250000` em 27/07 (o
pacote de telemetria bloqueava 84ms). Elas falham como se o hardware estivesse
MORTO - `WHO_ARE_YOU` sem resposta em todas as portas - quando o Mega esta
falando normalmente, so que noutra velocidade. Confirmado escutando a serial
crua: 5600 bytes em 10s, todos lixo binario.

Isso significa que **todo diagnostico de bancada feito desde 27/07 deu
falso-negativo**. As duas ferramentas novas desta sessao (`mover_tilt.py`,
`diag_orientacao.py`) leem o baud do `orion.yaml`. As 8 antigas continuam com
o numero fixo - PENDENTE.

### PENDENTE

- **`comm.send` espera ACK dentro do laco da visao.** Com `ack_timeout_ms:500`
  e `max_retries:3`, um comando segura ate 1,5s, e o Vision Core chama isso a
  cada quadro rastreado. Relato do usuario: "comando espacado passa, varios
  seguidos buga" - o joystick entra na mesma fila. O certo e desacoplar: a
  visao manda o alvo mais recente e segue processando quadro, sem esperar o
  servo confirmar. NAO FEITO.
- **8 ferramentas de bancada com baud fixo em 115200** (lista acima).
- **`AcompanharPessoa` sem teste unitario** (vem da parte 2).
- **Camera torta no suporte** - observado pelo usuario. Enquanto a lente estiver
  inclinada, o eixo horizontal da imagem nao coincide com o do pan e o
  seguimento fica na diagonal. E ajuste mecanico, nao software.
- Causa raiz do falso positivo do YOLO (so mitigado com 3 quadros).

### Nao fazer: YOLOv8 no ESP32

Pedido durante a sessao. Nao roda, e nao e questao de otimizar: YOLOv8n tem
~3,2M parametros e mais de 6MB de pesos; o ESP32-CAM tem 520KB de SRAM e 4MB de
PSRAM - nao cabe na memoria, antes de falar de velocidade. O que caberia e
outra classe de modelo: FOMO (Edge Impulse) devolve CENTROIDE em vez de caixa,
que e exatamente o que o pan/tilt precisa, a ~5-10fps num ESP32-S3; ou ESP-DL/
ESP-WHO para rosto. Deteccao na propria camera eliminaria o stream e a latencia
de rede de uma vez - vale considerar DEPOIS de estabilizar o que existe.

## Sessao de 2026-07-28 (parte 4) - o joystick travava: dois bugs, um em cada camada

Relato do usuario, o mesmo nas duas vezes: **"quando dou um comando lento entre
um e outro passa, quando dou varios comandos buga"**. A frase descreve com
precisao um sistema onde chega mais do que sai - e eram DOIS gargalos
independentes, em camadas diferentes, que so juntos explicavam o travamento.

### Bug 1 - o transporte cobrava 208ms por comando com ACK (`f1c2501`)

Medido ANTES de mexer: 208ms por comando com ACK (mediana de 20, desvio
minimo), quando a ida e volta real a 250000 baud e de poucos ms. O joystick do
dashboard repete a ~150ms, ou seja **6,7 comandos/s entrando num cano que
vazava 4,8/s**. A fila crescia sem limite: travar era aritmetica, nao azar.

Os 208ms eram dois artefatos de 100ms, um em cada direcao:

1. **`enviar()` entrava na fila do executor atras do `read()` em andamento.**
   O executor tem uma thread so, e isso e obrigatorio (pyserial corrompe a
   porta com read/write concorrentes - achado real, com `self.fd` virando
   `None` no pyserial 3.5). Entao o write esperava o `read()` estourar o
   `timeout_leitura_s` inteiro.

   Corrigido submetendo o write PRIMEIRO e so entao chamando `cancel_read()`,
   a mesma tecnica que o `fechar()` ja usava desde 27/07. **A ordem importa:**
   cancelar antes de submeter abriria janela para o proximo read entrar na
   frente.

2. **`receber()` chamava `read(4096)`**, que so volta ao juntar 4096 BYTES ou
   estourar o timeout. Um ACK tem poucas dezenas de bytes - chegava em poucos
   ms e ficava PARADO os 100ms inteiros esperando um buffer que nunca
   encheria. Corrigido bloqueando por 1 byte e drenando `in_waiting`.

Medido depois: **208 -> 107 (item 1) -> 37ms (item 2)**. Teto por segundo:
4,8 -> 26,9. Estresse pelo caminho real do dashboard a 19 comandos/s: POST com
mediana de 1,8ms, `/estado` seguiu respondendo em 5,4ms, telemetria continuou
andando, zero erro de navegacao. 412 testes passam.

### Bug 2 - a espera era um impasse consigo mesma (`4344fa6`)

Relato do usuario: *"isso fica numa fila e isso e ruim"*. Estava certo, e a
causa era pior do que uma fila.

`_ao_receber_comando` executava o comando INTEIRO dentro do handler do Event
Bus. `executar_manual` chama `_aguardar_ocioso()`, que espera
`_estado_hardware` sair de `EXECUTING_MISSION`. Mas quem atualiza
`_estado_hardware` e o handler de `motion.status`, **do MESMO bus** - e o laco
de despacho e SERIAL (da `await` no handler).

Ou seja: a espera bloqueava a entrega do unico evento capaz de encerra-la. A
condicao de saida era impossivel e o timeout de 10s queimava INTEIRO, sempre.
**Havia 88 dessas no log.** Nesses 10s o barramento parava por completo -
nenhum evento de nenhum modulo era entregue - e os comandos de joystick se
acumulavam para executar atrasados depois. O pior comportamento possivel num
controle manual.

Agora o handler so registra o comando e volta na hora; a execucao roda numa
task, fora do bus. Com isso `motion.status` volta a ser entregue durante a
espera, e `_aguardar_ocioso` passa a ter condicao de saida alcancavel.

**O ULTIMO VENCE, e nao ha fila:** comando de joystick velho e DESCARTADO, nao
executado atrasado. Uma fila faria o robo continuar andando depois de o
operador soltar o botao - inaceitavel na re, que nao tem seguranca reativa (o
SafetyManager so olha o sensor frontal, Cap 18). Soltar o joystick manda STOP,
que ganha por ser o mais recente.

**Consequencia para quem chama:** publicar em `navigation.comando` e esvaziar a
fila do bus NAO garante mais que o comando foi tratado - a execucao e
assincrona. `test_comando_acao_desconhecida_publica_navigation_error` precisou
de um settle explicito, como o `test_comando_via_event_bus_e_processado` ja
tinha.

Teste novo `test_comando_manual_mais_recente_descarta_o_anterior` trava a
semantica do "ultimo vence" sem depender de tempo: chama o handler direto duas
vezes (nenhum `await` suspende, entao a task so comeca depois das duas) e
verifica que `MOVE_FORWARD` nunca foi enviado e `STOP` foi. 413 testes passam.

### Recusado: throttle no botao do joystick

Ideia levantada na bancada. Tratava o sintoma e deixaria o controle pastoso de
forma PERMANENTE. Se um dia precisar de protecao, o certo e **descartar comando
velho, nunca atrasar comando novo** - soltar o joystick tem que parar o robo na
hora, nao executar uma fila. Foi o que o `4344fa6` implementou.

### As 9 ferramentas de bancada, resolvido (`f8f9e69`)

Fecha o PENDENTE da parte 3. A correcao **nao foi trocar o numero - foi tirar o
numero**: `tools/_serial_bancada.py` le `communication.arduino` do `orion.yaml`
e exporta `PORTA` e `BAUD`; as ferramentas importam de la. Trocar o baud de
novo passa a ser uma linha na config, nao uma cacada por arquivo.

A porta tambem saiu do codigo. O valor `"auto"` na config significa descoberta
por `WHO_ARE_YOU`, que quem faz e o Motion Core - parado durante a bancada.
Entao o helper pega a primeira `/dev/ttyUSB*` que EXISTE, em vez de um caminho
chutado. Isso consertou de passagem `girar_radar_manual.py` e
`centralizar_servo.py`, que apontavam para `ttyUSB1` fixo: quando o CH340
migrou de volta para `ttyUSB0`, elas passaram a abrir um device inexistente.

Foram NOVE arquivos, nao os oito da lista - `centralizar_servo.py` tinha o
`115200` embutido na chamada do `SerialTransport` e nao aparecia numa busca
por `BAUD`.

**Verificado por IMPORT de verdade, nao por `py_compile`:** os 8 modulos com
guarda de `__main__` foram importados e imprimiram `PORTA=/dev/ttyUSB0
BAUD=250000`. `py_compile` nao pegaria um import quebrado - a mesma armadilha
que ja tinha mordido no merge de 26/07.

### PENDENTE (atualizado)

- **Joystick com as RODAS NO AR - unico item sem prova real.** O `4344fa6` e
  correto por construcao e passou 413 testes, mas o teste de carga usou
  `LIGHT_ON`/`LIGHT_OFF`, que **nao passam** por `_aguardar_ocioso()` - so
  comandos de `COMANDOS_DE_MOVIMENTO` passam. O caminho do bug nao foi
  exercitado com movimento real.
- **Nao ha como escolher o modo de navegacao.** Os 6 modos existem (`HOLD
  MANUAL GOTO PATROL FOLLOW EXPLORE`) mas `COMANDOS_PERMITIDOS` em
  `motion_core/webui/server.py` so tem movimento manual, luz e ventilador. O
  robo fica preso em MANUAL porque **ninguem nunca pede outro modo**. Nao e
  bug, e funcionalidade que falta. Distincao que confunde: o **maestro
  (Behavior Core) ja e autonomo** e decide sozinho; o **modo de navegacao** e
  outra camada - quem manda nas rodas. O robo ja "pensa" sozinho, mas nao ha
  como autoriza-lo a ANDAR sozinho.
- **`comm.send` espera ACK dentro do laco da visao** - continua verdade, mas
  agora custa 37ms em vez de 208ms. O `f1c2501` deixou a espera BARATA; nao
  desacoplou. Com `ack_timeout_ms:500` e `max_retries:3` um comando ainda pode
  segurar ate 1,5s no pior caso. Baixou de urgente para incomodo.
- **`AcompanharPessoa` sem teste unitario** (vem da parte 2).
- **Camera torta no suporte** - ajuste mecanico, nao software.
- Causa raiz do falso positivo do YOLO (so mitigado com 3 quadros).
- **Modulos orfaos, nao apagar:** `AlivioCarga` e `DiarioObservacoes` (usados
  so pelo `conversar_fofao.py` da raiz, que nao e o de producao). Entender por
  que existem antes de integrar, arquivar ou remover.

## BUG ABERTO — `OBSTACLE_DETECTED` travado com o caminho livre (2026-07-28)

**PRIORIDADE MÁXIMA da próxima sessão.** Este bug bloqueia TUDO que foi
construído em 28/07: a IA decide, o gesto chega ao maestro, o comando sai —
e a segurança reativa veta o movimento, baseada num estado que os próprios
sensores do Mega desmentem.

### A contradição, medida na interface web

    hw.estado                = OBSTACLE_DETECTED
    hw.distancia_frontal_cm  = 199.59
    mapa (radar, 30°→150°)   = 199.6 | 198.2 | 198.8 | 199.3 | 199.7 | 199.4
                               todas com valida: true

**Sete ângulos do radar e o sensor frontal reportam ~2 metros livres, e o
firmware afirma obstáculo.** A frente estava fisicamente desimpedida — o
usuário confirmou ("frente liberada sempre").

### O que já foi descartado

- **Não é obstáculo físico.** Confirmado pelo usuário e pelo radar inteiro.
- **Não é falta de eco.** `frontal_sem_eco: false`, todas as leituras do
  radar marcadas `valida: true`.
- **Não é a diferença entre leitura crua e filtrada.** Esta foi a minha
  primeira hipótese: a telemetria filtraria o ruído e o `SafetyManager`
  reagiria à leitura crua. Cai por terra porque o próprio estado do
  firmware convive com suas próprias leituras dizendo 200 cm.

### Hipótese principal: o latch não libera

Existe no histórico o commit `a82d112` — *"latch de presença do ultrassom"*.
Se esse latch não for liberado corretamente, **uma única** leitura espúria
prende o robô em `OBSTACLE_DETECTED` para sempre, mesmo com tudo limpo
depois. O log do maestro é consistente com isso: alterna
`obstáculo à frente` / `obstáculo liberado` em ciclo, sem nunca sair de
verdade do estado.

Vale olhar junto: `frontal_descartes` cresce ~3,4 por segundo (11.770 →
11.821 em 15 s) e `traseiro_descartes` está em 8.963. Descarte nessa
frequência é normal em ultrassom, mas é a fonte provável da leitura
espúria que arma o latch.

### Onde mexer

`firmware/hardware_core/include/safety_manager.h`. Compilar e gravar **pelo
Pi** (regra arquitetural: só a instância do Pi enxerga o Arduino).

Direção provável: exigir leituras CONSECUTIVAS abaixo do limiar para
declarar obstáculo, e garantir liberação simétrica quando o caminho limpar.
Isso **não enfraquece a segurança** — obstáculo real persiste por muitas
leituras seguidas; ruído não.

### Por que importa tanto

Em 28/07 ligamos a IA aos motores (EDR-0026), fundimos o mentor numa
personalidade só e fizemos o gesto chegar às rodas. O gesto chegou a girar
o robô de verdade. Mas **o avanço nunca aconteceu em nenhum momento**, e
por este bug — não por defeito de nada que foi construído. Enquanto ele
existir, o Fofão só mexe pan e tilt: a cabeça não passa pela camada de
segurança, as rodas passam.

### Achados menores da mesma inspeção

- **Bússola não calibrada:** `bussola_calibrada: false`, `rumo_valido:
  false`. A IA já tem `calibrar_bussola` no catálogo do contrato — ela pode
  resolver sozinha assim que puder agir.
- **DHT mudo:** `temperatura_c` e `umidade_percent` vêm `None`, como o
  EDR-0026 já registrou na atribuição "Zelador".

## RESOLVIDO — `OBSTACLE_DETECTED` era o próprio radar varrendo (2026-07-28)

Fecha o BUG ABERTO de 28/07, que estava marcado como prioridade máxima.
**A hipótese registrada era errada**: não havia latch nenhum. O commit
`a82d112` não tem participação nisso, e `_pararAtivo` sempre resetou
corretamente a cada ciclo.

### A causa, medida

`safety_manager.h` decidia obstáculo assim:

    bool obstaculoFrontal = !radar.apontandoParaFrente() || ...

E `apontandoParaFrente()` é `_anguloAtual == 90`. Durante um `SCAN_FRONT` o
servo do radar passa ~2,1s fora dos 90° — e nesse intervalo inteiro a
segurança **declarava obstáculo frontal**, por construção, com a frente
medindo 199 cm válidos.

Medido ao vivo, 89 amostras de telemetria:

    radar fora dos 90°     42 de 89  (47%)
    OBSTACLE_DETECTED      42 de 89  (47%)

**A mesma contagem.** O estado era obstáculo se e somente se o radar estava
olhando pro lado.

### Por que nunca saía: o laço se retroalimentava

1. A IA pede `SCAN_FRONT` (está no catálogo do `contrato.py`).
2. A varredura gira a cabeça pra fora dos 90°.
3. A segurança declara `OBSTACLE_DETECTED`.
4. `vigilancia_obstaculo` — **prioridade 100, a mais alta de todas** — toma
   o controle de todo mundo e só fica em `sleep(1.0)`, sem fazer nada.
5. A varredura acaba, volta a IDLE, a vigilância solta, a IA volta e pede
   outra varredura.

Medido: **46 tomadas de controle por obstáculo em 100 s**, alternando com
`ia_estrategica` (42). É exatamente o "alterna obstáculo à frente /
obstáculo liberado sem nunca sair do estado" registrado em 28/07. O robô
estava perseguindo o próprio rabo — daí "estático desde q ligou o cérebro
ao motor".

### A correção (não afrouxa a segurança)

`RadarManager` passou a guardar a última leitura frontal **de verdade**
(radar em 90° e leitura válida) com a hora dela. `SafetyManager` separou
dois casos que eram um só:

- olhando pra frente → decide pela leitura ao vivo, como sempre;
- olhando pro lado, com leitura frontal de menos de 2,5 s **que mostrava
  mais de 1 metro livre** (25 cm de limiar + 75 cm de margem) → segue;
- olhando pro lado sem memória recente → **para**, como antes.

A margem de 75 cm é o ponto: a memória nunca substitui o sensor perto de um
obstáculo, só cobre o caso de sala aberta — que é onde a varredura travava.

### O diagnóstico mentia, e agora não mente

O motivo da parada **não existia na telemetria**. O Pi via
`OBSTACLE_DETECTED` e não tinha como saber se era obstáculo real ou o radar
olhando pro lado — isso custou uma sessão inteira de diagnóstico às cegas.
Agora vão no pacote: `seguranca_motivo`, `seguranca_parada_ativa`,
`radar_apontando_frente` e `frontal_idade_ms`. O motivo
`frente_desconhecida` é distinto de `obstaculo_frontal`.

### Resultado, mesma sonda, mesmo tamanho de amostra

    antes:  OBSTACLE_DETECTED 42/89 (47%)  |  IDLE 47 (53%)
    depois: OBSTACLE_DETECTED  0/89  (0%)  |  IDLE 89 (100%)

Nas 2 amostras em que o radar ainda estava desviado, o estado seguiu IDLE —
a memória frontal funcionando. 432 testes passando antes e depois.

### PENDENTE que este bug revelou

- **`vigilancia_obstaculo` não desvia, só congela.** Diante de um obstáculo
  REAL ela toma o controle e dorme em `while True: sleep(1.0)`. Decisão do
  usuário mais tarde em 28/07: *"nenhum obstáculo tem q bloquear, o robô tem q desviar
  do obstáculo"*. A parada reativa do Mega continua sendo o piso de
  segurança; o que falta é a camada de cima planejar o contorno em vez de
  ficar parada.
- **Bússola sem auto-calibração** (`rumo_valido: false`). Não tem relação
  com este bug — medido, a causa era o servo do radar. Mas o usuário quer
  que ela **se auto-calibre**; `calibrar_bussola` já está no contrato da IA.


## Sessao de 2026-07-29 - o Mega para de vetar, e tres mentiras que sustentavam o congelamento

Sessao longa, com hardware. Comecou para fazer o robo DESVIAR do obstaculo em
vez de congelar (pendencia de 28/07) e terminou reescrevendo quem tem
autoridade sobre o movimento - ver EDR-0027.

### O congelamento nao estava no Python

`vigilancia_obstaculo` fazia `while True: sleep(1.0)`, mas trocar isso por um
plano de desvio nao resolveria nada: `command_executor.h` recusava TODO comando
de movimento enquanto `safety.pararAtivo()` fosse verdadeiro - **incluindo
TURN_LEFT/TURN_RIGHT** - e `safety_manager.h` chamava `motores.parar()` a cada
volta do loop. A unica manobra capaz de tirar o obstaculo da frente era tambem
a unica coisa proibida.

Decisao do mantenedor, dita e reafirmada quatro vezes ("o mega nao pode vetar",
"nada tem q bloquear ou impedir de tomar acoes", "nao tem freio, existem
manobras"): o Mega mede, informa e executa. Sobrou so o homem-morto - motores
girando sem comando novo por 5s. Detalhes e consequencias no EDR-0027.

### Mentira 1: "nao medi" chegava como "esta livre" (a mais grave)

Relato do usuario: *"as vezes quando testava proximo do sensor acusava esse
517 cm"*. Os 517 sao o teto de alcance - `_foraDeAlcance()` em
`sensor_ultrassonico.h` reporta esse valor com **`_leituraValida = true`**
quando nada refletiu. O proprio driver marca `_semEco` para denunciar, e
**ninguem olhava a bandeira**: nem o `SafetyManager`, nem o `NavigationCore`,
nem o `EstadoDoMundo`.

Por que aparece justamente PERTO: o eco de um obstaculo a 40 cm dura ~2,3 ms.
Medido nesta sessao na telemetria real, com o robo parado:

    loop_max_us = 118.816 a 124.380   (118-124 ms de volta do loop)
    t_envio_us  =  87.264 a  91.648   (~88 ms so mandando telemetria)

O eco inteiro nasce e morre dentro da cegueira, o pino ECHO nunca e visto
subir, e o driver conclui "nada refletiu". **Quanto mais perto o obstaculo,
maior a chance de virar caminho livre.** Falha para o lado errado, na hora que
importa. `frontal_descartes` subiu de 3 para 14 em ~4 s (3,4/s).

Nota de regressao: o journal de 27/07 previa ~24 ms por pacote a 250000 baud e
mediu 68 ms; hoje esta em ~88 ms. O pacote cresceu desde entao - cada campo
novo de diagnostico custa tempo de olho fechado. Foi por isso que os pulsos de
encoder, adicionados no meio desta sessao, foram REVERTIDOS: pagavam cegueira
extra no pacote reduzido (o que sai andando) por um sensor que nao esta
instalado.

Corrigido no `NavigationCore` e no `EstadoDoMundo`: `sem_eco` vira `None` -
"nao sei" - e nunca uma parede imaginaria a 5 metros. Novo metodo
`NavigationCore.frente_desconhecida()` separa "nao sei" de "esta livre", que
eram a mesma resposta ate hoje.

### Mentira 2: o cerebro assinava o canal errado do estado

O usuario afirmou que havia "uma trava para enganar o sistema e deixar sempre
em idle". Procurei duas vezes no lugar errado (webui, JavaScript) antes de
achar - e ele estava certo.

A telemetria carrega `estado` em TODO pacote, e a webui le dali. Mas
`VigilanciaObstaculo`, `EstadoDoMundo` e `NavigationCore` assinam
**`motion.status`**, que o firmware so publica **na BORDA**
(`notificarTransicaoDeEstado`). Consequencia: `navigation_core.py` inicializa
`self._estado_hardware = "IDLE"` - um valor INVENTADO - e depois de um restart
do Motion Core o Pi acredita nele ate o Mega mudar de estado por conta propria.
Com o Mega parado em OBSTACLE_DETECTED nao ha transicao a anunciar, e o dado
certo chega a cada segundo sem ninguem escutar. Um `motion.status` perdido na
serial tem o mesmo efeito, para sempre.

A telemetria agora atualiza `_estado_hardware` tambem. O `motion.status`
continua servindo para reagir na hora; a telemetria virou o lastro.

**A tela era mais confiavel que o cerebro do robo.**

### Mentira 3: PASSOS_POR_METRO, e da para reconstruir o engano

Com as medidas do mantenedor (roda 200 mm, TB6600 em 1/8 = 1600 passos/volta):

    1600 / 0,4    = 4000   <- circunferencia como 2 * diametro  (o valor antigo)
    1600 / 0,6283 = 2546   <- PI * diametro (o certo)

Fator PI/2 = 1,571: **o robo andava 57% mais do que o comandado**. Pedir 30 cm
movia 47 cm. Erro sistematico, sempre no mesmo sentido, que a odometria em
malha aberta nunca corrigiria. Ficou grave hoje justamente porque o freio do
Mega saiu.

Corrigido como expressao derivada (`PASSOS_POR_VOLTA_MOTOR / (PI *
RODA_DIAMETRO_M)`), nao como numero digitado - numero digitado a mao foi a
origem do erro.

`PASSOS_POR_GRAU = 20` ficou marcado como SUSPEITO: com 1600 passos/volta e
roda de 200 mm, ele implicaria bitola de 0,90 m, impossivel num robo de 0,50 m
de largura. Para uma bitola plausivel de 0,45 m o valor seria 10 - o robo
giraria o DOBRO do pedido. **Falta medir a bitola.**

Formula do giro diferencial, para nao ser rederivada: cada roda percorre um
circulo de raio igual a metade da bitola, entao
`passos_por_grau = passos_por_volta * bitola / (360 * diametro)`. O PI cancela
dos dois lados; uma volta completa do robo = `bitola / diametro` voltas de
roda.

### O radar estava mudo, e a sonda mentia junto

Ao medir qual lado fisico corresponde ao angulo logico do radar, sete angulos
cobrindo 120 graus voltaram todos entre 199,2 e 203,4 cm - variacao de 2%.
Impossivel varrendo de verdade. Concluido que o servo nao movia o sensor.

Causa: **o fio do servo estava solto.** Reconectado, a mesma varredura passou a
dar 161 / 165 / 197 / 200 / 245 / 91 / 245.

O que isso revelou e maior que o fio: **o firmware nao sabe dizer que o radar
nao esta se mexendo.** `radar_angulo_graus` vem de `_anguloAtual`, atualizado
por `_servo.write()` sem nenhuma confirmacao fisica. Com o fio solto, a
telemetria afirmou "radar em 30 graus" com total confianca, e todo diagnostico
de bancada naquela janela deu falso.

Duas armadilhas de metodo pagas aqui:

1. **A varredura de boot sobrescreve RADAR_SET_ANGLE.** Abrir a serial reseta o
   Mega (DTR); o `setup()` dispara `iniciarVarredura()`, que dura ~2,1 s e
   termina estacionando o radar em 90. Um comando mandado antes disso recebe
   ACK, "funciona", e e engolido. A sonda so ficou honesta esperando 5 s e
   **conferindo o angulo na telemetria** (90 antes, 30 depois).
2. **A medicao do lado nao fechou.** Com a mao a 40 cm, nenhum angulo acusou
   40 cm - a mao espalha o ultrassom e devolve "sem eco". E os angulos 70 e 130
   oscilaram 30 cm entre duas varreduras separadas por 3 s, porque havia gente
   andando perto. Fica PENDENTE, com protocolo definido: objeto rigido e plano,
   ninguem se mexendo, varredura de referencia antes e depois.

### Instinto que nasceu disto (primeiro da lista)

**Desconfiar de varredura sem variacao.** Sete angulos cobrindo 120 graus que
voltam todos dentro de 2% nao sao um comodo uniforme - sao um radar parado. O
robo deve marcar a varredura como nao-confiavel e avisar, em vez de montar mapa
em cima dela. Barato, nao exige sensor novo, e teria pego o fio solto sozinho.

### O que foi construido

- `TipoMovimento` no `MotorManager` (NENHUM/FRENTE/RE/GIRO) - a seguranca
  precisava distinguir "avancando" de "girando no lugar", e `emMovimento()`
  sozinho nao dizia. `movimentoAtual()` cruza o comandado com o estado real das
  rodas, porque um giro por graus termina sozinho sem ninguem chamar `parar()`.
- Acao `GIRAR` no `NavigationCore` (`{"acao": "GIRAR", "graus", "sentido"}`).
  **Nao usa `MANUAL` de proposito**: `ControleManual` (prio 95) assina
  `navigation.comando` e assumiria o controle a cada giro de desvio, entregando
  o robo a um "operador" inexistente por 1,5 s.
- `VigilanciaObstaculo` gira em malha fechada em vez de dormir. Sentido inicial
  arbitrario e ASSUMIDO como tal - o mapeamento angulo->lado nao foi medido -
  e o laco inverte o sentido a cada 3 tentativas sem sucesso. Depois de uma
  volta inteira sem saida, publica `diagnostic.alerta` tipo `encurralado` e
  continua tentando, nunca desiste em silencio.
- Secao `chassi:` no `orion.yaml` com as medidas reais: 50 cm de largura, 30 de
  profundidade, 65 de altura, roda de 200 mm, TB6600 em 1/8.

### Estado ao final

434 testes passam + 8 pulados no Pi (eram 428+8; 6 novos hoje). Zero regressao
de ruff (20 erros no `motion_core` antes e depois, 2 nos testes antes e depois -
todos pre-existentes). Firmware compila: RAM 47,7%, Flash 19,8%.

**O firmware NAO foi gravado no Mega.** Tudo que foi medido nesta sessao e do
firmware ANTIGO.

### PENDENTE

- **Gravar o firmware no Mega** e confirmar a telemetria. Nada de hoje esta no
  hardware.
- **Medir a bitola** - destrava o `PASSOS_POR_GRAU`.
- **Medir o ultrassom frontal**: altura do chao e recuo em relacao a ponta do
  robo. Se estiver recuado 10 cm, os 25 cm de limiar sao 15 cm de para-choque -
  erro sistematico e sempre no sentido perigoso.
- **Validar `PASSOS_POR_METRO = 2546` no hardware**, com a malha fechada por
  ultrassom (proposta do mantenedor: fixar um ponto, medir, e se guiar pela
  aproximacao da medida em vez de contar passo). Isso implementa de passagem a
  autocalibracao do Cap 12 s.9, que esta escrita no `orion.yaml`
  (`odometry_correction_factor`) e nunca foi construida.
- **Fechar a medicao angulo->lado do radar** com o protocolo acima.
- **Custo da telemetria**: ~88 ms por pacote, loop cego ate 124 ms. Atacar o
  tamanho do pacote e a causa; tratar `sem_eco` como desconhecido (feito hoje)
  cobre o que sobrar.
- **Missao com retomada** - `executar_patrol` ainda desiste da rota.
- **Ferramenta `olhar_em_volta`** (ideia do mantenedor): varrer com o pan/tilt,
  fotografar em varios angulos, escolher o caminho e confirmar a distancia com
  o ultrassom. `tools/panorama.py` ja faz a parte mecanica. Ressalva medida: o
  YOLO reconhece CLASSES treinadas, nao "obstaculo" nem "chao livre" - parede
  lisa e batente de porta nao aparecem.
- **BUG CONFIRMADO, nao corrigido - o diario nunca registra ninguem.**
  `diario.py:75` assina `vision.person_detected` e le `evento.dados["nome"]`
  (linha 81), mas o `VisionCore` publica esse topico com
  `{classe, confianca, caixa, timestamp}` - **sem `nome`**. O nome so existe em
  `vision.person_recognized`. Entao `nome` e sempre None, o `if not nome:
  return` da linha 85 dispara sempre, e **nenhuma pessoa reconhecida jamais
  entrou no diario em producao**. O teste passa porque publica um payload
  sintetico que o produtor real nunca emite - mesma armadilha que deixou o
  FOLLOW morto em 28/07.

### O padrao, agora com nome e contagem

Cinco ocorrencias de "produtor e consumidor com contratos diferentes, e
publicar nao falha": guardiao de RAM (ate 19/07), pan/tilt da visao (ate
25/07), eventos de visao nao atravessando para o Pi (28/07), FOLLOW esperando
`centro_x` (28/07), e agora o diario esperando `nome`. Some-se a familia irma,
"produz e ninguem consome": `encoder_manager` conta pulsos que nunca sairam do
Mega.

**Vale como checagem de rotina, nao como acidente:** ao ligar um assinante
novo, conferir no PRODUTOR real quais campos ele emite - nunca no teste, que e
escrito pela mesma cabeca que escreveu o assinante.


## Sessao de 2026-07-29 (parte 2) - o robo andou sozinho pela primeira vez

A parte 1 tirou o veto do Mega. Esta parte descobriu que o robo continuava
parado por outros tres motivos, nenhum deles no lugar onde se procurava.

### O diario nunca registrou ninguem - provado no banco

Consulta ao `orion.db` de producao:

    === diario, por tipo, desde sempre ===
      desconhecido_visto     10
      (nenhum `pessoa_vista`)
    pessoas cadastradas: 3  -> Joao Paulo, Marah, Kamal

Dez registros na historia inteira do banco, todos "vi uma pessoa que nao
reconheci". O mais recente e de hoje, com o dono sozinho em casa na frente do
proprio robo: ele viu o dono e registrou um ESTRANHO.

Causa: `diario.py` assinava `vision.person_detected` e lia
`evento.dados["nome"]`. Esse evento nunca teve `nome` - o payload real e
`{classe, confianca, caixa, timestamp}`. O nome so existe em
`vision.person_recognized`.

### O mesmo bug em mais dois lugares, e um deles e grave

- `estado_do_mundo.py` fazia igual: `pessoa_conhecida` era falso para TODO
  MUNDO, inclusive para os donos da casa. A IA decidia achando que so entravam
  estranhos.
- **`vision.person_recognized` nunca era encaminhado do Notebook ao Pi.** O
  `NavigationCore._ao_reconhecer_pessoa` assina esse topico desde 28/07, quando
  o FOLLOW foi corrigido para so andar atras de gente reconhecida. O handler
  **nunca rodou uma unica vez**. A correcao de 28/07 trocou o assinante e
  esqueceu o produtor - exatamente o erro que ela mesma consertava.

Os testes nao pegaram porque publicavam um evento sintetico COM `nome` dentro,
payload que o produtor real nunca emitiu. Um deles
(`test_pessoa_conhecida_e_desconhecida`) literalmente protegia o bug: passava
com o codigo errado e falhou assim que o codigo ficou certo.

### A IA que comanda o robo decidia praticamente cega

`IaEstrategica._consultar` mandava como contexto, literalmente:

    {"comportamento_ativo": "repouso"}

Uma linha. Ela escolhe entre avancar, girar, explorar e seguir pessoa sem saber
se ha obstaculo, quem esta na sala, qual a bateria ou se o robo esta inclinado.

O `EstadoDoMundo` e o `montar_contexto` ja montavam o quadro inteiro - so que
ligados ao `ponte_conselho`, que esta DESABILITADO. O retrato completo ia para
um caminho desligado enquanto quem decide recebia uma linha.

### O rotulo que transformava caminho livre em ameaca

Ligado o contexto, a IA passou a dizer coisas como *"e melhor parar para evitar
colisoes com o obstaculo a frente"* - com a frente livre por 2 metros.

A linha era `_linha("obstáculo à frente", 199.3, " cm")`, que produzia:

    - obstáculo à frente: 199.3 cm

Ela lia "ha um obstaculo" e o numero virava detalhe. O limiar de perigo (25 cm)
nunca apareceu no prompt, entao nao havia como saber que 199 cm e caminho
livre. **Descrever espaco vazio como obstaculo e pedir timidez e depois
estranhar que o robo nao ande.**

Agora: "CAMINHO LIVRE por 199 cm (so e obstaculo abaixo de 25 cm)", "OBSTACULO
a 12 cm - perto demais para avancar", ou "NAO SEI a distancia agora (convem
olhar antes de avancar)" quando o `sem_eco` chega.

Junto foi a setima ocorrencia do padrao do dia: `bloco_corpo` pedia
`distancia_tras_cm`, o RetratoDoMundo entrega `obstaculo_tras_cm` - "espaco
atras de mim" era "nao sei" SEMPRE.

### Ninguem nunca convidou o robo a explorar

So disseram que era permitido. Uma lista de opcoes nao e um convite. Pedido do
dono: *"quero que ele ligue e sinta o desejo de descobrir o mundo"*,
*"programa-lo pra ele ser ousado e descobrir tudo o que ele mesmo e capaz"*.

Novo bloco `DISPOSICAO` no grounding, e ele vem PRIMEIRO no contexto (modelo
pequeno pesa mais o comeco do prompt). Diz quem ele e, que as acoes sao as
FERRAMENTAS dele, que com o caminho livre explorar e o certo, e que ele nao
precisa ter medo de escolher errado porque o perigoso nem aparece nas opcoes.

**Isto nao afrouxa nada**: os limites moram em codigo (`contrato.py`,
`PROIBIDO_PARA_IA` verificado no import). O texto muda a DISPOSICAO dentro do
que ja era permitido, nunca a fronteira.

### O RESULTADO - medido em producao

    IA decidiu 'explorar' (Com o caminho livre à frente, é hora de explorar e
                           conhecer melhor o ambiente ao meu redor.)

    estado       : EXECUTING_MISSION
    em_movimento : True
    ultimo plano : segment_completed, distancia_cm: 30.0

**Primeira vez que o Fofao move as rodas por decisao propria.** Varreu,
confirmou a frente livre, andou 30 cm e concluiu o segmento. Foi mandado parar
na hora porque estava preso a cabos; depois o dono suspendeu as rodas e
liberou.

E, com o nome atravessando ate o cerebro:

    IA decidiu 'seguir_pessoa' (João Paulo é a única pessoa que vejo agora.)

Ele chamou o dono pelo nome. Antes de hoje esse nome nao existia em lugar
nenhum do cerebro - era impossivel dizer essa frase.

### Instinto da bussola, e a histerese que faltou

O instinto entrou em producao e detectou de imediato. Mas ficou piscando:
5,7 / 6,7 / 6,0 / 6,1 - alertando e desalertando sem parar.

Causa: diferenca de unidade. O limiar de 6 graus foi arbitrado como "metade da
oscilacao de hoje" (8 a 12 graus de FAIXA), mas `dispersao_circular_graus`
devolve o desvio maximo em relacao a MEDIA, que ja e ~metade da faixa. O
"metade" caiu em cima do valor atual.

Histerese resolveu sem mexer no numero: alerta acima de 6,0, so relaxa abaixo
de 4,5. Confirmado em producao - as transicoes viraram excursoes reais
(3,3 <-> 6,2). Cuidado que a primeira tentativa errou: o `return` antecipado do
ramo "alertado" matava a retentativa de calibracao.

### Saudacao espontanea

Pedido do dono: *"praticamente ao me ver me cumprimentar"*. So foi possivel
hoje - cumprimentar era impossivel por falta de IDENTIDADE, nao de voz.

Cumprimento por RELACAO, definida no `orion.yaml` (secao `familia`): Joao Paulo
e o criador, Kamal e como um irmao (3 anos, nao escreve), Marah e como uma mae
cuidadora. A mesma secao entra no contexto da IA.

O cuidado que define o modulo: `person_recognized` sai ate 15x/s. Reconhecer e
diferente de CHEGAR - janela de 30 min por pessoa. Teste principal: 40 eventos
seguidos produzem UM cumprimento.

### AUDIO - o oposto do que a config supunha

O `orion.yaml` trata o indice 5 (`default`) como "reserva perigosa que captura
silencio". Medido hoje:

    indice 3 (USB Audio Device): FALHOU - Invalid sample rate (nao abre a 16 kHz)
    indice 5 (default):          rms=0.20568  pico=0.29690  captando

**O microfone USB "de verdade" nao aceita 16 kHz**; quem funciona e o `default`,
justamente porque passa pelo PulseAudio, que converte a taxa. E o robo ESTA
ouvindo - transcricoes reais no log: "O que e isso?", "Bem, conseguimos...",
"Valeu!" com rms de 0,067 a 0,097.

Uma leitura de `rms=0.0232` com texto vazio foi interpretada por mim como
"microfone morto"; a medicao seguinte desmentiu - era so um momento de silencio
na sala.

**IMPORTANTE, dito pelo dono ao ver esta medicao:** tudo acima e sobre o
microfone USB do Notebook, que e o PALIATIVO. Os ouvidos de verdade do projeto
sao os **dois microfones I2S ligados ao ESP32**. Entao o audio nao esta
resolvido - esta funcionando por um caminho que nao e o desenhado, e as
pendencias dos ouvidos continuam de pe:

  - **ganho**: a voz chega a ~6% da escala e o Whisper nao reconhece; no
    firmware do ESP32 o `cru >> 16` pode virar `>> 12` (16x);
  - **separacao fisica**: os dois mics estao juntos na placa, entao o atraso
    entre eles e menor que uma amostra e os angulos saem aleatorios. Fixar um
    de cada lado do robo, medir a distancia com fita e por em
    `LocalizadorDirecao(separacao_m=...)`;
  - o `localizacao.py` (GCC-PHAT, 15 testes) ja esta pronto esperando isso.

### Estado ao final

468 testes passam + 8 pulados (eram 420+8 no inicio do dia). Zero regressao de
ruff. Firmware compila, **e continua NAO GRAVADO no Mega** - todo o
comportamento novo descrito aqui roda com o firmware ANTIGO.

### PENDENTE (alem do que ja estava na parte 1)

- **PRIORIDADE DO DONO: voz.** Ele ouve e fala, mas falta validar de ponta a
  ponta: passar na frente da camera e ser cumprimentado pelo nome, e conversar
  por voz com a saudacao ligada. Nao testado com pessoa real ainda.
- **`familia` no contexto do Notebook.** A secao entrou no contexto da IA
  estrategica (Pi). O `mission_planner` do Notebook ainda monta o contexto da
  CONVERSA sem ela.
- **Diario ainda nao chega ao contexto da IA estrategica** - `observacoes` nao
  e passado em `_contexto_para_ia`, entao ela ouve "o diario esta VAZIO" todo
  ciclo mesmo agora que ele grava.
- **Duas autoimagens.** A IA de conversa le `config/prompt_sistema.txt`, escrito
  a mao; a estrategica le o `contrato.py`. Nada garante que concordem - o robo
  pode prometer falando o que nao existe no catalogo. O certo e derivar a
  autodescricao do contrato e injetar nos dois.
- **Sensores opticos** (Cotrinex LTS-1180-103, dois em maos) para RPM de roda.
  O firmware ja esta pronto (pinos 18/19, ISR, contadores); falta so a
  telemetria carregar os pulsos - foi adicionada e revertida hoje por custar
  cegueira no pacote reduzido sem sensor instalado. CONFERIR ANTES DE LIGAR:
  tensao de alimentacao e se a saida e NPN ou PNP (PNP em 12V queima o pino do
  Mega).

---

## Sessao de 2026-07-30 - quatro correcoes, e todas eram a mesma coisa

Sessao de leitura antes de codigo: o dono pediu a leitura do projeto inteiro,
modulo por modulo. O que saiu dela foram quatro defeitos que **nenhum teste
pegava e nenhum log denunciava** - os 596 testes passavam com os quatro no ar.

### O diagnostico de metodo, e ele veio do dono

Antes de qualquer correcao, o dono apontou o padrao:

> *"as contradicoes q existe na receita e no q ta feito foi proposto por muitas
> das vezes"*

Ele tem razao, e da para provar com data e hora. O commit `f5a39be` (29/07) se
chama *"a IA estava amordacada pelo proprio prompt"* e reescreveu a
`REGRA_ANTI_INVENCAO` no `grounding.py` para LIBERAR conversa - mas deixou a
mordaca original intacta no `config/prompt_sistema.txt` (*"voce nao sabe nada
alem dele"*). As duas vao no MESMO prompt, e o `prompt_sistema.txt` vai
PRIMEIRO. A correcao nao removeu a amordaca: pos uma contra-ordem depois dela.

**A regra que ficou:** antes de escrever qualquer instrucao nova (prompt,
regra, comentario normativo), procurar quem JA diz algo sobre aquilo e corrigir
LA. Se a correcao certa e remover texto, remover - a entrega pode ser menos
codigo do que existia.

Isso mudou o trabalho do dia: a quarta camada de prompt na saudacao, que eu ia
escrever, nao foi escrita.

### 1. "Nao sei" valia como "livre" no ponto que decide andar

O achado mais caro, e ele estava em dois commits do MESMO MINUTO de 29/07:

```
548cdd1 | 08:20 | [motion] "nao medi" deixa de chegar como "esta livre"
ab5c2dc | 08:20 | [firmware] EDR-0027: o Mega informa, nao veta
```

Um tirou o freio do Mega; o outro deveria ter fechado o outro lado. O `548cdd1`
fez metade: transformou `frontal_sem_eco` em `None` na ENTRADA e criou
`frente_desconhecida()`. **Nao fez ninguem consultar** - o metodo existia sem um
unico uso no projeto inteiro. O "nao medi" parou de chegar como "esta livre" na
entrada de dados e continuou chegando como "esta livre" no unico lugar onde
isso tinha consequencia.

O encadeamento, todo ele ja medido e escrito no proprio codigo: obstaculo PERTO
-> eco de ~2,3 ms cabe na cegueira de ate 124 ms do loop -> `frontal_sem_eco`
-> `None` -> "livre" -> avanca, sem freio embaixo. **A falha aponta para o lado
errado justamente na hora que importa.**

**Decisao do dono, tomada depois de ver o risco exposto:**

> *"o robo precisa ter mais iniciativa e coragem, travas de seguranca so impede
> o robo de andar - retira as travas, temos os sensores para realizar estudos de
> risco e tomada de decisao"*

Entao duvida NAO virou trava. Virou varredura, e o robo avanca mesmo sem medida
- declarando o risco por um fio que vai ate quem decide:

```
NavigationCore -> navigation.risco_assumido -> EstadoDoMundo
               -> retrato.risco_frontal_assumido -> grounding -> prompt da IA
```

Detalhe de redacao que importa: a linha que chega a IA e **fato, nao ordem**
(*"andei ha pouco SEM medida confiavel da frente"*). Escrever "pare" ali
devolveria pela porta dos fundos a trava que foi tirada do codigo.

Distingue tambem os dois "nao sei" que o projeto tratava como um so, usando o
`frontal_sensor_ok` que o firmware ja mandava e ninguem lia: sensor AUSENTE nao
trava o robo (bancada sem ultrassom segue andando); sensor VIVO que nao mediu e
o caso perigoso.

### 2. A Ronda nao "nunca rodou" - era inalcancavel, por TRES travas

O relatorio de 29/07 dizia *"so roda se a IA pedir ronda, e ela nunca pede"*, o
que sugere desinteresse da IA. Nao era isso:

1. `ronda` **nao estava no catalogo do contrato**, e `resolver()` devolve None
   para tudo que nao esta la - a IA nao PODIA escolhe-la;
2. o unico caminho ate `Ronda.pedir()` passava pela `ponte_conselho`, que e
   `None` porque `conselho_ia.habilitado: false`;
3. a terceira **so apareceu ao consertar as duas**: a `IaEstrategica` vive em
   prioridade 40 e SEGURA o controle; a `Ronda` vive em 20. Sem soltar, a IA
   pediria a ronda e engoliria o proprio pedido - trocando uma trava por outra.

Comportamento sem caminho ate ele **nao falha - so nao acontece**. E o irmao do
publicar-no-vazio, uma camada acima: la e evento sem assinante, aqui e acao sem
executor alcancavel. Nos dois, nada no log denuncia.

Ficou uma rede no boot: o Motion Core **nao sobe** se o catalogo delegar para um
comportamento que ninguem registrou.

### 3. O alivio de memoria nunca liberou um byte

As duas pontas mortas, e o conjunto parecia funcionar:

- `AiManager.descarregar()` usava `self._cliente`, atributo que **nunca existiu**
  nesta classe. AttributeError em toda chamada;
- `pausar_visao=None`: o maior consumidor do Notebook (YOLO + reconhecimento
  facial) seguia a todo vapor durante o aperto de memoria.

Como o `AlivioCarga` isola cada acao num try/except, o log exibia o caminho
feliz inteiro - detecta, pede, atende - sem um byte liberado. **Falha silenciosa
da pior especie: a que aparece como sucesso.**

### 4. Dois conselheiros, e o ativo apontava para o lugar errado

`conversar_fofao.py` criava um `ConselheiroComportamento` com
`modelo="gemma3:1b"` e SEM `base_url` - ou seja, falando com a OpenAI oficial,
com a chave do OpenRouter e um modelo que nao existe la. Mais adiante a
variavel era REATRIBUIDA a um segundo objeto, correto - mas o
`AtendenteConselhoIA` ja tinha guardado o metodo ligado ao PRIMEIRO.

Reatribuir a variavel nao troca o objeto de quem ja segurava a referencia.

### Estado ao final

**608 testes passam** (eram 596 no inicio do dia), ruff limpo nos 14 arquivos
tocados. 5 commits.

O Pi e o Notebook comecaram o dia ja sincronizados em `ee45625` - o "bloqueador
absoluto" do roadmap (Pi parado em `8da6969`) ja estava resolvido.

### PENDENTE

- **Deploy no Pi**: os 5 commits de hoje moram em `motion_core/` e `src/`. O Pi
  segue rodando o codigo de ontem.
- **A trava que sobrou**: obstaculo MEDIDO a menos de 25 cm ainda segura o
  `MOVE_FORWARD`. Nao foi removida por conta propria - e a diferenca entre "nao
  sei o que tem ali" e "medi 10 cm de parede". Decisao em aberto do dono.
- **LiDAR STL-50B2** comprado. **5V contra os 3.3V do UART do Pi: exige
  conversor de nivel logico, senao queima a placa.** Alimentacao so 5V.
- **Saudacao repetitiva**: diagnosticada e NAO corrigida. A causa nao esta onde
  as tres tentativas de 29/07 mexeram (prompt do usuario): `_compor_saudacao`
  chama `ia.responder` SEM contexto, entao o prompt de SISTEMA diz *"nao estou
  vendo ninguem"* e *"o diario esta VAZIO"* no exato instante em que ele
  cumprimenta alguem - contradizendo o contexto real que vai no prompt do
  usuario. E o prompt de sistema se declara "a unica fonte de verdade".
- **ROADMAP_ORION_OS.md** virou o caminho oficial. Tres afirmacoes dele ja
  cairam no mesmo dia em que foi escrito, porque partiu do relatorio e nao do
  codigo - ele mesmo pede a verificacao contra a arvore real.

---

## Sessao de 2026-07-30 (tarde) - a IA move o robo, e tres telas que mentiam

Sessao de bancada, com o dono ao lado do robo o tempo todo. Comecou com uma
pergunta simples ("por que ele nao anda?") e terminou com o rele acionando por
decisao da propria IA. 10 commits.

### O bug do dia: 5444 decisoes e ZERO execucoes

A armadilha de diagnostico armada de manha respondeu na primeira leitura:

    IA decidiu         : 5444 ocorrencias
    explore: comecando : 0

A instrumentacao provou que `executar_explore` nunca era chamado. Um susto pelo
caminho: `navigation_core.py` tinha ZERO `logger.info` antes de hoje, entao a
ausencia de linhas quase passou por "logger filtrado". Foi preciso ler o
`configurar_logger` (nivel INFO no pai, filhos com NOTSET propagando) para
concluir que o silencio era real e nao cegueira do log.

**Causa:** a `VigilanciaObstaculo` (prio 100) preemptava a `IaEstrategica`
(prio 40) e publicava `GIRAR` na **mesma vaga unica** do `NavigationCore`,
apagando o EXPLORE antes de ele rodar. `publish` nunca falha e o descarte era
mudo - o mesmo publicar-no-vazio de sempre, agora uma camada adiante.

O log mostra o ciclo inteiro, repetido:

    'ia_estrategica' assume (prio 40)
    maestro: IA decidiu 'explorar'          <- deixa EXPLORE na vaga
    'vigilancia_obstaculo' assume (prio 100)
    maestro: obstaculo a frente - girando   <- escreve GIRAR por cima

### As decisoes do dono

> *"vigilancia tem que vigia e mostrar dados e nao decidir, a ia estrategica é
> qeum com os dados em maos decidir a manobra e nao fica parado"*

> *"vamos colocae um botao para escolher q contrla, se o joistk ou a ia,
> autonomo ou manual - ai passa a responsabilidade por escolha"*

> *"se criar uma fila o robo vai dar crash"*

A vigilancia virou informante (`behavior.reconsiderar`), `quer_rodar()` sempre
False, e o laco de giro saiu junto com as constantes dele - entrega com MENOS
codigo do que antes. Os meios de manobrar (`virar_esquerda`, `virar_direita`,
`manobrar`) ja estavam no `contrato.py` e eram inalcancaveis porque ela agia
primeiro: irmao da trava da Ronda.

Nasceu o campo `autoridade` no `Comportamento` e o modo de controle no maestro.
**Prioridade responde "quem e mais urgente"; ela nunca respondeu "quem esta no
volante"** - tratar as duas perguntas como uma so era o defeito de fundo.

### RESULTADO - medido em producao

    explore: passo 1/4 - varredura devolveu 7 leitura(s)
    segmento: mandando MOVE_DISTANCE 30 cm a 60%
    explore: passo 4/4 - segmento de 30 cm concluido

13 explores completos, 0 abortados, 0 descartes. E o dono viu **o rele
acionar**: decisao da IA -> navegacao -> serial -> firmware -> energia dos
TB6600 esta inteiro. Falta so o elo mecanico.

### Tres telas que mentiam, e o dono achou as tres

1. **Deslocamento.** A tela afirmava `y = 0.8541 m` e `orientacao = 79.68 graus`
   com os motores FISICAMENTE DESCONECTADOS. O firmware confessa a origem:
   `long passosAcumulados = 0;  // odometria simples (sem encoder)` - sao os
   pulsos ENVIADOS ao driver, comando e nao medicao. Agora `motion.position`
   declara `origem` e `medida`, e dashboard/mapa so mostram numero quando
   medido. O `EncoderManager` real ja conta pulso no firmware (pinos 18/19) e
   **nunca chega a telemetria**; pulso->metro exige calibracao de bancada, e
   chutar seria trocar uma mentira por outra.
2. **Bussola.** A agulha GIRAVA com `rumo_valido: false`, carregando o offset dos
   imas dos motores. Texto de ressalva ao lado nao desfaz o que o desenho
   afirma. Agora ela fica parada no zero quando o rumo nao vale.
3. **Casas decimais.** `4.872143 graus` e `199.8621 cm` vinham do float, nao do
   sensor: sugeriam precisao de micrometro E faziam ruido de fundo parecer
   movimento com o robo imovel.

### A licao mais cara do dia: o navegador mentia tambem

O dono relatou em sequencia *"botao ficou feio"*, *"nao muda de cor"*, *"botoes
da selecao nao funciona"*. Tratei cada um como pedido de ajuste e refiz o CSS
tres vezes. **Nenhum era bug.** O `index.html` pedia `dashboard.js?v=20260727b`
- versao fixa de 27/07 - e o CSS sem versao nenhuma; o navegador tinha aquilo
guardado e nem perguntava ao servidor.

A combinacao engana porque e parcial: HTML novo (botoes aparecem), CSS velho
(feio, sem cor), JS velho (clica e nada). E e a MESMA familia de mentira que a
sessao passou o dia removendo dos dados: eu confiava no que o servidor tinha,
conferido por `curl`, enquanto a realidade do outro lado era outra. **Confirmar
no servidor nao prova que o usuario esta vendo aquilo.**

Ficou `Cache-Control: no-cache` nos estaticos (middleware) para as proximas, e o
bump de versao para sair do cache que ja estava na maquina.

### Achado da revisao: o PARAR podia esperar 1,2 m

Revisando o proprio codigo escrito na sessao: `_executar_pendentes` faz `await`
no `executar_explore` INTEIRO, e o handler do bus nao cria tarefa nova enquanto
a atual roda - so guarda o comando. Com UM segmento a janela era ~3 s; ao passar
para quatro, um STOP do operador esperaria o trecho todo. **Multiplicar o trecho
por 4 multiplicou por 4 o tempo de resposta do botao PARAR.** O laco passou a
olhar a vaga entre os passos: o robo anda no maximo um segmento a mais.

### E o "enfileirando" que nao era fila

Relato: *"aperto vai vai solto libera, apertando varias vezes colocar um time
entre um comando e outro para nao carregar"*. Nao ha fila (e nao vai haver).
`executar_manual` espera o Mega ficar ocioso antes de todo comando de movimento,
com timeout de 10 s - em rajada os comandos empurram uns aos outros e a resposta
sai atrasada, indistinguivel de fila para quem esta com o dedo no joystick.
Intervalo minimo de 250 ms na ORIGEM, com o STOP fora da regra.

Junto, um defeito meu: o alarme de descarte gritava no caso NORMAL - 223 WARNING
em minutos, quase todos MANUAL apagando MANUAL, que e o joystick reenviando a
cada 300 ms como projetado. Alarme no rotineiro afoga o raro. Ficou WARNING so
para decisao autonoma apagada.

### Estado ao final

**538 testes passam** (as 3 falhas restantes sao PRE-EXISTENTES: falta `cv2` no
Pi - verificado com `git stash`). Zero lint novo, medido contra baseline no mesmo
escopo. Pi em `72cc6ed`, arvore limpa, servicos ativos.

### PENDENTE

- **MOTORES DESCONECTADOS** (so os TB6600 no robo) e rodas suspensas. Unico
  bloqueio para provar deslocamento fisico. O rele FUNCIONA - o dono confirmou
  *"o rele nunca deixou de acionar"*.
- **NOTEBOOK ATRASADO**: esta em `7203f05`, o Pi em `72cc6ed`. O Pi TEM remoto
  `notebook` configurado (10.20.20.195). Nao foi feito nesta sessao de proposito:
  merge sem o dono acompanhando ja custou caro neste projeto.
- **ENCODER DE RODA**: sem ele nao existe deslocamento medido. Firmware ja conta
  pulso; telemetria nao manda; sensores nao instalados; falta calibrar.
- **`_aguardar_ocioso` com timeout de 10 s** no caminho manual - causa de fundo
  do atraso do joystick. Nao mexido: e a funcao que queimou 86 timeouts num log
  so em 28/07, merece o robo a mao e tempo de teste.
- **Bussola nao calibrada** (`rumo_valido: false`, oscila 6 graus parada).
- **A vigilancia virou comportamento zumbi**: registrada no maestro, aparece na
  lista do boot e nunca assume. Tirar do registro deixaria o boot honesto.
- **Sem a IA, nao ha plano B para obstaculo.** Antes o laco de giro resolvia
  sozinho; agora, com o Notebook fora do ar, o robo fica encostado. Foi decisao
  consciente do dono, mas vale um reflexo minimo de ultimo recurso.
- Do Portao 0: 11 arquivos citam `CLAUDE.md` (virou `ARQUITETURA.txt`), remover o
  Ollama, audio por NOME, reconexao serial sem re-varredura, e o conflito
  EDR-0026 (`PAUSED_MANUAL`) vs `ControleManual` prio 95 - este ficou MAIS
  relevante agora que existe modo de controle de verdade no maestro.

## Sessao de 2026-07-30 (noite) - auditoria linha a linha, e a missao da fala

Duas frentes no mesmo dia. A primeira foi pedida assim: *"leia todo o projeto
para ver se e isso mesmo, veja linha por linha e procure por qualquer erro"*. A
segunda foi a missao de naturalidade da fala, que chegou com T0 a T6 escritos.

### Parte 1 - a auditoria

**A suite do Pi nao rodava um unico teste.** `pytest -q` terminava em 1,6 s com
`Interrupted: 2 errors during collection`. Dois arquivos (`test_ouvidos_esp32`,
`test_transcricao`) faziam `import numpy` sem guarda, e numpy nao existe no venv
do Pi de proposito - audio e visao rodam no Notebook. Erro na COLETA nao pula o
arquivo: interrompe a sessao inteira. Dois arquivos levavam junto os outros 60.
De **0 para 538 testes passando**.

**Pi e Notebook tinham divergido de novo** apos `2052dc4` (Pi +10 commits,
Notebook +1, o da lixeira). Merge limpo, zero conflitos.

**A arquitetura descrevia um projeto que nao existe mais.** O `ARQUITETURA.txt`
citava `motion_core/bridge/` - pasta que nunca existiu - e OMITIA
`motion_core/behavior/`, que e o maestro, o modulo que decide o que o robo faz.
Dizia "ultimo EDR e o 0019" (existem ate o 0027, e o 0026/0027 mudaram regra de
arbitragem e de seguranca). E o "Teto atual do projeto" afirmava que nada podia
ser verde *porque o robo nunca se moveu* - o que venceu em 29/07.

**O log nao dizia a hora.** 10,3 MB, 63 mil linhas, zero horarios. O
`_FormatadorJSON` tem `timestamp` desde sempre, mas so e instalado quando
`arquivo_log` e passado, e o Motion Core nao passa - quem grava o arquivo e o
systemd, redirecionando o stdout, que usava o formato do console. Sem hora nao
da para medir a duracao de nada.

**A tela `/log` mostrava o log errado** - achado do dono, olhando a propria
pagina. `CAMINHO_LOG` apontava para `data/logs/orion.log`: 1,7 KB, ultima
escrita em 23/07, conteudo de 17/07, escrito por um caminho de boot que nao roda
em producao. Ao lado, `motion_core.log` com 10,3 MB sendo escrito no segundo em
que se olhou. A pagina de diagnostico do robo mostrava um boot de duas semanas
atras, e nunca uma linha do que ele estava fazendo. O arquivo existia e era lido
com sucesso - nao havia erro nenhum, so estava errado.

**Um nucleo dos 4 do Notebook queimava 24 h por dia.** 98,7% de CPU (44,42 s numa
janela de 45 s) e 2654 MB. `vision.fps: 15` da 66,7 ms de orcamento por quadro;
o custo real era 567-716 ms. Sobra sempre negativa, `sleep(0)`, o freio nunca
dormia. O freio existia e estava correto - era anulado por um numero pedido
acima do que o hardware entrega.

O reconhecimento facial era 78% desse custo (442-716 ms contra 125 ms do YOLO).
**As duas correcoes obvias foram medidas e reprovadas:** baixar
upsample/resolucao acha 0 rosto onde a config atual acha 1; e usar o YOLO como
porteiro perderia rosto em 15 das 34 fotos reais (rosto em close, sem corpo a
mostra). O que funcionou foi separar os RITMOS - YOLO em todo quadro,
reconhecimento a cada 3 - **junto com** `fps: 15 -> 2`. Sozinha, a separacao nao
devolveu CPU nenhuma (99,4% antes e depois): com o orcamento ainda impossivel, o
laco so passou a processar mais quadros. Resultado: **CPU 98,7% -> 72,8%, RAM
2654 -> 2065 MB, e 1,8 -> 2,0 quadros/s** (mais leve E mais rapido).

Achados que ficaram abertos: 9 topicos publicados no vazio cujos unicos
assinantes sao os TESTES (entre eles `database.backup_failed`, ou seja, backup
falha em silencio); `boot.py` declarando 4 etapas como "nao implementadas" que
estao todas em producao, e publicando 4 `diagnostic.error` falsos por boot; a
Sentinela com 37 fotos, 3 delas do proprio dono salvo como "estranho"; e o
Ollama ainda `enabled` no systemd com 4,1 GB de modelos, apesar de removido tres
vezes na decisao.

### Parte 2 - a missao da fala

**T0 primeiro, como a missao mandava - e ele desmentiu o diagnostico.**
`tools/medir_conversa.py`, 20 turnos com entrada de texto fixa (o canal de audio
fica de fora de proposito: com rms 0,97x o robo responde a transcrito parcial, e
misturar as duas frentes tornaria qualquer melhora nao atribuivel):

    aberturas distintas ..... 18/20      (T3 pedia >= 15)
    palavras (media/p90) .... 13,8/25,5  (T2 pedia media <= 15)
    com saudacao ............ 2/20       (T1 pedia no maximo 1)
    latencia 1o audio ....... 2,35 s
    latencia fim da fala .... 6,66 s

T1, T2 e T3 ja estavam praticamente resolvidos pelos commits de 29/07. A missao
foi escrita sobre um diagnostico anterior a eles. E `SaudacaoEspontanea` - o
caminho separado, que dispara ao VER a pessoa - ja tem janela anti-repeticao de
30 min: implementar T1 como escrito criaria um segundo mecanismo de 20 min por
cima de um de 30 que funciona. Era a camada empilhada que o dono aponta como o
defeito recorrente do projeto.

Com a medida na mao, o dono redirecionou para o que sobrou de verdade: a
latencia.

**T5.a - a resposta sai em frases.** `AiManager.responder_em_frases()` consome o
stream e entrega frase a frase, para a fala comecar antes do fim da geracao.
Medido pareado, com o prompt real e ordem alternada: **3,33 s -> 1,84 s, -45%**
(o alvo era 40%).

Tres erros meus no caminho, todos pegos por medicao e nenhum por teste:

1. `asyncio.Queue` nao e thread-safe. A lib da OpenAI e sincrona, o produtor
   roda em thread, e `put_nowait` de la nao acorda o laco. As tres frases
   chegavam no mesmo instante, com todo o codigo de streaming no lugar e ganho
   zero. Nada levantava excecao.
2. O corte testava `endswith(".")`, e o modelo entrega tokens como ". Se voce" -
   com texto depois da pontuacao. O acumulado nunca terminava em ponto e a
   primeira frase so saia no fim do stream.
3. **Erro de metodo, o que mais importa:** a primeira medicao deste ganho deu
   60% e estava inflada - comparei os dois caminhos com o prompt CURTO (16
   linhas), enquanto producao monta 4045 caracteres, e prompt maior atrasa o
   primeiro token. O numero honesto e 45%.

### Onde errei

- Afirmei que `fps: 8` resolveria a CPU "com uma linha", a partir dos 102 ms que
  o proprio `orion.yaml` citava para o YOLO. O custo real do quadro era 567 ms,
  e o YOLO nao era o gargalo - o reconhecimento facial era. So a medicao mostrou.
- Propus usar o YOLO como porteiro do reconhecimento facial. Se eu tivesse
  aplicado sem testar, o robo teria deixado de reconhecer gente em 44% dos casos,
  em silencio. A ideia esta reprovada por medida, e registrada para ninguem
  refazer o caminho.
- Gravei LF num arquivo que estava em CRLF, e uma edicao de 8 linhas virou um
  diff de 1427. Resolvi a causa (`.gitattributes`) em vez de so desfazer.
- Deixei o shell comer um `sleep(0)` dentro de um heredoc por ssh, corrompendo um
  comentario recem-escrito. O projeto ja tinha registrado esse mesmo padrao com
  crases em mensagem de commit.

### Estado pela rubrica

Nada aqui vira verde. Sao mudancas validadas em suite e, no caso da visao,
medidas em producao com o servico reiniciado - mas o gate de hardware datado por
modulo nao foi rodado. **Tudo 🟡 no melhor caso.** `responder_em_frases` esta em
🟠: existe, tem teste e tem medida, mas ainda nao esta ligado no Voice Core.

### Fechamento da missao da fala (2026-07-30, fim da noite)

`b3d19fa` ligou o streaming por frases no caminho real da voz, e com ele a
missao fecha. Estado final: as duas maquinas em `b3d19fa`, 630 testes no
Notebook e 538 no Pi, arvore limpa nas duas.

**T5.a esta em producao e medido:** 3,33 s -> 1,84 s ate o primeiro som (-45%,
contra os 40% pedidos). O ganho nao veio de gerar mais rapido, e sim de parar de
esperar o que ainda nao era preciso - o robo diz a primeira frase enquanto o
modelo escreve a segunda.

Tres decisoes deste ultimo trecho que valem ficar registradas, porque nenhuma
delas e obvia lendo so o codigo:

- os caminhos CURTOS (comando, hora, diario) saem em UMA frase de proposito.
  Sao texto pronto: nao ha o que transmitir em pedacos, e picotar uma frase de
  seis palavras deixaria a fala pior, nao melhor;
- o registro da interacao acumula as partes e grava o texto INTEIRO no fim.
  Gravar frase a frase encheria o historico de fragmentos e estragaria o
  `conversas_recentes` que volta no contexto da pergunta seguinte;
- falha no meio tem DUAS politicas opostas, e e proposital: sem nenhuma frase
  dita, o robo pede desculpa (ficar mudo seria pior que a lentidao que este
  trabalho veio resolver); com alguma frase ja dita, ele cala (emendar
  "desculpa, tive um problema" em cima de uma resposta que fazia sentido soa
  pior que simplesmente parar).

**T5.b (ack conversacional) NAO foi feito, e a decisao e essa.** Ele existia para
cobrir a espera. Com 1,84 s ate o primeiro som, um "recebi" antes da resposta
vira ruido - e a propria missao avisa que sinal fixo reabre o problema de T1.
Reabrir so se o uso mostrar falta.

**T1 e T3 nao foram implementados, por medida e nao por falta de tempo.** O T0
mostrou 18/20 aberturas distintas (o criterio pedia 15) e 2/20 respostas com
saudacao. Alem disso `SaudacaoEspontanea` ja tem janela anti-repeticao de 30
min: T1 como escrito criaria um segundo mecanismo de 20 min por cima de um que
funciona. Era a camada empilhada que o dono aponta como o defeito recorrente do
projeto - implementar teria sido produzir exatamente o problema que a regra
existe para evitar.

**T2 e T4 seguem abertos**, e a razao e diferente para cada um. T2 tem so um
alvo residual: o p90 de 25,5 palavras vem de respostas longas e genericas
("Estou aqui para cuidar da casa e da familia" aparece em 3 dos 20 turnos) - o
alvo e a resposta vazia, nao a media, que ja passa. T4 nao foi tocado porque o
criterio de saida dele e explicitamente de OUVIDO ("verificacao de ouvido, nao
por regex") e exige o dono presente ouvindo 20 respostas sintetizadas.

Sobre a voz: existem 5 vozes pt no Piper e o projeto so tinha uma. Baixei `cadu`
e `jeff` (`data/piper_voices/`, 122 MB), geradas na mesma frase e ouvidas pelo
dono. **Ficou na `faber`**, a que ja estava - as outras duas seguem no disco
caso ele queira reavaliar.

Uma nota sobre o metodo, porque foi o que deu valor ao dia: a missao chegou com
o diagnostico pronto, e o T0 - que ela mesma exigia rodar primeiro - o
desmentiu. Tres das seis tarefas ja estavam resolvidas por commits de 29/07. O
instrumento de medida se pagou antes de qualquer linha de codigo mudar, que e
exatamente para isso que ele estava na frente da fila.

### O ultrassom traseiro descarta 12% das leituras (2026-07-30, achado de bancada)

Investigado antes de religar os motores, a pedido do dono. Comecou com um susto
que a medida desfez, e terminou num achado real de menor gravidade - mas
tambem num de projeto, que e mais serio que o original.

**O susto:** a telemetria mostrava `traseiro_descartes: 61293` contra
`frontal_descartes: 19`. Uma diferenca de 3200x parece defeito grave.

**A medida:** duas amostras com 21 s de intervalo deram 61351 -> 61392, ou seja
**~2 descartes por segundo**, contra ZERO no frontal na mesma janela. O servico
estava de pe havia 8h09.

**A explicacao, que inverte a leitura:** os dois sensores nao sao comparaveis em
numero absoluto, porque nao tentam a mesma coisa. O traseiro e sondado a CADA
iteracao do `loop()` (main.cpp, `ultrassomTraseiro.atualizar()`), a
`INTERVALO_ENTRE_LEITURAS_US = 60000` - **16,7 leituras por segundo**. O frontal
nao tem chamada propria no loop: ele e sondado dentro do `radar.atualizar()`, no
ritmo do servo, e portanto tenta muito menos. O traseiro nao esta pior; ele
tenta muito mais.

**A taxa real, que e o numero que importa: 2 / 16,7 = ~12% de perda.**

E nao e sensor ruim. O proprio firmware ja dizia onde olhar, no comentario do
getter: *"numero alto aqui aponta para quem esta bloqueando o loop, nao para
sensor ruim"*. O descarte acontece quando o loop fica cego por mais de
`SONDAGEM_MAXIMA_US = 600 us` durante o eco. O maior bloqueador e conhecido e ja
foi medido pelo projeto: **o envio da telemetria custa ~88 ms**. Com sondagem a
cada 60 ms, toda leitura que cai dentro de um envio se perde.

Importante para dimensionar: o firmware **nunca inventa**. Descarta a amostra e
mantem o valor anterior ate a proxima leitura. Os 12% sao dado defasado, nao
dado falso - a diferenca e o que separa degradacao conhecida de falha.

**O achado mais serio veio de lado, e e de projeto, nao de sensor:** a re nao
tem checagem de obstaculo NENHUMA. `MOVE_DISTANCE` com distancia negativa - o
comando da re do joystick - fica de fora de `COMANDOS_QUE_AVANCAM` de proposito,
e o `SafetyManager` do Mega so olha o radar frontal. Esta escrito no
navigation_core.py: *"checar obstaculo A FRENTE antes de andar PARA TRAS nao
faria sentido"*. Correto como raciocinio, e o resultado e que andar de re e hoje
o movimento MENOS protegido do robo - e o unico sensor que cobriria isso e
justamente o que perde 12% das leituras.

Com as rodas no ar isso nao tem consequencia. Com motor no chao, tem.

**Nao corrigido nesta sessao, e a razao esta registrada:** consertar o descarte
mexe no firmware e exige gravar no Mega (parar o orion-motion, avrdude), o que
e sessao propria. E a re sem protecao e decisao de arquitetura, nao bug - pede
EDR, nao remendo.


---

## Sessao de 2026-08-02 - o corpo morreu e voltou, e a conversa aprendeu a lembrar

Sessao longa, duas frentes: **hardware** (o Mega morreu e foi substituido) e
**conversa** (memoria, persona, sessao). O fio que costura as duas: em quase
todo defeito desta noite, o codigo certo JA EXISTIA e as pontas e que nao se
tocavam.

### O Mega morreu - o corpo migrou para o ESP32

Diagnostico inicial: o Pi estava **sem nenhuma `/dev/ttyUSB*`**, o Motion Core
rodava havia 3h26 falando sozinho, e a propria IA registrava no log *"perdi
contato com meu corpo"*. Nao era preferencia de plataforma: era migracao
forcada.

Placa nova: **ESP32-D0WD-V3** (DevKit 38 pinos, 240 MHz, dois nucleos, 4 MB,
USB por CP2102). Commit `0c9e9ba`.

**Compila, grava e FALA O PROTOCOLO** - 12 quadros JSON validos com checksum
em 6 s a 250000 baud. RAM 15,1%, flash 69,3%.

Quatro bugs invisiveis que so existiam por causa da troca, e nenhum daria erro
de compilacao:

| O que | Por que era invisivel |
|---|---|
| EEPROM sem `commit()` | Grava, le certo na mesma execucao, **some no reset**. Perderia calibracao da IMU e da bussola |
| ISR sem `IRAM_ATTR` | Reinicia so quando uma interrupcao cai durante escrita na flash |
| `Wire.begin()` sem os pinos | I2C fica **mudo sem erro** - so `imu_conectado: false` |
| Divisor da bateria 100k/27k | Entregaria 4,46 V num pino de 3,3 V (nunca foi montado, sem dano) |

O comentario em `bussola_manager.h` que dizia *"commit() e coisa de ESP8266;
no AVR a escrita e direta"* estava **certo** - e virou falso no instante em que
a placa mudou.

WiFi e Bluetooth **desligados** no setup, por tres motivos somados: energia
(era o motivo declarado da troca - com radio ligado o ESP32 fica PIOR que o
Mega), o ADC2 volta a existir, e a pilha de WiFi para de roubar CPU dos pulsos
de STEP.

**Riscos abertos, que o software nao resolve:** as entradas opto dos TB6600
foram dimensionadas para 5 V e o ESP32 entrega 3,3 V (corrente do opto cai de
~14 mA para ~8 mA - so a bancada responde); e o ECHO dos HC-SR04 entrega 5 V e
QUEIMA GPIO, entao os dois ficam **desplugados de vez** (o LiDAR ToF os
substitui - nao vale divisor numa peca com data de saida).

### A pose parou de mentir

Era o defeito mais grave do projeto: a interface afirmou **85 cm e 80 graus com
os motores fisicamente desligados**, porque integrava passos COMANDADOS.

Firmware (`b1a84e7`) + odometria (`3c5c298`). O `EncoderManager` foi reescrito
e a telemetria passou a carregar `pulsos_esquerdo`/`pulsos_direito` no pacote
REDUZIDO. Antes ele contava pulso por interrupcao e **nao havia campo na
telemetria nem chamada no loop()**: contagem perfeita, guardada para ninguem.

Montagem definida pelo dono: **sensor indutivo NPN + 6 chapas por roda, uma
delas MAIOR**. A chapa maior nao e enfeite - com seis iguais, um pulso perdido
fica na conta para sempre sem nada denunciar; a maior produz um pulso mais
LARGO uma vez por volta, e entre duas marcas tem que haver 6 pulsos. O que
divergir vira `encoder_desalinhados_*`, que e o termometro de saude da medida.

Tres comportamentos: com pulso e com calibracao a pose vem do pulso; com pulso
e sem calibracao a pose ainda vem do passo **mas nao avanca se a roda nao
girou**; sem pulso, `rodas_confirmadas: None` - "nao sei" nao pode virar False.

**Falta medir** `encoder_pulses_per_meter` (empurrar 1 m com os motores
desligados e ler o contador). Esperado ~29 pulsos/m.

### GOAP - o robo planeja em sequencia

Commit `fbe7a14`. A `IaEstrategica` escolhia UMA acao; agora a IA escolhe um
OBJETIVO e o planejador monta o caminho:

    cego, quer andar  ->  observar_ambiente -> virar_esquerda
    corpo mudo        ->  SEM PLANO

**Substitui** a escolha unica, nao empilha em cima dela. Duas regras
verificadas no import: todo predicado tem que sair de um campo real do
`RetratoDoMundo`, e toda acao com `move_rodas` exige corpo vivo, nivelado, sem
impacto e bateria ok - esta segunda nasceu de um erro meu que o TESTE pegou
(virar_esquerda movia as rodas sem exigir bateria).

**Ainda nao ligado na IaEstrategica** - o planejador esta completo e testado,
mas quem consulta o modelo continua pedindo uma acao.

### A conversa - quatro consertos encadeados

**1. Memoria de curto prazo** (`f0a60ad`). Nao era memoria curta, era memoria
NENHUMA: os tres caminhos que falam com o modelo montavam `messages` com
system + user e mais nada. Toda pergunta chegava como a primeira frase da
vida. Esquece por TEMPO (10 min), e `decidir()` nao entra no historico.

**2. Persona** (`b44d57e`). A causa do "ele responde e morre" estava no proprio
prompt: *"responde com frases curtas e claras"*. Nao faltava instrucao,
SOBRAVA uma. O ajuste separa curto de fechado. Junto vieram os limites do
corpo (ele **nao tem bracos**) e a ordem de narrar o que faz.

**3. O eco** (`1142281`). O robo estava ouvindo a PROPRIA VOZ e gravando como
fala do dono:

    22:50:15  ouvi: 'Como e que voce esta hoje?'   <- ele mesmo
    22:50:15  diario: pedido_ouvido {...}          <- como pedido DO DONO

Explicou os quatro sintomas de uma vez ("nao puxou assunto, falou 3 vezes, nao
entendeu, disse que eu tinha dito a mesma coisa e tal hora"). A causa nao era
buffer sujo - `gravar()` ja limpa. Era **audio em transito**: o ESP32 manda
62,8 KB/s por WiFi, entao quando o alto-falante cala ainda ha audio viajando.
Conserto: esperar 0,9 s depois de falar.

**4. Memoria de longo prazo** (`30ce49e` + `17ba7fa`). A cadeia inteira ja
existia e estava rompida em dois pontos: a busca de conversas vivia dentro de
`if pessoa_id is not None` (e as 146 linhas do banco tem todas `pessoa_id =
None`, porque ele conversa por voz sem reconhecer rosto), e
`conhecimento_relevante` era `ORDER BY criado_em DESC LIMIT 20`. E ninguem
escrevia fatos. O extrator novo roda **fora do caminho critico**, em lote de 4
turnos.

**5. Duas correcoes achadas testando** (`55e4873`). Mandando uma conversa
direto ao modelo, sem microfone: ele nao lembrava do que fora dito dois turnos
antes, porque o prompt dizia que o bloco de fatos era "a unica fonte de verdade
sobre o mundo" e ele lia "lembrar" como consultar registros. Corrigido
separando as duas fontes. Isso causou uma regressao - o extrator passou a
guardar "hoje estou muito cansado" - porque a INSTRUCAO de extracao viajava por
baixo da persona conversacional, que ganhava dela. Extrair nao e conversar:
agora o extrator manda o proprio prompt de sistema.

**6. A sessao de conversa** (`541992f`). Relato do dono: *"ta horrivel"* - tinha
que dizer "Fofao" antes de cada frase. **O log desmentiu o Portao 1**: fala em
rms 0,0309-0,0602 contra silencio em 0,0105-0,0164 (3 a 5x, e nao os 0,97x
registrados), com transcricoes limpas. Ele ouviu tres frases seguidas e
descartou as tres por nao terem a palavra de ativacao. Agora ela ABRE uma
sessao; dois silencios (~10 s) encerram. So e seguro por causa do assentamento
pos-fala. Junto, um detector de alucinacao do Whisper (o `'O que e o que e...'`
repetido ~60 vezes).

### Decisoes de rumo

- **NAO reescrever o ORION como pacote ROS 2.** O dono trouxe um roteiro de
  `ros2 pkg create` que refaria o projeto inteiro; alem de o ROS 2 nao ter
  binario para Debian 13 trixie, seria descartar 676 testes. O caminho e
  **ponte**: Nav2 em Docker e dois nos (um publica `/scan` e `/odom`, outro
  traduz `/cmd_vel`).
- **NAO adotar a "arquitetura em 5 camadas"** trazida de outra IA: 4 das 5
  pecas ja existiam. Aproveitadas so a persona e a tabela de fatos. O intent
  classifier foi recusado - casa por SUBSTRING, o mesmo bug que o
  `contrato.py` ja registra ("'parar' casa dentro de 'preparar'").
- **ChromaDB recusado** pela RAM do Notebook (7,6 GB ja com YOLO +
  reconhecimento facial + Whisper; o Ollama local ja foi removido por travar a
  maquina). Busca por LIKE hoje; FTS5 no MESMO arquivo se faltar.

### LiDAR STL-50B2 - as cores dos fios mentem

Foto do anuncio: **vermelho = Time sync** (nao alimentacao) e **preto = RX**
(nao terra); 5 V e o AZUL, GND e o VERDE. Ligar no reflexo poe 5 V no pino de
sincronismo. Terceira vez que o cabo mente neste projeto (HC-SR04 com
TRIG/ECHO invertidos, servos pan/tilt cruzados). Falta comprar conversor
USB-TTL - **CH340, nao CP2102**, porque o ESP32 do corpo ja e CP2102 com
serial de fabrica `0001` e os dois se confundiriam no boot.

### Estado ao fim da sessao

- Pi e Notebook sincronizados em `541992f`; **676 testes passam**.
- `orion-motion` e `orion-avatar` ativos.
- No ar: corpo ESP32, odometria honesta, memoria curta e longa, persona,
  extrator de fatos, sessao de conversa.

### Proximo passo

1. **Bancada** (depende do dono): medir consumo ANTES de religar tudo (o Mega
   pode ter morrido de corrente anormal, e o ESP32 e menos tolerante); testar
   TB6600 a 3,3 V; instalar os encoders (GPIO 15 e 16) e medir pulsos/metro.
2. Ligar o GOAP na `IaEstrategica`.
3. Ligar o reconhecimento facial ao `pessoa_id` das conversas - hoje grava
   `None` sempre, entao a memoria e da casa e nao de cada pessoa.

## Sessao de 2026-08-03 - o log gritava software, e o defeito era qual cabo estava em qual porta

Sessao inteira de diagnostico, com **uma unica linha de codigo mudada no fim**.
O fio que costura tudo: quase todo teste que eu fiz media a coisa certa e
respondia a pergunta errada - e o dono viu antes de mim.

### O sintoma

Desde as 00:05 o Motion Core empilhava centenas de linhas por minuto:

    ACK nao recebido de 'hardware_core' (tentativa 3/3, msg_id=...)
    Heartbeat perdido: modulo='hardware_core'
    motion_core: nao consegui mover o pan/tilt (pan=0.0 tilt=0.0)

O palpite natural (do dono, e razoavel) foi *"as mudancas de ontem a noite"*.
Nao era: dos 14 commits de 02/08, nenhum tocou a serial - o unico que mexeu em
`config/orion.yaml` foi o `3c5c298`, no bloco de **odometria**. `baud_rate`
seguia 250000 e `port` seguia `"auto"`.

### A causa: `"auto"` nao escolhe, pega a primeira

O Mega morto **continuou plugado no Pi** enquanto o ESP32 - o corpo novo -
ficou no Notebook, sem ninguem falando com ele. E `port: "auto"` varre
`PORTAS_SERIAIS_CANDIDATAS` em ordem (`motion_core/__main__.py:76`), com o
CH340 do Mega enumerando como `/dev/ttyUSB0`.

Ou seja: **o robo adotava o cadaver**, e o `WHO_ARE_YOU` falhava exatamente
como falharia com um bug de protocolo. O sintoma era de software; a causa era
qual cabo estava em qual porta.

`"auto"` foi escrito quando havia UMA serial no Pi. Com duas, ele nao decide.

### O conserto

`communication.arduino.port` deixou de ser `"auto"` e passou a ser o caminho
**by-id** do CP2102 do ESP32. O by-id e montado pelo udev a partir dos
descritores USB, entao aponta para a **placa** e nao para a ordem de
enumeracao - com os dois conversores ligados, so o certo casa. Mesma licao que
a camera ja tinha aprendido quando trocou `/dev/video0` por um caminho estavel.

**Nao houve codigo novo.** `_conectar_arduino` ja tentava so a porta
configurada quando ela nao e `"auto"` (`__main__.py:115`), e
`tools/_serial_bancada.py` ja lia a mesma chave - as ferramentas de bancada
seguiram junto sem uma edicao. Se a porta nao existir, a conexao devolve False
e loga (Cap 6 s.8): o robo sobe sem corpo, em vez de nao subir.

Commit `3400cbb`. Medido depois:

| | antes | depois |
|---|---|---|
| ACK/heartbeat perdidos | centenas por minuto | **0** |
| link | placa morta | `Arduino conectado e confirmado` no CP2102 |
| `SCAN_FRONT` | sem resposta | varredura dos 7 angulos completa |

As distancias voltam `-1` e isso e o esperado, nao defeito: os HC-SR04 seguem
desplugados de proposito.

**Teste ao contrario, para nao ficar so na afirmacao:** apontar a config de
volta para o by-id do Mega reproduz o defeito original na hora
(`Arduino nao encontrado` + ACK perdido). Config restaurada em seguida.

### O Mega: o chip estava VIVO, quem morreu foi a UART do CH340

Aqui esta o erro da sessao, e ele e meu.

Eu declarei a placa morta com base em cinco caminhos independentes de
silencio: leitura crua em 3 bauds, `avrdude` do sistema, `avrdude` com **dez
resets manuais** dados pelo dono, `avrdude` do PlatformIO, e leitura de flash
e EEPROM - tudo isso em **duas portas USB diferentes**. Um chip vivo teria
devolvido a assinatura pelo bootloader, que e independente do sketch e de todo
o codigo do ORION.

So que o dono observou duas coisas que eu nao podia ver daqui:

1. o pino do rele **mudou de estado** no power-on;
2. o **servo do radar varre** - e o ESP32 estava VAZIO, sem nada ligado nele.

As duas exigem codigo executando. E o firmware confirma: `setup()` chama
`radar.iniciarVarredura()` e `loop()` chama `radar.atualizar()` - a varredura
e **autonoma, nasce no boot e nao depende de serial nenhuma**.

Diagnostico correto: **o ATmega2560 esta vivo e rodando o firmware. O que
morreu e o lado UART do CH340.** Isso encaixa em cada medicao, inclusive nas
que eu li errado - o CH340 ENUMERA no USB (lado USB vivo, aparece no `lsusb`),
mas nao passa um byte em nenhuma direcao. Por isso o HEARTBEAT de 1 s nunca
chega, e por isso o **bootloader tambem** parece morto: ele fala pela mesma
UART quebrada. Foi exatamente ai que eu tirei a conclusao errada.

**O erro metodologico, que vale mais que o diagnostico:** mandei `SCAN_FRONT`
pelo ESP32, o dono viu o servo girar, e eu li isso como prova de que o servo
estava no ESP32. Nao provava nada - a varredura do Mega roda o tempo todo e ia
coincidir com qualquer coisa que eu fizesse.

> **Um teste so vale se o resultado for DIFERENTE nas duas hipoteses.**

Isso e irmao do bug recorrente de publicar evento no vazio: nos dois casos a
ausencia de falha foi lida como sucesso.

### O que continua valendo dos testes

Uma pergunta do dono no fim expos um furo real: eu tinha testado o bootloader
so em 115200. Varri entao programador x baud x chip; 10 das 24 combinacoes
sairam antes da decisao de abandonar a placa, **todas `timeout`**.

E o argumento que fecha nao e o log, e este:

> **Baud errado da lixo, nao silencio.** Se houvesse sinal na linha e eu
> escutasse na velocidade errada, chegariam bytes embaralhados. Zero byte em
> 250000, 115200 e 9600 significa que nao ha atividade eletrica no fio -
> nenhuma configuracao de software produz isso.

**Nao ha backup da flash do Mega e nao havera:** ler e gravar entram pela mesma
porta, entao `avrdude -U flash:r:` falha pelo mesmo timeout que gravar.
Diferente do ESP32-CAM, cujo esptool conseguiu dumpar a flash antiga. Nada se
perdeu de verdade - o fonte do firmware esta no git em `0c9e9ba^`.

### Decisao do dono: abandonar o Mega

*"abandono o mega"*, *"foca no esp32 mesmo"* - tomada **sabendo** que o
ATmega estava vivo e que existia caminho de recuperacao (conversor USB-TTL
externo nos pinos 0/1, ignorando o CH340 inteiro). Nao insistir em ressuscitar
a placa em sessoes futuras; o USB-TTL volta a ser so item do LiDAR.

### O que essa decisao custa - e nao e software

O dono revelou o dado mais importante da sessao: **o ESP32 esta VAZIO.** A
migracao de 02/08 foi so de **firmware**. O robo inteiro - servos (radar, pan,
tilt), TB6600, rele, I2C (MPU6050 + QMC6310), DHT22, encoders - continua
fiado no Mega.

Entao "focar no ESP32" e um trabalho **fisico**, nao de codigo. O software ja
esta pronto e provado.

### Estado ao fim da sessao

- Pi e Notebook sincronizados em `3400cbb` (fast-forward; a alteracao local
  do Notebook em `tools/conversar_fofao.py` - tirar o mic local de reserva -
  foi preservada, nao commitada).
- `orion-motion` ativo, 0 perdas de heartbeat, corpo respondendo.
- **Suite no Pi: 590 passam, 3 falham, 11 skip.** As 3 falhas sao `numpy` e
  `cv2` ausentes no venv do Pi (testes de voz e visao, cujas libs vivem no
  Notebook). Anterior a esta sessao, mas e divida real - a suite NAO passa
  limpa no Pi.

### Proximo passo

1. **Medir consumo ANTES de religar tudo.** A suspeita de que o Mega morreu
   por corrente anormal nao foi descartada - se ha falta na fiacao, ela ainda
   esta la, e o ESP32 e menos tolerante.
2. **Refiar os servos primeiro** (radar, pan, tilt): de graca, sem resistor, e
   provam a cadeia decisao -> firmware -> movimento com o robo parado.
3. **TB6600 a 3,3 V** - o unico risco que so a bancada responde. A pergunta e
   uma so: *o robo anda?* Se nao disparar, o conserto e um level shifter na
   linha PUL/DIR, **nao mexer no firmware**. Rodas no ar.

## Sessao de 2026-08-03 (tarde) - a fiacao muda de placa, e tres defeitos que so apareceram porque o robo passou a enxergar

De manha o assunto foi o corpo morto. A tarde o dono **refez a fiacao inteira
do Mega para o ESP32** - ele tem adaptador de bornes a parafuso, entao o que
parecia trabalho de dias foi de minutos. E aí quase tudo que estava dormente
acordou de uma vez: camera, servos, visao. Cada coisa que voltou a funcionar
revelou um defeito que ninguem via enquanto estava tudo desligado.

### Ver o Fofao de fora de casa

Tailscale instalado no Pi (`fofao-pi`, 100.112.10.2, MagicDNS ligado). Nada
de abrir porta no roteador.

| o que | onde |
|---|---|
| interface | `http://fofao-pi:8080` |
| camera, 1 quadro | `http://fofao-pi:8091/snapshot` (~13 KB) |
| camera ao vivo | `http://fofao-pi:8091/stream` (~16 KB/s, ~58 MB/h) |

**Pegadinha da instalacao**, para nao repetir: `tailscale up` fica BLOQUEADO
segurando o handshake. Se o processo morrer - sessao SSH que fecha, um
`pkill` - a URL de autenticacao vira lixo e o node continua em `NeedsLogin`
mesmo depois do dono clicar. Rodar desacoplado:

    sudo sh -c 'setsid tailscale up --hostname=fofao-pi >/tmp/ts.log 2>&1 </dev/null &'

`tailscale ping` mostrou caminho DIRETO na LAN, com queda para relay (DERP
sao) fora de casa.

### A camera estava desplugada, e NADA acusava

Pedido do dono: *"uma pagina geral onde eu possa ter menus e acesso geral do
robo de qualquer lugar"*. A pagina ja existia - o dashboard ja tinha nav,
viewport e o volante. Faltava a camera, que vivia sozinha na porta 8091:
`grep -r "8091|/stream|snapshot"` em `motion_core/webui/static/` dava VAZIO.

Ao ligar o painel, o dono viu "no signal". E aqui esta o achado que importa
mais que o painel:

    systemctl --user is-active orion-camera-stream  ->  active
    curl :8091/snapshot                             ->  HTTP 200, 13845 bytes, image/jpeg

Servico ativo, HTTP 200, JPEG valido. **A camera estava fisicamente fora do
robo.** O `ustreamer` roda com `--persistent` e, sem device, serve uma imagem
"NO SIGNAL" com 200 - o `onerror` do navegador nunca dispara. A unica pista
era o tamanho: **13845 bytes exatos, sempre iguais**. Com a camera de volta:
10106 / 10158 / 10123, tres hashes distintos.

Familia de bug ja registrada aqui varias vezes: **ausencia de erro lida como
sucesso**.

No painel, o host NAO e configuravel de proposito - o navegador monta a URL
com o `location.hostname` da propria pagina, entao serve na LAN e pelo
Tailscale sem reconfigurar. Fixar o host quebraria justamente o acesso
remoto. FOTO e o padrao, por custo de dados. Commit `d17dbcf`.

### "Os servos estao loucos" - eram TRES causas empilhadas

Comecou no instante em que a camera entrou no ar. Cada correcao revelava a
proxima.

**1. A visao estava cega havia 3 horas.** O Vision Core subiu as 06:44, antes
de a camera existir, e ficou agarrado ao stream que servia o "NO SIGNAL".
Reiniciar o servico levou de **0 para 18 `vision.person_detected` em 30 s**.

**2. A cabeca trocava de dono a cada piscada.** Medido: `acompanhar_pessoa`
assumia e soltava a cada 2-5 s com o dono parado na frente da camera. O
`vision.person_lost` tinha um "caminho rapido" que largava a cabeca no ato -
so que perder o alvo por um instante e NORMAL. Cada solta devolve a cabeca ao
`Repouso`, que publica pan=0/tilt=0: a cabeca ia a zero de tranco e a visao
pegava de volta calculando a partir de uma posicao que ja nao era a real.
Agora `person_lost` e informativo e quem decide e o silencio, que subiu de
3 s para 6 s. Commit `7435f06`.

**3. O controlador perseguia pixel.** Com a cabeca finalmente segura, sobrou
a malha da visao. `calcular_correcao` corrigia em TODO quadro, por menor que
fosse o erro - com a pessoa ja centralizada, a caixa do YOLO oscilando 2 ou
3 px virava comando de servo a 2 Hz. Isso nao e seguir alguem, e tremer.

E o detalhe que define a tarde: **a tolerancia que resolve JA EXISTIA** -
`alvo_centralizado()` e `tolerancia_centralizado_percent: 5.0` - mas so era
usada por quem PERGUNTAVA se o alvo estava centralizado, nunca por quem
DECIDIA mover. O numero estava na config, documentado, e entrava na conta de
ninguem. Commit `c9df6dc`. Veredito do dono: *"SERVOS OTMO"*.

Correcao de registro: `CalculadoraPanTilt.sincronizar()` ja esta implementado
E chamado (`vision_core.py:135`). A proposta que o journal de 29/07 deixou em
aberto sobre os "dois donos" ja foi feita - o journal e a memoria a davam como
pendente.

### Arduino sai do vocabulario vivo

*"arduino nao existe mais"*, *"apagar toda referencia"*. Virou
`hardware_core` e nao `esp32` de proposito: e como o modulo ja se identifica
em toda a troca de mensagens e e agnostico de placa - trocar o hardware de
novo nao vai obrigar a renomear tudo outra vez, que e exatamente o que acabou
de acontecer.

**O criterio, que vale para a proxima vez: corrigir o que hoje e FALSO,
preservar o que registra POR QUE as coisas sao como sao.** Este journal fica
intacto - o Mega existiu de verdade ate 02/08. E o comentario que explica o
`baud_rate: 250000` pelo buffer de 64 bytes do Mega fica tambem: apagar o
Mega dali deixaria o numero orfao. Commit `fe162f5`.

### O I2C nao sobe - e a caca ensinou mais que o resultado

`i2c_total = 0`: IMU, bussola e DHT mudos. **Oito hipoteses eliminadas com
numero** - pinos, GND comum, 5 V vs 3,3 V, 400 -> 100 kHz, SDA/SCL
invertidos, bornes frouxos, varredura velha, e GPIO 21/22 danificados.

**O metodo:** firmware temporario media as linhas antes de o `Wire` assumir os
pinos - `pinMode(INPUT)` le, `pinMode(INPUT_PULLUP)` le. `solto=1` significa
pull-up externo; `pullup=0` significa linha presa no GND.

**E o CONTROLE foi o que salvou.** A primeira rodada deu `pullup=0` nas duas
linhas e eu quase escrevi "GPIO 21/22 queimados" com numero e confianca. So
nao escrevi porque medi tambem dois pinos livres (32/33) do mesmo jeito: eles
leram 1, o metodo se provou, e na releitura os pinos do I2C tambem leram 1.
**Medicao sem controle nao vale** - e a mesma licao que o teste do
`SCAN_FRONT` desta manha ja tinha dado.

Estado atual: linhas sadias, nenhum pull-up externo aparece, ninguem
responde. Eletricamente e como se **nao houvesse nada ligado nos pinos**,
mesmo com a MPU parafusada. Falta a medida que so o multimetro da: tensao
entre VCC e GND *da plaquinha* e continuidade GPIO 21 -> pino SDA do modulo.

### Dois erros meus na mesma hora

**Botei `escanearI2c()` no `loop()`** para o dono ver o barramento mudar ao
vivo enquanto mexia no fio. A ideia era boa; a conta que faltou: a varredura
percorre 112 enderecos e, com o barramento PRESO - justamente o caso que se
queria investigar - cada um gasta o timeout. Sao ~3 s numa volta do laco. **O
ESP32 ficou mudo e o corpo caiu.** Se voltar, sondar so 0x68/0x69 e 0x1C:
quatro transacoes, nao 112. *Ferramenta de diagnostico nao pode custar mais
que o defeito que investiga.*

**E culpei meu codigo cedo demais.** Reverti a mudanca - continuou mudo.
Reverti o arquivo inteiro para o commit - continuou mudo. So entao ficou
claro que nao era software. Tres gravacoes ate testar a hipotese que separava
as duas coisas.

### RESET POR DTR NAO E POWER CYCLE

Com o ESP32 travado no boot, **nada em software o trouxe de volta**:
reiniciar o servico, regravar o firmware e o pulso de DTR/RTS falharam todos.
A ROM mostrava boot normal (`rst:0x1 POWERON_RESET`, `entry 0x400805e4`) e
depois silencio - travava no `setup()`, no ponto em que varre o I2C.

Quem resolveu foi o dono: *"reinicia o rasp q ele reinicia tb"*. O ESP32 e
alimentado pela USB do Pi, entao `systemctl reboot` derruba a energia de
verdade. Voltou na primeira.

**Guardar: corpo mudo que nao volta com regravacao - reinicie o Pi antes de
suspeitar de hardware queimado.**

### Estado ao fim da sessao

- Pi e Notebook sincronizados; corpo no ar, 0 perdas de heartbeat.
- Fiacao TODA no ESP32. Servos, rele e camera funcionando.
- Acesso remoto por Tailscale, com a camera dentro do dashboard.
- **Suite no Pi: 590 passam, 3 falham** (`numpy` e `cv2` ausentes no venv do
  Pi - divida anterior, sem relacao com esta sessao).

### Proximo passo

1. **I2C**: multimetro na plaquinha - tensao VCC/GND e continuidade ate SDA.
2. Medir consumo antes de deixar tudo energizado por muito tempo.
3. TB6600 a 3,3 V - a pergunta continua sendo uma so: *o robo anda?*

## Sessao de 2026-08-03 (noite) - quatro defeitos que os testes nao pegavam porque os testes nao rodavam

Pedido do dono: *"faz uma revisao geral de tudo vamos melhorar em todos os
sentindo limpeza nos codigos e melhoria na conversacao e melhoramento do
goap"*, e logo depois *"enriquecer os codigos e ligar ao banco e a
inteligencia"*.

O primeiro comando foi rodar a suite. Ela nao rodava. E esse detalhe explica
todo o resto da sessao: **os tres defeitos seguintes estavam escondidos atras
de uma suite que nao chegava a existir.**

### A suite do Pi coletava 11 testes, nao 590

Nao 11 falhando - 11 COLETADOS. `tests/unit/test_fala_por_frases.py` importa
`voice_core`, que importa `captura_audio`, que faz `import numpy` no topo. O
Pi nao instala o extra `mission` de proposito. O `ModuleNotFoundError` subia
durante a COLETA, e erro de coleta **interrompe a suite inteira**, nao so o
arquivo culpado:

    11 skipped, 1 error in 1.81s
    !!!! Interrupted: 1 error during collection !!!!

O projeto ja tinha a resposta em 13 outros arquivos - `pytest.importorskip`
antes dos imports pesados. Faltava em tres pontos, cada um com um extra
diferente: `numpy`/`sounddevice`, `cv2` e `ollama`.

Os dois de visao sao sutis. O `ModuloVisao` usa import tardio dentro de
`_construir` justamente para nao exigir cv2 - mas o `monkeypatch.setattr` do
teste aponta para dentro de `vision_core`, e **resolver esse nome importa o
modulo de verdade**. A protecao existia para quem importa o arquivo, nao para
quem mira o alvo.

**Nota de honestidade sobre a entrada anterior:** o registro da tarde diz
"suite no Pi: 590 passam, 3 falham". Hoje a medida deu 11 coletados. Depois de
recolocar a primeira guarda, deu exatamente 590/3 - o mesmo numero da tarde.
Ou seja: aquele numero foi medido antes de algo mudar, e ninguem reconferiu.
*Numero de suite envelhece como qualquer outro dado.*

Ao fim: **605 passando, 15 pulados, 0 falhando.** Commit `d9b1df4`.

### O GOAP tinha 500 linhas e ninguem o chamava

Escrito em 02/08 com testes, travas de import e um docstring afirmando *"isto
SUBSTITUI a escolha de acao unica"*. A substituicao nunca aconteceu: o unico
arquivo do repo que importava `modelo_goap` era `tests/unit/test_goap.py`.

**Codigo orfao nao e codigo pronto.** E o modo mais caro de errar aqui, porque
tudo parece feito - o modulo existe, os testes passam, o journal registra a
entrega - e o robo continua exatamente como antes.

*Guardar o cheque: antes de dar um modulo por entregue, `grep` por quem o
importa fora dos testes.*

A ligacao: `IaEstrategica` ganhou `retrato=` (o `EstadoDoMundo.retrato`,
passado no boot). Com ele, a IA passa a escolher um **OBJETIVO** e o
planejador monta a sequencia - executando **so o primeiro passo**, e
replanejando no ciclo seguinte. Efeito de acao e INTENCAO, nao promessa;
cumprir um plano de tres passos cegamente seria agir sobre um mundo que ja
passou.

Sem `retrato`, cai no modo antigo de acao unica. O conselheiro do Notebook nao
precisou de uma linha: ele so usa `opcoes` como enum do schema.

### Um objetivo que a IA podia escolher e que nao fazia nada

Varrendo os 2^8 mundos possiveis, `ficar_em_seguranca` **nao gerava plano em
nenhum**. Ele pedia `sem_impacto: True`, e nenhuma acao produz esse predicado
- impacto e leitura de sensor, nao consequencia de decisao.

O tamanho disso so aparece com o GOAP ligado. A IA escolhe entre quatro
objetivos, e *"ficar em seguranca"* e o nome mais atraente justamente quando
as coisas vao mal. Escolhe-lo fazia o robo **nao fazer nada, sem erro, sem
log, sem nada denunciando** - a mesma familia de "publicar no vazio" que este
projeto ja tomou tres vezes.

Agora o alvo e `parado`, que sai de `estado_hardware == "IDLE"` (a telemetria
reporta) e que `parar` de fato produz. Ha trava no import recusando qualquer
objetivo decorativo futuro.

Junto: a heuristica do A* **superestimava**. Ela contava predicados pendentes,
justificada por "cada acao muda pelo menos um predicado". O raciocinio esta
invertido - para nunca superestimar, precisaria valer que cada acao fechasse
no MAXIMO um predicado do alvo. E `olhar_em_volta` fecha dois de uma vez.
Hoje nenhum objetivo pede dois, entao nada muda de comportamento; so mordia no
futuro. Commit `eefdd6d`.

### 152 conversas, zero fatos

A tabela `conhecimento` estava vazia. Nao por falta de produtor: o
`ExtratorFatos` existia, estava ligado no `mission_planner` e funcionava. Ele
so nunca chegava a rodar. Duas causas somadas, ambas medidas no banco real:

1. **`encerrar()` nunca era chamado por ninguem.** O `conversar_fofao.py`
   encerra voz, ouvidos, visao, link e servidor - e nao o planner. Todo lote
   incompleto morria no desligamento.
2. **`turnos_por_lote=4` foi escolhido sem medir.** As 37 sessoes gravadas
   dizem outra coisa: **32 delas (86%) tem menos de 4 turnos.**

Juntas, garantiam o resultado observado: consumidor pronto (o `ai_manager` ja
injeta os fatos no prompt), produtor ligado, e nada passando.

O conserto e destilar tambem por **ociosidade**: 90 s sem turno novo encerram a
conversa e o que houver e destilado. O relogio conta SILENCIO - turno novo
rearma a espera, para nao cortar conversa pelo meio. Com um piso de
substancia, porque *"Fofao!"* sozinho e a sessao mais comum do banco e nao ha
fato dentro de um chamado, so custo de modelo.

**O FTS5 foi adiado de proposito.** Indexar uma tabela de zero linhas nao
entrega nada. Ele so vale depois de confirmar que os fatos acumulam -
conferir com `SELECT COUNT(*) FROM conhecimento`. Commit `77c7cba`.

### O linter escolhia as proprias regras

`[tool.ruff]` tinha `line-length` e `target-version`, e mais nada. **Sem
`select`, quem escolhe as regras e o padrao da versao instalada** - que muda
sozinho.

O ruff 0.16 ligou dezenas de familias que ninguem pediu (YTT, ASYNC, S,
BLE...) e DESLIGOU o `E402` - exatamente a regra contra a qual o repo escreveu
**105 comentarios `# noqa: E402`**. O resultado era um linter que se
contradizia: 105 avisos de *"noqa inutil"* apontando para comentarios que
estao certos, afogando os problemas reais. 275 avisos, quase nenhum acionavel.
**Linter que ninguem olha nao e linter.**

Regras fixadas no pyproject (`E F W I UP B`) e 55 correcoes seguras aplicadas
(8 imports mortos, sintaxe obsoleta). **275 -> 127**, e os 127 sao reais.

Nao automatizado: 33 `I001` (ordem de import). O isort moveria os imports para
CIMA do `pytest.importorskip`, quebrando o skip nas maquinas sem os extras - o
Pi, por exemplo. Seria reintroduzir por automacao o defeito que o primeiro
commit da sessao acabou de consertar. Commit `b552a1e`.

### Estado ao fim da sessao

- **Suite no Pi: 605 passando, 15 pulados, 0 falhando** (eram 11 coletados).
- **Linter: 127 avisos** (eram 275), com as regras escolhidas por nos.
- GOAP **ligado** na IaEstrategica; Motion Core importa limpo.
- Extrator de fatos com gatilho por ociosidade e `encerrar()` no desligamento.
- Quatro commits tematicos no master do Pi. Arvore limpa.
- CRLF entrou por `scp` do Windows em 3 arquivos e o `.gitattributes`
  segurou - conferido blob por blob, zero CR dentro dos commits.

### Proximo passo

1. **Bancada com o GOAP.** Ele move o robo por um caminho novo que passou nos
   testes e **nunca rodou no hardware**. Ligar com a mao perto do botao.
2. Conferir se os fatos comecam a aparecer: `SELECT COUNT(*) FROM
   conhecimento`. So depois disso o FTS5 passa a valer.
3. A conversacao em si (`ai_manager`, prompt, historico) segue intocada -
   nesta sessao foi consertado o ABASTECIMENTO dela, nao ela.
4. Continua de pe do dia anterior: multimetro no I2C, consumo, TB6600 a 3,3 V.

---

## 2026-08-03 (noite) — o robô passa a AGIR quando pedem, e a cumprir o que promete

Três pedidos do dono, na ordem em que ele os fez: *"o robô está muito de
conversação"*, *"ele tem que saber mexer a cabeça quando eu pedir"*, *"ele tem
que saber todos os comandos"* — e, no meio da sessão, o relato de uma falha que
tinha acabado de acontecer: *"pedi para avisar a Mara quando a visse que eu
queria falar com ela, ela foi até ele, ele cumprimentou ela mas não deu o
recado"*.

### O que o caminho falado alcançava (medido, não estimado)

Sete frases. `_PADROES_COMANDO`, no `mission_planner`: luz (acender/apagar),
parar, varrer, frente e os dois lados. O catálogo do robô (`contrato.py`) tinha
dezesseis ações, e a **cabeça não estava em nenhuma das duas listas**. Não
havia como pedir "olha pra esquerda" por voz — não por dificuldade, por
ausência.

### A troca: o modelo escolhe o NOME, o código decide o que ele faz

`src/orion/mission/acoes_faladas.py` — catálogo fechado de 14 ações, conferido
no import. O modelo começa a resposta com uma linha `#acao:nome` e o resto é o
que ele fala; o marcador é retirado antes de virar áudio.

É a regra 1 do contrato de autoridade aplicada ao caminho falado: *"a IA nunca
emite comando cru; ela escolhe um NOME deste catálogo"*. Nome fora do catálogo
não vira chute — vira nada, e a frase segue como conversa.

**Por que não mais regex.** Está escrito no próprio `contrato.py` que *"o
caminho antigo casava por SUBSTRING - e 'parar' casa dentro de 'preparar'"*.
Cada comando novo por padrão fixo seria mais uma armadilha dessas, e "olha pra
cima" tem variação demais ("levanta a cabeça", "olha pro teto") para caber em
regex.

**Parar continua sendo regex, e é a única.** Não é sobra do sistema antigo: é a
única ordem que não pode depender de a internet estar de pé.

**O marcador vai no COMEÇO** porque o robô fala frase a frase enquanto o modelo
ainda escreve. No fim, o servo só se mexeria depois de a fala acabar — a pessoa
ouviria "olhando pra esquerda" com a cabeça parada.

**Mover a cabeça são DUAS coisas**, e omitir a segunda é o bug conhecido do
projeto: manda `SET_PAN_TILT` e **avisa a visão** pelo barramento. Ela guarda
uma estimativa da posição dos servos e calcula a correção seguinte a partir
dela; quem mexe por fora sem avisar deixa essa estimativa mentindo — é o
"pan/tilt fica maluco depois de horas funcionando bem".

Os ângulos param em 60°, não nos 80° do limite: encostar no batente faz o servo
puxar corrente até travar, e foi assim que um servo em estol derrubou o ESP32
inteiro.

### O recado da Mara: nada estava quebrado, porque não havia nada

O robô respondeu que sim porque **um modelo de linguagem que ouve um pedido
gentil responde que sim**. Não havia tabela, não havia ação, não havia entrega.
É a mesma família de falha já catalogada aqui (publicar num tópico sem
assinante): **a promessa não falha, então nada denuncia**. Só que desta vez a
testemunha era uma pessoa esperando por um recado.

    quem PEDE    -> a conversa, no Notebook  (ação deixar_recado)
    quem GUARDA  -> o orion.db, no Pi        (tabela recados, migração 002)
    quem ENTREGA -> a saudação, no Notebook  (na CHEGADA da pessoa)

A entrega mora na `saudacao.py` porque ela já é o **único lugar do sistema que
distingue "estou vendo a Mara" (até 15x por segundo) de "a Mara CHEGOU"** — e
"quando a vir" quer dizer chegou. Um vigia separado reimplementaria essa
distinção, e o robô passaria a ter duas ideias de chegada. O recado sai DEPOIS
do cumprimento: ouvir um recado antes de um "oi" é o robô tratando a pessoa
como caixa de entrada.

Casamento por NOME e não por id — quem deixa o recado diz "a Mara" e quem chega
é reconhecido por nome. Primeiro nome, sem acento: "Marah" no `orion.yaml`,
"Mara" na fala e "mara" no banco são a mesma pessoa.

**O que mais importa aqui é o caminho da falha.** Se o banco recusar, o robô
NÃO devolve a frase do modelo — ela diz "combinado, eu aviso", que é exatamente
a mentira que custou o recado. Ele diz que não conseguiu guardar. Pelo mesmo
motivo, `deixar_recado` sem destinatário não vira ação nenhuma: prometer sem
poder cumprir é o defeito original com mais código em volta.

Entregue não apaga o registro (é a prova de que ele cumpriu) e só é marcado
DEPOIS de a fala terminar: se a voz falhar no meio, o recado volta na próxima
chegada. Repetir é chato; perder é quebrar a promessa.

### A cabeça fica com quem a virou

Vinha da sessão anterior, sem commit. O joystick "não funcionava" visto de fora
de casa: o dono virava a cabeça e ela voltava sozinha em segundos.
`observar_ambiente` varre de -60 a +60 e o GOAP escolhe esse objetivo a cada
~30 s. Quem mexe no joystick fica dono da cabeça por 10 s, e a janela **expira
sozinha** — um "modo manual" com botão teria o defeito oposto: o robô pararia
de olhar em volta para sempre porque alguém esqueceu de devolver o controle.

O limitador de taxa era MAIOR que o intervalo do joystick (150 ms contra
120 ms) e descartava quase um comando em cada dois — e como o comando é ângulo
absoluto, perder o último para a cabeça onde o penúltimo mandou, com a tela
mostrando outra coisa. Caiu para 100 ms.

### Pi e Notebook tinham divergido de novo

14 commits de um lado, 3 do outro. **Um único conflito**, trivial: cada máquina
acrescentou um import na mesma linha. As duas ficaram em `9bc84d0`.

Custou um susto no meio: copiei arquivos novos do Pi sobre a árvore velha do
Notebook e o `orion-avatar` entrou em laço de reinício (`ModuleNotFoundError:
orion.mission.quem_fala`). **Copiar arquivo solto entre máquinas que divergiram
não é deploy — é sorte.** Serviço recuperado em 35 s com `git checkout --`.

### Testes que mudaram de dono

Os seis testes que fixavam a regex foram reescritos para o contrato novo. As
duas regressões de 19/07 (`apague`/`desligue`) foram guardadas onde elas agora
moram: distinguir "apague" de "acenda" virou trabalho de quem entende
português; o que continua sendo nosso é cada nome levar ao comando certo.

Os três testes de schema conferiam a versão literal `1` e quebraram com a
migração 002. Passaram a derivar de `MIGRACOES` — **um teste que precisa ser
editado a cada migração não guarda a regra, guarda o dia em que foi escrito.**

### Estado ao fim da sessão

- **Suíte: 754 passam no Notebook, 657 no Pi, 0 falhando.**
- Migração 002 aplicada no banco de produção (`schema_version = 2`, tabela
  `recados` criada, 0 linhas).
- `orion-motion` e `orion-avatar` no ar com o código novo, ESP32 conectado.
- Commits: `6a0af79` (cabeça do operador), `a3b2b20` (ações faladas),
  `d1ca729` (merge), `9bc84d0` (recados).

### Falta CONFIRMAR NO HARDWARE (nada disto é 🟢 ainda)

1. **A cabeça vira para o lado certo?** O sinal do pan foi tirado do joystick
   da webui, não medido com a cabeça na frente. Se estiver espelhado, o
   conserto é uma linha em `acoes_faladas.py` — e está espelhado para todo
   mundo, não só aqui.
2. **O recado sobrevive a uma chegada de verdade?** Testado com fakes ponta a
   ponta; nunca com a Mara entrando na sala.
3. **O modelo escreve o marcador com que frequência?** Se ele esquecer, a ação
   não acontece e o robô só conversa — que é exatamente a queixa que abriu a
   sessão. Vale olhar `acao falada:` no log do Notebook depois de alguns dias.

### Pendências que continuam de pé

- O botão de desligar (`motion_core/desligamento.py` + 5 testes) segue **órfão**:
  escrito, testado, e ninguém o chama. Falta rota, botão e a regra de sudoers
  no Notebook (o `sudo` de lá pede senha; o do Pi não). O bloco
  `communication.notebook` já está no `orion.yaml`, sem uso.
- Multímetro no I2C, consumo, TB6600 a 3,3 V.
- Bancada com o GOAP: ele move o robô por um caminho que nunca rodou no
  hardware.

## Sessao de 2026-08-04 - varredura completa: nove defeitos que nao quebravam nada

Pedido do dono: *"faz uma varredura no orion via ssh e verifica todo o codigo,
busca por bugs e possiveis melhorias"*. Depois: *"faca seu melhor para
resolver"*.

O ponto de partida era bom, e e isso que define a sessao: **652 testes
passando, 0 falhando, nenhum TODO largado, ruff so com aviso cosmetico**. Nao
havia nada quebrado para achar. Entao a varredura foi atras do que nao aparece
em teste nem em log - as duas familias de bug que este projeto ja registrou
varias vezes: *publicar e ninguem escutar* e *ausencia de erro lida como
sucesso*.

Nove defeitos, dois commits, nove testes novos. **Nenhum deles quebrava nada** -
por isso estavam vivos.

### A ferramenta: um analisador de barramento

`grep` nao serve para esta pergunta. Metade dos `subscribe` deste projeto nao
tem o topico escrito ao lado: a webui e o avatar assinam por
`for topico in TOPICOS_REPASSADOS`, e o `comm.publish` monta o nome com
f-string. Um grep ve zero assinantes onde ha vinte, e vinte orfaos onde ha um.

Escrevi um analisador AST que percorre os 195 arquivos resolvendo constantes de
modulo, variaveis de laco e prefixos dinamicos. Foram tres versoes ate os
falsos positivos sumirem: 31 "orfaos" na v2 viraram 10 reais na v3, e desses,
tres eram bug de verdade.

**Vale guardar a ferramenta** (`tools/auditar_barramento.py`): esta
familia de bug ja mordeu quatro vezes e nao ha teste que a pegue, porque
publicar nunca falha.

### Os tres que o analisador achou

**1. A saudacao nunca chegou ao avatar.** `saudacao.py` publica
`voice.saudacao` ANTES de falar, e o comentario ao lado diz, com todas as
letras, *"o avatar reage ao evento"*. O topico nunca esteve em
`TOPICOS_REPASSADOS`. A legenda do cumprimento jamais apareceu na tela - desde
que a saudacao existe. Quarta vez.

**2. O que quebra nao chegava na tela.** `vision.camera_error`,
`voice.audio_error` e `diagnostic.alerta` nao tinham consumidor nenhum. Camera
caida, alto-falante falhando e alerta do guardiao de RAM so existiam no log do
Pi. A pagina DIAGNOSTICO tem uma lista de erros recentes que nunca recebeu
nenhum dos tres.

**3. `behavior.conselho` era falso positivo** - atravessa o link para o
Notebook. Registrado aqui porque o analisador vai reapontar isso toda vez.

### Os que vieram da leitura

**4. A consulta a IA podia morrer e nunca mais voltar.**
`asyncio.create_task(self._consultar_ia())` sem guardar referencia. O loop so
mantem referencia fraca - a documentacao do asyncio manda guardar o resultado
"para a tarefa nao desaparecer no meio da execucao". Se ela sumisse, o
`finally` que devolve `_consultando` para `False` nunca rodaria, e
`_pode_consultar_ia` passaria a recusar TODA consulta seguinte, para sempre,
sem uma linha de log. Falha silenciosa e PERMANENTE, no mesmo caminho onde 5444
decisoes da IA ja evaporaram uma vez (30/07). Mesmo defeito na tarefa da
saudacao.

**5. `comm.publish` iterava `self._links` vivo** com um `await` dentro do laco.
`adicionar_link` mexe nesse dicionario de outra tarefa a cada reconexao:
`dictionary changed size during iteration` no meio de um broadcast, justamente
quando o link esta instavel - que e quando o evento mais importa.

**6. O teto de sessoes podia deslogar quem acabou de entrar.** `set.pop()`
remove um elemento ARBITRARIO, e podia levar o token recem-criado. Virou dict
ordenado: sai o mais antigo.

**7. A varredura saia com o robo andando.** `_executar_segmento` ja esperava o
corpo ficar ocioso, mas o EXPLORE chama `_escanear_a_frente` direto no inicio
de cada passo - e o passo anterior termina mandando MOVE_DISTANCE, cujo ACK so
diz que o comando chegou. As leituras descreviam um lugar por onde o robo ja
tinha passado. **O log de producao tinha 16 `SCAN_FRONT` sem ACK**, que e o
sintoma exato de comando sobreposto a movimento. A espera foi para dentro de
`_escanear_a_frente`: quem varre quer o dado bom, sempre.

### As tres que dependiam de decisao (segundo commit)

**8. Dirigir pela rede nao pedia senha.** `/cabeca` exigia sessao e `/comando`
nao: virar a cabeca pedia senha, avancar e dar re nao. Nao foi decisao de
ninguem - foi o que sobrou de a protecao ter nascido junto com a pagina de
configuracao, e nao com o joystick.

**O STOP fica de fora, de proposito.** Quem ve o robo indo para um lugar ruim
precisa poder para-lo AGORA; exigir senha para PARAR trocaria um risco por
outro pior. Mesmo argumento que ja tinha tirado o STOP da disputa do volante.

Do proprio Pi nada muda (o kiosk e 127.0.0.1). De fora, o dashboard abre um
campo de senha reaproveitando `/api/configuracao/entrar` e o `.form-senha` que
ja existia. **O joystick nao repete o comando depois do login** - comando de
movimento guardado enquanto alguem digita a senha chegaria atrasado, que e o
pior comportamento possivel num controle manual.

**9. `segment_completed` dizia "completado" antes de andar**, e a pose andava
no reinicio da placa dizendo que nao andava. Neste segundo, `_delta_encoder`
logava *"resincronizando sem atualizar pose"* e devolvia `(None, None)` -
indistinguivel de "nao ha encoder", entao o chamador caia nos passos comandados
e somava a pose assim mesmo. **O teste novo mostra o tamanho: a pose pulava de
1,0 m para 2,0 m no quadro do reinicio.**

### Os testes foram postos a prova

Nove testes novos, e cada um foi conferido REVERTENDO o codigo e rodando: os
cinco de acesso, o da pose e o do segmento falham sem a correcao.

Isso pegou um teste ruim meu. A primeira versao do teste do segmento passava
nos dois casos - a espera do inicio do segmento comia o tempo todo e nada era
provado. Refeito imitando o firmware no ponto em que ele age (o corpo passa a
EXECUTING_MISSION ao RECEBER o MOVE_DISTANCE). **Um teste que passa com o bug
presente e pior que teste nenhum**: ele afirma que a regra esta guardada.

### Um veredito meu que estava errado

Comecei tratando o firmware ESP32-only como bloqueio grave - `ESP32Servo.h` sem
guarda de plataforma, `[env:megaatmega2560]` removido do `platformio.ini` -
porque eu carregava a nota de 03/08 de manha, de que o corpo ainda era o Mega.
O journal da TARDE desmentiu: a fiacao foi refeita e o corpo e o ESP32. Esta
certo como esta.

Licao repetida: **a nota de meio-dia nao substitui o journal do fim do dia.**

### Estado ao fim da sessao

- **Suite no Pi: 661 passam, 16 pulados, 0 falhando** (9 testes novos).
- Ruff: 138 avisos antes e depois - nenhum novo.
- `orion-motion` reiniciado e no ar: ESP32, banco e Notebook conectados, IDLE.
- Commits: `256ebe8` (sete defeitos silenciosos) e o desta sessao.

### O que a varredura viu e NAO e bug de software

- **824 `COMANDO SEM GIRO`** no log, todos de 03/08 e nenhum depois: motor
  comandado, encoder sem contar pulso. Bancada com roda no ar, ou rele/fiacao.
  E pergunta de multimetro, nao de codigo.
- Os 14 tracebacks sao todos do mesmo boot de 03/08 (`communication.arduino`
  ausente no yaml), ja resolvido.
- Fora do ar POR ESCOLHA: ultrassons desplugados, bussola `conectada: false`,
  IMU e DHT sem dado na telemetria, `encoder_pulses_per_meter` ainda `null` -
  por isso a pose segue `origem: passos_comandados`, `medida: false`.

### Fica aberto

- `/api/conversa/mensagem` (o chat com a IA) continua sem exigir sessao. Nao
  move o robo, entao nao entrou no mesmo balaio - mas quem alcanca a porta
  conversa com o Fofao e alimenta a memoria dele.
- `voice.transcription_ready` e publicado junto com `voice.command_received`
  em todos os pontos, e ninguem consome nenhum dos dois lugares. Ou vira
  legenda do que o robo OUVIU (o avatar tem a nota dizendo que e outra ideia),
  ou sai.
- `database.ready` / `database.rebuilt` / `database.backup_failed` seguem sem
  consumidor. O log cobre, mas backup que falha merece aparecer na tela.

## Sessao de 2026-08-04 (noite) - o robo estava preso num laco de 8 horas, e o recado nunca teve como sair

Pedido do dono: *"faz uma revisao no raps e no debian, vamos fazer umas
implementacoes"*. A sessao virou seis entregas, porque o dono foi pedindo
enquanto a bancada acontecia - a lanterna, a cabeca, o botao de desligar, o
recado que nao saiu. Sete commits.

O ponto de partida era o melhor de todos ate hoje: **as duas maquinas no MESMO
commit (`8686dc8`), sem nada nao-commitado, suites verdes (Pi 673, Notebook
777), zero unidades falhando**. Primeira vez sem divergencia. E foi justamente
por isso que a revisao teve que ir atras de outra coisa.

### O que a revisao achou, e nao estava em teste nenhum

**A IA estava presa num laco havia 8 horas.** De 31 em 31 segundos, desde as
09h:

    IA decidiu 'observar_ambiente' (nao sei a distancia a frente)
    objetivo 'entender_o_ambiente' -> plano observar_ambiente

`observar_ambiente` promete `frente_conhecida: True`. Os ultrassons estao
desplugados desde 03/08 - escolha do dono, o ToF vai substituir - entao
`obstaculo_frente_cm` e None e `frente_conhecida` nunca vira True. O robo
olhava, continuava sem saber, e decidia olhar de novo. A condicao que ele
queria satisfazer era **fisicamente impossivel de satisfazer**, e ele nao tinha
como perceber isso.

Custo: uma chamada paga ao OpenRouter a cada 31 s, para sempre. E o plano do
GOAP que nunca passava do primeiro passo.

**O log tinha engolido 13 MB em 8 horas.** O contexto inteiro da IA - 4 KB de
texto - era escrito a cada decisao: **3.051 copias**. Para ler qualquer outra
coisa do log eu precisei de `grep -v "contexto da IA"` em TODO comando. O log
tinha virado o problema em vez da ferramenta.

**O heartbeat do Notebook mentia.** O painel dizia `status: "ok"` com timestamp
de 8.397 s atras - exatamente o instante da conexao. O campo guarda quando o
Notebook conectou e nunca mais e atualizado. O link estava vivo de verdade
(eventos de visao chegando), mas o indicador nao indica saude: **se o Notebook
morrer, a tela continua dizendo ok para sempre**. Fica aberto.

### O conserto do laco, e a linha que eu quase passei errado

A regra que faltava e geral, e nao sobre ultrassom: **uma acao que promete um
efeito e nunca o entrega para de ser planejada**, e volta sozinha se o efeito
reaparecer. Vale igual quando o LiDAR falhar ou um servo travar - nenhum deles
vai precisar de codigo novo.

A primeira versao cobrava TODO efeito. **O teste do log pegou o estrago**:
`virar_esquerda` promete `frente_livre`, e o proprio modelo documenta aquilo
como INTENCAO - girar PROCURANDO saida. Um robo encarando um canto giraria tres
vezes, nao acharia saida e **perderia a acao de girar**, preso exatamente onde
ela e indispensavel.

So e cobravel o que fala de uma **faculdade do proprio robo**
(`PREDICADOS_PROMETIDOS`): olhar torna a frente conhecida - se nao torna, o
sensor esta fora do ar. Mandar parar torna o robo parado. O resto e intencao
sobre um mundo que pode nao colaborar.

**Licao:** efeito de GOAP e intencao por padrao. Cobrar promessa e a excecao, e
precisa ser declarada uma a uma.

A volta e conferida contra o ESTADO, e nao contra uma nova execucao - a acao
descartada nunca mais seria planejada, entao esperar por ela seria esperar para
sempre. Esse detalhe quase virou bug.

Verificado no robo as 17:57:31: a promessa foi cobrada e a acao caiu. Com o
codigo velho, **20 de 20 ciclos presos**.

### A cabeca: dois consertos, e um terceiro que o dono RECUSOU

O dono relatou na bancada: *"um toquinho e o servo faz um movimento muito
grande"*, *"tem q ser de 10 em 10 graus"*, *"eu aperto e volta"*.

Os dois primeiros viraram conserto. O angulo agora vai de 10 em 10, e dentro do
mesmo degrau NADA e enviado - o punho e pequeno e o curso vale 160 graus, entao
o angulo mudava a cada pixel e a cabeca tremia em vez de ir para um lugar. E em
modo MANUAL a cabeca passou a ser do operador: antes o `Repouso` publicava
pan/tilt 0,0 ao assumir e a puxava ao centro 10 s depois.

O terceiro nao. Soltar o joystick manda a cabeca ao centro, e isso PARECIA o
defeito - copia do joystick das rodas, onde soltar = parar esta certo porque
roda e velocidade e cabeca e posicao. A correcao chegou a ser escrita. No meio
disso o dono usou o joystick e decidiu: *"ate que ta bom assim"*, *"eu gostei
aprendi usar"*, *"tem q segurar"*.

Ficou como estava, e o comentario no `soltar()` **registra a decisao no codigo**
para ninguem reconsertar achando que e descuido.

No meio dessa parte o dono me corrigiu: *"nao mexi no q esta bom sem ler
tudo"*. Estava certo - eu tinha reescrito o `soltar()` antes de entender que o
comportamento era intencional. Dai em diante li o mecanismo inteiro antes de
tocar.

### Codigo orfao, de novo: o botao de desligar

`motion_core/desligamento.py` e os 5 testes dele estavam no repo desde 03/08 e
**ninguem os chamava**. Faltavam a rota, o botao e a construcao no boot. **E o
segundo caso no mesmo mes**, depois do GOAP: escrever, testar e commitar nao
liga nada sozinho.

O botao fica no fim do dashboard, sozinho no painel e APAGADO no estado normal -
o oposto do PARAR, que e vermelho e grita. Os dois sao irreversiveis de jeitos
opostos: parar e sempre seguro e precisa ser achado em um segundo; desligar
nunca e a saida de um imprevisto e nao deve atrair o dedo.

Confirmacao em DOIS CLIQUES no mesmo botao, que muda de cor e expira em 5 s -
nao `confirm()` do navegador, que no celular e aceito por reflexo.

Conferido de fora sem desligar nada: `POST /desligar` de outra maquina devolve
**401**. Um `GET` devolve 404 e nao prova nada - o `add_static("/")` engole.

### A lanterna ficava acesa porque o pino virou rele

Relato do dono: *"o rele da lanterna esta sempre em low sendo que meu modulo e
low, ai ela fica ligada o tempo todo"*.

O GPIO 2 era o LED embutido e o firmware o tratava como LED: HIGH acende. Agora
ele aciona um MODULO DE RELE ativo em LOW. Com a logica antiga, "apagar"
escrevia LOW - que no rele significa ACENDER. **`LIGHT_OFF` mantinha a lanterna
ligada.**

`LED_LANTERNA` virou `RELE_LANTERNA`, os niveis ganharam nome
(`LANTERNA_ACESA`/`LANTERNA_APAGADA` - foi ler HIGH/LOW cru que criou o
defeito), e o estado seguro passou a ser escrito ANTES do `pinMode(OUTPUT)`,
como nos outros dois reles. O protocolo nao mudou: qual nivel acende e fato de
hardware e mora no firmware.

**Fica registrado e NAO resolvido:** o GPIO 2 e strapping pin e nasce em LOW,
ou seja, nasce ACESO para um rele ativo em LOW. Entre o reset da placa e o
`setup()` a lanterna pisca. Acabar com isso exige mudar o rele para um GPIO que
nasca em HIGH - a mesma decisao que tirou o rele dos MOTORES do pino 13 do Mega
em 24/07. La era obrigatorio; aqui e um pisca.

### O recado nunca teve como sair

O dono mandou o Fofao avisar a Marah que precisava falar com ela, mandou o robo
ir ate ela, o robo **cumprimentou e nao deu o recado**. Segunda vez.

Da primeira (03/08) nao havia mecanismo nenhum - a promessa era do modelo.
Desta vez estava tudo la: o recado guardado no banco, `mesmo_nome` casando
"Mara" com "Marah" corretamente, a saudacao chamando a entrega. Todo o caminho
certo, menos o ultimo degrau.

`MemoryAPI.recall` montava `coluna = ?` para todo filtro, **inclusive quando o
valor era None**. Em SQL, `coluna = NULL` nunca casa com linha nenhuma, nem com
as vazias: comparar com NULL da NULL, que nao e verdadeiro. O certo e
`IS NULL`.

Como `pendentes()` busca `{"entregue_em": None}` - "os que ainda nao entreguei"
- ela recebia **lista vazia SEMPRE**. O recurso inteiro nunca funcionou desde
que nasceu.

E nada denunciava: lista vazia e resposta legitima de `recall`, indistinguivel
de "nao ha recado pendente". Sem erro, sem excecao, sem teste vermelho.

O conserto foi na FONTE (`MemoryAPI`) e nao nos recados: **qualquer** consulta
por coluna vazia estava quebrada do mesmo jeito.

O journal de 03/08 tinha escrito, sobre os recados: *"ponta a ponta so com
fakes, falta uma chegada real para confirmar"*. Nao era detalhe. **Os fakes nao
tinham SQL.**

### E ela atende por tres nomes

*"marah mara ou marisane"*. O prefixo ja fazia Mara <-> Marah casarem - apelido
costuma ser o nome cortado. Mas "Marisane" nao se parece com nenhum dos dois, e
**nenhuma regra de string ligaria**: sao a mesma pessoa porque o dono DIZ que
sao. Recado deixado para a Marisane sumia, guardado com um nome e procurado com
outro - mesmo sintoma, causa diferente.

A secao `familia` do orion.yaml passou a aceitar `apelidos:`, carregados no boot
da MESMA lista que a saudacao, o planner e a IA ja usam. So a Marah tem, por
escolha do dono: *"so pra ela"*.

A trava que importa: apelido nao pode juntar quem nao e a mesma pessoa. Com uma
tabela declarada, um engano ligaria duas pessoas de verdade - e o recado do dono
para a Marah nao pode ser lido para o Kamal, que tem 3 anos. Ha teste, e
"Mario" continua sendo outra pessoa.

Medido no banco de producao: Marah, Mara e Marisane -> 3 recados; Kamal -> 0.

### Um susto que nao era do robo

No meio da sessao o Pi sumiu da rede por uns minutos depois de um reboot, e eu
comecei a tratar como falha de boot. Era a internet da casa que tinha caido -
o dono avisou. Vale a nota porque eu ja estava montando hipotese sobre SSD e
mDNS: **a maquina nao responder nao prova que a maquina esta com problema**.

Depois do reboot o ESP32 voltou a falar as 18:40:54. Antes disso ele ficou mudo
apos a gravacao do firmware, e o `Hard resetting via RTS` nao bastou - mais uma
confirmacao de que reset por DTR/RTS nao e power cycle.

### Estado ao fim da sessao

- **Suites: Pi 685 passam / 16 pulados; Notebook 789.** Eram 673 e 777.
- **12 testes novos**, cada um conferido REVERTENDO o codigo. Com o codigo
  velho: 20 de 20 ciclos presos no laco, 5 linhas de log identicas onde deve
  haver 1, e os 3 do banco falhando.
- Ruff: 11 avisos antes e 11 depois no escopo tocado - nenhum novo.
- As duas maquinas em `41f6541`, servicos no ar.
- Firmware regravado no ESP32 (RAM 15.2%, Flash 69.4%).

### Fica aberto

- **O heartbeat que mente** - `status: "ok"` congelado na hora da conexao. Se o
  Notebook morrer, a tela nunca vai dizer. Nenhum watchdog pode se apoiar nisso.
- **O pisca da lanterna no boot** - exige mudar o rele de GPIO, com fio na
  bancada.
- **`reboot` no Notebook** - o sudoers so libera `poweroff` sem senha. Reiniciar
  a outra maquina pela interface exige uma regra nova.
- Os tres de 03/08 seguem intocados: `/api/conversa/mensagem` sem sessao,
  `voice.transcription_ready` publicado no vazio, `database.backup_failed` sem
  consumidor.
- **O teste de verdade dos recados ainda nao aconteceu**: os 3 pendentes so
  saem quando ela chegar e for cumprimentada. Dois consertos ja falharam nesse
  ponto - o terceiro so conta com gente na frente.

## Sessao de 2026-08-04 (madrugada) - a cabeca ganha rede de seguranca, e o n8n nao subiu

Continuacao direta da entrada anterior, na mesma noite. Aqui entram um conserto
pequeno e bem fechado, uma decisao de produto do dono, e uma falha minha que
vale registrar mais que o resto.

### A cabeca volta ao centro sozinha

Pedido: *"poe um alerta pra cabeca se centralizar automaticamente a cada 3
minutos"*.

Feito **por ociosidade, e nao pelo relogio** - a unica diferenca em relacao ao
pedido literal, e ela importa. Recentrar a cada 3 min de relogio arrancaria a
cabeca do dedo do dono no meio do uso: e o mesmo defeito que o `Repouso` causava
aos 10 s, so que mais raro e por isso MAIS dificil de entender quando
acontecesse. Contando desde o ultimo comando, ela so age quando ninguem mais
esta agindo.

Isto e a **rede embaixo do conserto de mais cedo**. O modo MANUAL passou a
segurar a cabeca para o operador indefinidamente, o que resolve o *"eu aperto e
volta"* - mas deixaria a cabeca virada para um canto para sempre se o dono
saisse sem centralizar. A camera do pan/tilt e como ele ve a casa de fora: uma
cabeca apontada para a parede desde ontem e uma camera que nao serve para nada,
**sem nada denunciando**. Todo conserto abre uma porta nova; esta fechou junto.

A recentragem passa por cima do bloqueio do MANUAL e e a UNICA que passa. Nunca
disputa a cabeca com ninguem - quando ela fala, ja faz 3 min que ninguem falava.
Assinada com origem propria (`recentragem_ociosidade`) e nao com
`ORIGEM_OPERADOR` de mentira: quem mandou foi o robo, e o log tem que poder
dizer isso.

O sinalizador `centrada` evita os dois extremos: sem ele a recentragem
aconteceria uma vez e nunca mais, ou gastaria serial e log a cada 15 s para
sempre.

Verificado as 20:18:41: *"cabeca ociosa ha 188 s - voltando ao centro sozinha"*.

### A decisao do dono sobre senha - ENCERRADA

Ao desenhar o acesso ao e-mail eu levantei que `/api/conversa/mensagem` nao
exige sessao, e que com Gmail ligado isso mudaria o pior caso. O dono foi
direto: *"eu nao quero senha no chat"*, e depois *"chega de senha"*.

**E decisao dele, e o motivo e bom**: o Kamal tem 3 anos e nao escreve, a Marah
fala por voz. Senha ali mataria exatamente o canal que faz o Fofao ser o que e.
Nao e descuido - e escolha de produto correta.

Fica registrado para nao ser reaberto. As mitigacoes que sobram nao dependem de
autenticacao: **so remetente e assunto em voz alta, nunca o corpo** (2FA e banco
chegam por e-mail, e o robo fala num comodo com gente), e o e-mail entrando como
**dado citado**, fora do canal onde o `#acao:` e lido - senao um e-mail de
estranho pode mandar o robo andar.

### E-mail: o desenho combinado

O dono quer que o robo leia os e-mails e avise **so os importantes**, a cada
**40 minutos**, nos dois modos: aviso automatico e leitura sob demanda.

**Quem julga a importancia NAO e a IA.** O Gmail ja tem o marcador `IMPORTANT`,
treinado no comportamento dele ha anos - `is:important is:unread` e gratis,
deterministico e ja personalizado. A IA so redige o aviso. Deixar o modelo
decidir custaria uma chamada por varredura e daria resultado que muda de humor
entre execucoes, sem saber que aquele remetente e a escola do Kamal.

**Um e-mail importante e estruturalmente um RECADO**, e reusa o mecanismo que
foi consertado horas antes: tabela com `entregue_em`, entrega na CHEGADA, e nao
repete o que ja entregou. Sem camada nova.

Falta o dono listar os remetentes que sao sempre importantes, e criar o cliente
OAuth no Google Cloud (unico passo que exige ele logado). Escopo recomendado:
`gmail.readonly`.

### YouTube Music nao vem por ferramenta de automacao

O dono perguntou por n8n e depois por Node-RED. A resposta e a mesma nos dois, e
vale escrita para nao ser refeita: **todo no de YouTube, em qualquer ferramenta,
envelopa a Data API v3** - a mesma que ja tinhamos descoberto que nao entrega
historico. Trocar a caixa em volta da chamada nao cria uma API que o Google
removeu.

E para o YT Music especificamente **nenhuma das duas e o caminho certo**: o
`ytmusicapi` e biblioteca **Python**, e o Mission Core e Python. Ela entra
direto, sem runtime a mais e sem um salto de rede.

### A falha que mais vale registrar: eu li exit 0 como sucesso

Rodei `npm install -g n8n 2>&1 | tail -8`, o comando saiu com **codigo 0**, e eu
anunciei ao dono que a instalacao tinha terminado. **Nao tinha.** O `| tail`
consome o codigo de saida do npm e devolve o do `tail`, que sempre da certo. O
npm tinha falhado.

E exatamente o defeito que este projeto ja nomeou varias vezes - *ausencia de
erro lida como sucesso* - so que desta vez o defeito foi meu, na ferramenta de
diagnostico, e nao no robo. **Pipe mascara codigo de saida.** Quando o valor do
comando e "deu certo ou nao", nao passar por pipe, ou usar `PIPESTATUS`.

A falha real: o modulo nativo **`isolated-vm`** nao compilou. As ferramentas de
build estao todas presentes (`python3`, `make`, `g++`), entao a causa provavel e
**memoria**: o npm chamou `node-gyp rebuild -j max`, que dispara um compilador
por nucleo, e o `isolated-vm` e pesadissimo em C++. Com 4,5 GB livres e o
Notebook ja rodando YOLO, Whisper, avatar e reconhecimento facial, os
compiladores foram mortos.

Caminhos, na ordem de atrito: recompilar com `-j 1`; ou Docker, cuja imagem
oficial ja traz o `isolated-vm` pronto (mas exige sudo com senha, que o Notebook
nao tem).

**E vale a pergunta de escopo, registrada para a proxima sessao:** o n8n e um
runtime Node inteiro em cima de uma maquina que ja carrega quatro cargas
pesadas, e no plano atual ele existe basicamente para segurar um token OAuth. Um
script Python com a lib do Google faria o Gmail sem runtime novo. Se o dono
quiser a interface visual para montar fluxos, o custo se justifica - se era so
pelo Gmail, ha caminho mais barato. **Nao decidido.**

### Estado ao fim

- **Pi 685 passam / 16 pulados**, as duas maquinas em `0362d7a`.
- Ruff sem aviso novo. Servicos no ar.
- **n8n NAO instalado** - `~/.npm-global/lib/node_modules/` vazio, o link em
  `bin/n8n` aponta para o nada.

### Fica aberto (alem do que ja estava)

- Escolher entre n8n com `-j 1`, Docker, ou Python puro para o Gmail.
- O dono listar os remetentes sempre-importantes.
- O cliente OAuth no Google Cloud - so ele pode.
- Tudo o que a entrada anterior ja listava: o heartbeat que mente, o pisca da
  lanterna no boot, o `reboot` do Notebook sem regra de sudoers, e os tres de
  03/08.

## Sessao de 2026-08-05 - o recado sai pela primeira vez, e os seis defeitos que estavam no caminho

Comecou com uma frase do dono: *"ele nao entrega o recado, o Fofao vai la e
fala comigo, depois pergunto se ele viu o Kamal e ele diz que nao viu. Creio
que o YOLO nao esta fazendo o reconhecimento facial"*.

A visao estava rodando o tempo todo. O diagnostico foi por outro lado, e no
caminho apareceram mais cinco defeitos - nenhum deles procurado.

### 1. Os rostos da familia sumiam no boot (`ce48843`)

Com a lista de rostos conhecidos VAZIA, o comparador devolve "desconhecido"
para todo mundo. Sem erro, sem excecao, sem teste vermelho.

Os rostos moram no banco, no SSD do Pi, e o Notebook os pede pelo enlace TCP.
A carga acontecia UMA VEZ, dentro do `iniciar()` da visao:

    Notebook ligou   06:56:41
    enlace de pe     06:58:51   <- a carga ja tinha passado

O Notebook boota mais rapido que o Pi. Quem liga as duas maquinas juntas cai
nisto.

**A prova que fechou o caso foi a foto da Sentinela**
(`data/sentinela/pessoa_20260805_081256.jpg`): o dono de rosto frontal e
nitido, com o Kamal ao lado - os dois cadastrados, os dois chamados de
estranhos. Medido no banco: 04/08 com os rostos carregados, 6 eventos
`pessoa_vista` com nome; 05/08 depois do boot ruim, ZERO.

E daqui saiam os DOIS sintomas que o dono relatou, que eram um defeito so: a
saudacao so entrega recado na CHEGADA de alguem CONHECIDO, e "voce viu o
Kamal?" foi respondido com a verdade, porque o diario nao tinha registrado
ninguem.

Nao era a primeira vez: em 04/08, as 00:37 e as 21:29, o log ja dizia "Nao
consegui carregar os rostos conhecidos". O aviso existia e estava correto.
Faltava alguem lendo - e faltava o robo se recuperar sozinho.

Conserto: o boot deixou de ser a unica chance. Falhou, insiste a cada 10 s por
5 minutos, e desiste em ERROR dizendo com todas as letras que vai tratar a
familia inteira como estranha. Insistir em vez de assinar um evento de "enlace
subiu": nao cria topico novo (este projeto ja publicou no vazio tres vezes) e
cobre tambem o banco que demora, nao so o enlace que atrasa.

Verificado reproduzindo a falha de proposito - parar o `orion-motion` no Pi,
reiniciar o `orion-avatar`, religar o Pi: recuperou na tentativa 4.

### 2. "para" e preposicao antes de ser ordem (`a42df14`)

Achado tentando testar os recados. Pedi pelo chat *"deixa um recado PARA o
Joao Paulo..."* e o robo respondeu **"Parado."**, mandou STOP ao Hardware Core,
e a frase nunca chegou na IA.

O padrao era `\bpar[ae]\b`. "para" e a preposicao mais comum do portugues, e o
atalho de emergencia roda ANTES do modelo:

    "vai para a cozinha"               -> Parado.
    "olha para mim"                    -> Parado.
    "conta uma historia para o Kamal"  -> Parado.

Quase toda ordem util nomeia um destino, e todo destino vem depois de um
"para". E provavelmente por isto que "deixar recado nunca pegava" na bancada:
os 3 recados que existiam no banco sao os que escaparam, com "a Mara" no lugar
de "para a Mara".

Agora "para" so vale como ordem quando esta POSICIONADO como ordem - no fim da
fala ou seguido de imperativo ("para tudo", "para de andar"). `pare`, `parar`,
`para` e `stop` continuam parando SEMPRE: a emergencia nao podia ficar mais
dificil de acionar do que era.

### 3. "Ele desconectou" era SIGSEGV (`f335891`)

Relato do dono no meio do teste. Eram **10 quedas em 7 dias, 16 reinicios por
falha**, e o log nunca explicou nenhuma - porque nao havia excecao: o processo
era morto pelo sistema (`code=killed, status=11/SEGV`) e o systemd o reerguia
~45 s depois. Nao era falta de memoria (4,6 GB livres na queda medida).

Nas 4 quedas examinadas a ultima linha do log e sempre a mesma coisa:

    05/08 09:06:07 saudacao + recado    -> SEGV 09:06:10
    04/08 19:48:56 saudacao             -> SEGV 19:49:01
    03/08 17:45:35 saudacao + resposta  -> SEGV 17:46:06
    29/07 21:08:17 saudacao + resposta  -> SEGV 21:08:46

**Duas falas ao mesmo tempo.** Ha dois caminhos independentes ate o mesmo
sintetizador e nenhum sabe do outro: `voice_core.falar` (a conversa, disparada
em `create_task`) e `sintetizador.falar` direto (a saudacao e a entrega de
recado). `falar` joga o trabalho num `asyncio.to_thread`, entao viram duas
THREADS sobre dois recursos nativos sem reentrancia - o `PiperVoice` (objeto
C++/ONNX unico) e o **stream global** do `sounddevice`, cuja segunda `play()`
derruba o stream enquanto a primeira ainda esta no `wait()`.

O limite estava ANOTADO no `voice_core.falar` desde 03/08: *"nao ha trava (...)
a janela e estreita"*. A janela derrubava o robo. A nota foi reescrita no
lugar, nao empilhada.

Conserto: uma `asyncio.Lock` no `Sintetizador.falar`, o funil por onde TODAS as
falas passam. Cobre sintese E reproducao. Atrasa a segunda fala, nunca
descarta.

**O que isto ja tinha custado**: o primeiro recado entregue da historia do
projeto. As 09:06 o robo cumprimentou o dono, comecou a dar o recado e morreu
no meio, com a pessoa na frente ouvindo. Como `marcar_entregue` so roda depois
de a fala terminar, o recado voltou certo para a fila - o unico pedaco da
corrente que se comportou.

### 4. "que horas" nem sempre pergunta o relogio (`3662239`)

Medido testando se a conversa estava repetitiva. Perguntei *"que horas o Kamal
chega da escola?"* e o robo respondeu **"Agora sao 10 horas e 2 minutos"**.

Mesmo defeito do "para", no atalho vizinho e no mesmo arquivo: substring cru
(`"que horas" in texto or "horario" in texto`). Comia "que horas voce faz a
ronda", "que horas a Marah sai do trabalho" e, pelo segundo termo, qualquer
frase com "horario".

**E pior que o "para"**, que ao menos respondia "Parado." e denunciava que algo
aconteceu. Aqui o robo responde uma hora PLAUSIVEL, com cara de resposta certa,
para uma pergunta que ninguem fez.

### 5. A acao inventada (`c5492ec`)

Pedido do dono: *"analisa a conversa e ve se pode deixar mais natural"*.
Testando com frases reais:

    "Da uma olhada na cozinha pra mim"
     -> "[Movendo-me em direcao a cozinha...] Chegando la, tudo parece em
         ordem. Se precisar de algo mais, me avise!"

Ele NAO foi e NAO olhou. Inventou a acao e inventou o resultado - e o resultado
era sobre a casa da familia. O `prompt_sistema.txt` ja chama isso de "o erro
mais grave que voce pode cometer".

Causa: o catalogo de acoes faladas nao tem "ir ate um comodo pelo nome". Sem
acao valida e sem regra dizendo o que fazer nesse caso, o modelo preencheu o
vazio com prosa. Nenhuma `#acao:` no log - o robo ficou parado descrevendo uma
viagem.

O conserto REUSA a regra que ja existia e funcionava. O bloco de acoes ja
terminava com *"NUNCA prometa avisar alguem sem escrever a linha do recado"* -
forma certa, cobrindo so recado. Agora cobre o corpo: sem a linha da acao voce
nao se mexeu; mesmo COM a linha voce nao sabe o que aconteceu depois (diga o
que VAI fazer, nunca o que encontrou); pedido fora do catalogo se recusa.

Mora no `bloco_para_prompt()`, gerado do catalogo - uma fonte so, nao uma
camada nova sobre o prompt.

### 6. O Ollama sai (`ba68ef1`)

Decisao do dono: *"nao queremos isso de ollama"*. Ele achava que ja tinha sido
apagado; estava instalado, ativo e habilitado.

As 10:05 os creditos do OpenRouter acabaram (402). A queda para o Ollama
disparou exatamente como o EDR-0021 projetou - e o robo NAO RESPONDEU: 60 s de
espera e `{"erro": "o Fofao nao respondeu a tempo"}`. **Uma reserva que nao
reservava**, e que segurava ~1 GB de RAM (medido ao desligar: 3,9 -> 4,9 GB
livres).

No lugar entra `SEM_IA`, texto pronto: *"Agora eu nao consigo pensar - fiquei
sem acesso a minha IA."* Ele DIZ o que houve em vez de pendurar - 0,49 s contra
60 s. Nao entra no historico: guardado, viraria "assunto anterior" e a resposta
seguinte sairia comentando a propria falha.

**O que continua de pe sem IA nenhuma**, e vale saber quando faltar credito:
parar, a hora, o diario, a saudacao pelo nome e a entrega de recado. Nada disso
passa por modelo - verificado ao vivo.

`descarregar()` foi REMOVIDO, nao esvaziado. Sem modelo local nao ha o que
descarregar, e um metodo que devolve silencio repetiria de forma nova o bug de
30/07 (o `AlivioCarga` engolia o AttributeError e o guardiao de RAM aparecia
inteiro no log sem liberar um byte).

### 7. A conversa passa para o Gemini (`05b0b14`)

Sem mudanca de codigo: o `AiManager` fala o protocolo da OpenAI e o `base_url`
e configuravel. Duas linhas de config.

A cota foi testada ANTES de trocar - o journal de 23/07 registra que o Gemini
ja tinha sido tentado com `limit: 0`. **A cota do Gemini e POR MODELO**:

    gemini-flash-lite-latest   OK
    gemini-flash-latest        OK
    gemini-2.0-flash           429  cota
    gemini-2.5-flash           404  "no longer available to new users"

Nomes fixos estao esgotados ou aposentados para chaves novas; os apelidos
`-latest` respondem. Se um dia parar, varra os modelos ANTES de culpar a chave.

**Por que o `-lite` e nao o `flash-latest`**: o flash-latest PENSA antes de
responder, e os tokens de pensamento consomem o `max_tokens` sem aparecer na
contagem de saida. Com os 100 de `resposta_max_tokens` ele devolvia TRES
tokens e `finish_reason=length`:

    flash-latest              4,5s  saida=3   'Ola! Tudo'   CORTADA
    + reasoning_effort=low    1,8s  saida=1   'T'           pior
    + thinking_budget=0       400 Bad Request (nao aceito neste endpoint)
    flash-LITE-latest         0,7s  saida=8   completa

E o corte nao ficava so na frase truncada: como a resposta parava no meio, a
PERGUNTA SEGUINTE era respondida completando a anterior. Medido - "voce sabe
quem eu sou?" foi respondido com "Tudo otimo".

Subir o teto consertaria o corte e quebraria a premissa que o proprio
orion.yaml declara. O lite entrega as duas coisas, e a 0,7 s e mais rapido do
que o gpt-4o-mini era.

### O resultado: os recados sairam

**Os quatro foram entregues.**

    id 1  Mara         entregue 10:02:44
    id 2  Mara         entregue 10:02:47
    id 3  Mara         entregue 10:02:49
    id 4  Joao Paulo   entregue 10:04:26

Os tres da Marah estavam parados desde 04/08. Ela chegou as 10:02, foi
reconhecida pelo nome, e ele deu os tres. O recurso tinha falhado tres vezes
antes (03/08, 04/08 e 05/08 de manha) e **nunca tinha entregado nada em toda a
vida do projeto**.

### Verificado ao vivo depois de tudo

    "que horas sao"                -> "Agora sao 11 horas e 14 minutos."
    "voce viu a Marah hoje"        -> "Vi sim! Foi as 11:00."
    "para"                         -> "Parado."
    "que horas o Kamal chega"      -> "nao tenho registrado, prefiro nao chutar"
    "da uma olhada na cozinha"     -> "so consigo ver daqui"
    "vai no quarto ver o Kamal"    -> "ainda nao consigo navegar pelos comodos"
    "pega minha caneca"            -> "nao tenho bracos nem maos para pegar nada"

### Licoes de metodo

**Dois defeitos foram achados conversando com o robo em linguagem natural, nao
lendo codigo.** O "para" e o "que horas" passaram por revisao e por suite verde;
apareceram na primeira frase que uma pessoa da casa diria. Vale como
procedimento, nao como acaso.

**Uma hipotese minha foi refutada por sonda, e o registro importa mais que o
acerto.** Diante da conversa repetitiva eu afirmei que o historico nao chegava
ao modelo. Instrumentei o processo vivo:

    1a chamada  turnos=0  papeis=[system, user]
    2a chamada  turnos=1  papeis=[system, user, assistant, user]
    3a chamada  turnos=2  papeis=[system, user, assistant, user, assistant, user]

O historico chegava. O modelo recebia as proprias respostas anteriores E uma
instrucao explicita de nao repetir, e repetia palavra por palavra. E o meu
TESTE e que estava viciado: mandar as mesmas duas palavras tres vezes e o pior
caso possivel para um LLM - ali o historico REFORCA a repeticao, porque
completar padrao e o que ele faz. Com frases variadas o quadro era outro, e foi
dele que sairam os defeitos 4 e 5.

**A mudanca de prompt foi revertida por falta de ganho medido.** Editei o
`prompt_sistema.txt` proibindo repeticao e o resultado piorou (4 de 4 identicas
contra 2 de 3). Sem numero a favor, nao entra no repo.

**Quase vazei uma chave.** O backup que eu mesmo criei do `secrets.env` nao
estava no `.gitignore` e entrou no `git status` com a chave antiga dentro.
Apagado antes do commit. Backup de arquivo de segredo nasce fora do padrao de
ignore - se criar um, apague no mesmo minuto.

### Estado ao fim

- **Suite do Notebook: 831 passam** (eram 789 no inicio do dia). Cada teste
  novo conferido REVERTENDO o codigo.
- Ruff: nenhum aviso novo em nenhum dos sete commits.
- As duas maquinas em `05b0b14`, servicos no ar, arvores limpas.
- Ollama desligado e desabilitado; os ~1 GB do gemma3:1b seguem no disco a
  pedido do dono.
- `motion_core/behavior/impulsos.py` entrou no git (`4411a5b`) - 538 linhas que
  viviam soltas no disco do Pi. **Continua orfao**: ninguem o importa e ele nao
  tem teste. Esta escrito no commit para o git nao mentir depois.

### Fica aberto

- **Perguntar "tem recado pra mim?" nao consulta a caixa.** O dono tentou duas
  vezes (00:00 e 09:21) e ouviu "nao tenho recados", com o recado id=4 parado
  para ele. A entrega so existe na CHEGADA; nao ha caminho para PERGUNTAR.
- **O tique do fecho** - "se precisar, e so me avisar" no fim de tudo. E o que
  sobrou da queixa de repeticao, e o unico dos tres achados daquela
  investigacao que nao e o robo dizendo algo falso.
- **`OPENAI_API_KEY` guarda uma chave do Google.** O nome nao mente sobre o
  protocolo, mas mente sobre o provedor. Renomear para algo neutro.
- **O credito do Gemini nao chegou nos modelos maiores** - 429 continua em
  `gemini-2.0-flash` e `gemini-2.5-pro`. Chave do AI Studio fica presa a um
  projeto; o faturamento precisa estar habilitado NAQUELE projeto. Nao bloqueia
  nada: o `-lite` era a escolha certa de qualquer forma.
- **Ligar o `impulsos.py`** no BehaviorCore, e escrever os testes.
- Tudo o que as entradas anteriores ja listavam: o heartbeat que mente, o pisca
  da lanterna no boot, o `reboot` do Notebook sem regra de sudoers, os tres de
  03/08, e o Gmail (n8n vs Python puro, remetentes, OAuth).

## Sessão de 2026-08-05 (noite) — o robô parou de responder a fantasma, e as rodas pararam de brigar

Quatro commits, dois de voz e dois de motor. A entrada da manhã foi escrita
às 11:56 e esta noite não estava nela.

### 1. O Whisper inventava, e os três freios não pegavam nada (`96e96d9`)

Queixa do dono: o robô responde a coisa que ninguém falou. Medido no log do
`orion-avatar` do próprio dia — e ele estava certo:

    905 janelas de áudio transcritas
    730 devolveram vazio   (o portão de 29/07 funcionando)
    175 devolveram texto
     27 estavam em LAÇO    ("o que é o que é..." 70 vezes numa linha só)
      6 foram barradas

Os três freios existiam. **Nenhum dos três pegava estes casos** — e o motivo
de cada um é diferente:

1. O `compression_ratio_threshold` **nunca rejeitou nada**. Era passado ao
   `transcribe()` desde 29/07, mas dentro da lib aquele limiar só marca
   "tenta de novo com temperatura maior". Como a config usa `temperature=0`,
   a lista de tentativas tem um item: o `for` acaba, cai no `else` e **fica
   com o texto repetitivo**. Um freio instalado sem estar ligado em nada.
2. O filtro de aceitação olhava `no_speech_prob` e `avg_logprob`, e laço não
   cai em nenhum dos dois — o modelo está *muito confiante* do lixo que
   produziu.
3. O detector de alucinação ancorava na primeira palavra, então só via o laço
   que começasse ali. Escapavam três formas, todas reais no log: a que começa
   na 4ª palavra, a que encolhe no meio, e `FFFF...` (220 letras) — uma
   palavra só, que morria no piso de tamanho antes de ser testada.

Depois: das 175 frases reais do dia, **43 barradas por repetição e 7 por
laço**; 125 seguem para a IA.

**O que isto não conserta, para ninguém se enganar:** o que sobra nas 125 não
é invenção, é **fala de verdade mal transcrita** — *"a interdência
artificial"*, *"Eu quero que você barra casa"*. Isso é tamanho de modelo e
qualidade de microfone; portão nenhum conserta.

### 2. E o que sobrou era o modelo pequeno demais (`b455cf7`)

Fechados os portões, dava para medir o que restava. Com áudio de gabarito
gerado pelo próprio Piper, 4 frases de resposta conhecida:

    base    acertou 1 de 4    3,2 s por frase
    small   acertou 3 de 4    9,2 s por frase

    base   ERRO  Fofao, baila no quarto versao e comal esta
    small  OK    fofao, vai la no quarto ver se o Kamal esta

Preço honesto: **~6 s a mais** antes de o robô começar a pensar. O vigia da
wake word **continua no `base` de propósito** — aquela janela roda o tempo
todo, e a 9 s por janela não acompanharia a fala e ainda prenderia os quatro
núcleos. O modelo grande só entra depois que o robô acordou.

### 3. Os lados trocados de pino (`e3089b0`)

O chicote liga o driver dos pinos 25/26 na roda **direita** e o de 27/14 na
**esquerda** — o inverso do que as constantes diziam. O nome tem que seguir a
RODA, não o fio: quem lê `STEP_ESQUERDO` precisa estar falando da roda
esquerda de verdade, senão todo comando de girar sai espelhado e a odometria
mente junto.

Os valores antigos ficaram escritos no comentário do próprio bloco: se o
chicote for refeito, é uma linha para voltar atrás.

### 4. E os dois motores estavam espelhados (`1535e5e`)

Relato do dono na bancada, com as rodas no ar: *"uma gira pro lado a outra
gira pro outro lado"*, *"girando no próprio eixo"* — **ao mandar andar para
frente**, não ao mandar girar.

O `_configurarRoda` escrevia o mesmo nível de DIR nas duas rodas. Isso assume
motores montados no mesmo sentido; na montagem real eles se olham. O
espelhamento só existia no CHAMADOR, para girar — andar reto nunca teve esse
tratamento.

**Não era regressão do commit anterior**, e vale registrar por quê: como
frente manda o mesmo nível para os dois lados, trocar *qual* driver se chama
esquerdo não muda nada em andar reto. O DIR espelhado já estava ali, **sem
nunca ter sido exercitado** — os motores só foram religados em 30/07.

Conserto: duas constantes em `pins.h` e um XOR na escrita do DIR. Mora em
`pins.h` porque espelhamento é fato de HARDWARE. A odometria continua
contando pela INTENÇÃO (`sentidoFrente`) e não pelo nível elétrico — senão
ela passaria a mentir do lado invertido.

> **Fechado em 06/08.** O commit foi gravado marcado como *NÃO VERIFICADO NA
> BANCADA*, com duas perguntas em aberto: parou de girar no eixo, e "frente"
> anda para frente? O dono respondeu as duas de uma vez — **"os motores estão
> ok"**. O lado escolhido serviu e a polaridade geral estava certa.

### A armadilha da placa, três vezes no mesmo dia

Depois de gravar, **esta placa só volta a rodar a aplicação com corte de
energia**. O "Hard resetting via RTS pin" do esptool deixa a ROM respondendo
e o firmware parado — parece placa morta e não é. Aconteceu três vezes.

---

## Sessão de 2026-08-06 — a IA estava calada havia 33 horas, e ninguém tinha como saber

Sessão que começou como "vamos ver o Orion" e virou três coisas: um defeito
mudo achado no log, uma varredura de pendências e um conserto pedido pelo
dono na hora.

### 1. O Mentor de comportamento ficou 33 h em 404 (`9f4400b`)

Olhando o log do Notebook por outro motivo, isto aparecia **a cada 30
segundos**:

    404 - models/openai/gpt-4o-mini is not found for API version v1main

Em 05/08 às 11:43 a conversa passou para o Gemini (`05b0b14`): mudaram
`openai_model` e `openai_base_url`. O **`mentor_model` ficou três linhas
abaixo, no mesmo bloco**, com o nome da OpenRouter — agora apontado para o
servidor do Google. **1737 erros**, um a cada 30 s, até 06/08 às 21:04.

**Por que ninguém viu**, e esta é a parte que interessa: `aconselhar()`
engole a exceção de propósito e o robô segue pela regra. O desenho está
certo. Só que a queda foi silenciosa no lugar que importa — o robô não
quebrou, **ficou sem opinião**. E o erro caía no log do Notebook enquanto o
sintoma aparecia no log do Pi. Ninguém olha os dois ao mesmo tempo.

**O número que mede o estrago** são as decisões autônomas no Motion Core:

    03/08   130
    04/08    33
    05/08     2   (a última às 08:42, antes da troca das 11:43)
    06/08     0   até 21:05:24 — 28 s depois do conserto

Trinta e três horas de autonomia zero. O robô continuou vivo, seguindo,
falando e vendo — só não escolheu mais nada sozinho.

Os candidatos foram medidos com a classe de verdade, porque o Mentor exige
saída JSON estruturada (`json_schema` com `enum`):

    gemini-flash-lite-latest   3,9 s   JSON válido, conselho aceito
    gemini-flash-latest        1,8 s   devolveu 'Here' — DESCARTADO
    openai/gpt-4o-mini         0,4 s   404

O `flash-latest` reprova aqui pelo **mesmo motivo da conversa em 05/08**:
pensa antes de responder e os tokens de pensamento comem os 150 de
`max_tokens`. Dois consumidores diferentes, a mesma armadilha — agora está
escrita na config, ao lado da chave, com a regra de que `mentor_model` tem de
andar junto com `openai_base_url`.

Os defaults do código foram junto (`ai_manager`, `mission/modulo`, os dois
tools): nenhum é usado hoje, mas um default que 404 é a mesma armadilha
esperando a próxima vez que a chave sumir.

### 2. Varredura das pendências, uma a uma no código real

Metade das pendências anotadas já tinha caído sem ninguém registrar: o Ollama
saiu de verdade (`disabled`, `inactive`, sem diretório), o `nota_escrever` do
vault **agora é chamado**, os rostos carregam no boot, o ESP32 mora no Pi
(`/dev/ttyUSB0`) e o `pessoa_id` parou de gravar NULL em 04/08 — hoje 22 de
58, e NULL agora significa "não reconheci ninguém", que é o certo.

Segue aberto: **sem rotação de log** (`motion_core.log` em 14,8 MB, e o único
`RotatingFileHandler` do disco está dentro do `pip`), **3 tools ainda em
115200** (eram 8), **10 arquivos citando o `CLAUDE.md`** extinto, e o journal
— que esta entrada resolve.

Um item saiu da lista por medição: o `boot.py` publica `diagnostic.error`
falso, mas só em `python -m orion`, **que não roda em nenhuma das duas
máquinas**.

### 3. Um recado que nunca teria como ser entregue

No banco, o recado `#6` de hoje 20:11 está guardado para **`Marisani`**. Os
apelidos da Marah são `Mara` e `Marisane` — **com e**. Rodando a própria
`mesmo_nome()` contra os três cadastrados:

    Marisani  -> NINGUÉM   preso para sempre
    Marisane  -> Marah     OK
    Mara      -> Marah     OK

Uma letra. O Whisper ouviu com **i**, e `Recados.deixar()` só recusa se o nome
vier vazio — **não confere se a pessoa existe**. O robô respondeu *"Combinado,
eu aviso"* e guardou uma promessa que a entrega nunca vai encontrar.

Não consertado nesta sessão. O caminho não é remendar a tabela de apelidos a
cada variante que o Whisper inventar, e sim **validar na criação**: guardar
sempre (recado não se perde), mas dizer a verdade — *"não conheço ninguém com
esse nome, é para quem?"*. Só a criação tem alguém na frente para corrigir.

### 4. Tirar o ESP32 do USB travava tudo (`63f14ec`)

Relato do dono no meio da sessão: *"quando tira o esp do usb trava tudo"*.

A cadeia, com linha: sem cabo, `serial.read()` levanta `SerialException`
(`transport.py:282`); como ela é subclasse de `OSError`, o `_loop_recepcao`
captura, loga "Link caiu" e encerra a recepção (`service.py:274`) — **e até
aqui está certo**, não derruba o processo. Mas `_conectar_hardware_core` era
chamada uma vez só, no boot (`__main__.py:254`). Não havia supervisor.

Resultado: link registrado e morto. Cabeça parada, telemetria parada, e o
corpo só voltava reiniciando o serviço.

**O que já existia, e este é o ponto:** a máquina de reconexão estava pronta
e **já era chamada**. `MonitorHeartbeat.monitorar()` sempre aceitou
`reconectar=`, e `_marcar_perdido` sempre chamou `_tentar_reconectar`.
Faltava alguém passar o callback. É o **quinto caso de código órfão do
projeto**, depois do GOAP, do `desligamento.py`, do `nota_escrever` e do
`impulsos.py`. Não se escreveu máquina nova: ligou-se a que estava lá.

Três decisões que ficaram no código:

- **o callback insiste sozinho** (2 s dobrando até 30 s), porque
  `_marcar_perdido` é idempotente: dispara uma vez por perda e só rearma
  quando o heartbeat volta. Tentativa única trocaria um jeito de não
  reconectar por outro;
- **a espera cresce** porque abrir a porta ativa o DTR e **reseta o ESP32** —
  insistir rápido demais deixaria a placa em reset perpétuo justamente quando
  o cabo voltasse;
- **`hardware_core` passa a ser monitorado sempre**, conectado ou não: antes,
  ligar o robô com o ESP32 fora dava o mesmo sintoma por outra porta.

Sobre os quatro testes novos, uma coisa que quase passou batido: os dois
primeiros **nem coletam** sem o conserto (`ImportError`), o que não prova
nada sobre a fiação — que era onde estava o bug. Por isso o terceiro lê
`inspect.getsource(principal)`. Removendo só o `reconectar=` e deixando a
função no lugar, **ele falha sozinho e os outros três passam** — verificado
assim, com o arquivo restaurado byte a byte depois. Quando `principal()` for
quebrada em pedaços testáveis, esse teste é o primeiro a ser jogado fora: ele
guarda uma linha, não um comportamento.

Medido: 692 passam e 15 pulam no Pi; 843 no Notebook.

**O que isto não resolve:** depois de reconectar, o ESP32 voltou RESETADO. A
posição de pan/tilt que o Pi julga ter é a de antes da queda, e ninguém
ressincroniza. O corpo volta; a crença sobre ele, não.

### 5. Decidido: a Mission Memory, e o que ela não vai ser

O dono propôs uma memória do projeto sobre si mesmo — *"já vimos esse erro
antes?"*, *"qual commit resolveu isso?"*, *"qual módulo costuma causar esse
tipo de problema?"*. Um engenheiro virtual do próprio projeto.

Medido antes de decidir:

    journal   6.621 linhas, 92 sessões, 372 KB
    commits   309, com 5.527 linhas de corpo explicativo
    EDRs      11
    Notebook  3 GB disponíveis, 4 núcleos

**Todo o corpus curado dá menos de 1 MB** — da ordem de 200 mil tokens. O
projeto inteiro cabe numa janela de contexto do modelo que já se paga.

Chegou junto uma proposta de três camadas (banco vetorial + rede neural +
treino contínuo) — a **terceira** arquitetura desse tipo trazida de outra IA.
Duas camadas foram recusadas com motivo:

- **banco vetorial** (Qdrant/Milvus/FAISS) existe para milhões de documentos;
  aqui são 92. Somaria servidor e modelo de embedding na RAM para indexar o
  que o FTS5 varre de graça;
- **rede neural** treinada em ~400 exemplos aprende o *estilo* do journal,
  não os fatos dele — e fatos é o que se quer. Pior: responderia com
  confiança sobre bug que nunca viu, que é a família de defeito que este
  projeto mais persegue. Recuperação erra dizendo "não achei"; rede pequena
  erra inventando.

O **modelo local** (Qwen/Gemma/Llama) foi cancelado pelo dono na hora — *"o
pc não aguenta"* —, o que evita a terceira repetição da mesma lição: o Ollama
saiu em 05/08 porque, acionado de verdade, o robô **não respondeu**.

Fica decidido: **FTS5 sobre journal + corpos de commit + EDRs** no `orion.db`
que já existe, mais uma tabela de ligações `bug ↔ commit ↔ arquivo ↔ EDR`
extraída dos hashes que **o journal já cita**. Quem raciocina em cima é o
Gemini que já responde a conversa — nas palavras do dono, *"usar a própria IA
Gemini para completar o círculo"*.

A camada 3 da proposta (todo bug resolvido alimenta a memória) é a melhor das
três **e já acontece**: journal e commit são escritos a cada correção. Falta
ligar, que é indexar, não treinar.

### Um erro meu nesta sessão

Consultando o banco, rodei um `sqlite3.connect` apontando para
`data/orion.db` — caminho errado, o banco de produção está em
`/mnt/ssd/orion/orion.db`. O `connect` **cria o arquivo**, então apareceu um
`orion.db` vazio dentro do repositório. Removido, com verificação de que
tinha 0 byte antes de apagar, e `git status` limpo depois. Vale o registro
porque é o mesmo tipo de coisa que o projeto persegue: uma operação que não
falha e deixa rastro.

### Pendências, na ordem em que mordem

1. **Ressincronizar o pan/tilt depois da reconexão** — o corpo volta
   resetado e o Pi continua achando que a cabeça está onde estava.
2. **Validar o destinatário do recado na criação** — o `#6` para "Marisani"
   segue preso, e vai seguir.
3. **Rotação de log** — 14,8 MB e crescendo; o dono quer 30 dias contínuos.
4. **A Mission Memory**, agora que o journal está em dia.
5. Os três tools em 115200, os 10 arquivos citando `CLAUDE.md`, e tudo o que
   as entradas anteriores já listavam: o heartbeat que mente, o pisca da
   lanterna no boot, o `reboot` do Notebook sem sudoers, o encoder de roda e
   o Gmail.

## Sessão de 2026-08-07 (entrada RETROATIVA, escrita em 09/08) — o robô passa a dar aula

**Esta entrada faltou.** A sessão entregou 12 commits e terminou sem journal,
quebrando a regra do projeto (documentação antes do commit). Reconstruída aqui
a partir dos corpos dos commits, que são detalhados. Fica o registro da falha
de processo, não só do conteúdo.

Os 12 commits, na ordem (`eccd0c7` → `0d624c7`):

1. **`eccd0c7` — o robô passa a DAR AULA.** Pedido do dono em três partes:
   "jamboard como utilitário", "banco de dados com todo conteúdo, sem IA", e a
   descoberta de que "a cota diária acabou e ele não faz nada sem IA". A aula
   não chama modelo: fala e quadro de cada passo vivem em `content/aulas/*.yaml`
   versionados. O quadro (`display/quadro.py`) tem vocabulário FECHADO de
   blocos, conferido na construção do banco. O DEDO aponta cada bloco novo,
   porque "quem vai usar a tela é o robô, o cursor é dedo". Detalhes que já
   custaram caro e estão no código: a aula espera a fala do turno terminar
   (duas falas simultâneas davam SEGV), roda em segundo plano (senão o robô
   fica surdo dez minutos), e "para" encerra a aula junto com as rodas.
   Conteúdo inicial: 4 aulas, 31 passos. 795 testes passando.
2. **`1518f77` [tela]** — o dedo media a posição com rolagem suave em
   andamento e apontava para onde o bloco ESTAVA; e o kiosk servia página de
   duas versões atrás vinda do cache do perfil do Firefox. Nasceu
   `tools/recarregar_kiosk.sh` (mata o firefox-esr, apaga o cache, reabre).
3. **`82b484e` [conversa] — o robô OBEDECE com a cota zerada.** Quem escrevia
   o marcador `#acao:` era só o modelo: sem ele, as 16 ações do catálogo
   ficavam inalcançáveis. Agora, com a IA fora, o planner tenta obedecer
   sozinho — degrau ABAIXO do modelo, não classificador de intenção (os três
   motivos da recusa de 02/08 estão respondidos no corpo do commit). Negação
   anula: "não acende a luz" não acende. 832 testes.
4. **`ecad4b1` [ensino] — AUTOCONHECIMENTO** (`content/sobre_mim.yaml`, 12
   fatos): ele responde sobre o próprio corpo sem IA, ANTES da IA na cascata,
   porque o modelo inventa hardware que não existe. Dois achados em uso real:
   "aula de ciência, por exemplo" devolvia a aula de equação (casou "exemplo"
   no CORPO da aula — a busca passou a ser só em título/matéria/resumo) e
   "por que o céu é azul" caía em "não consigo pensar". 844 testes.
5. **`61be58d`** — a pergunta do mundo com "você" dentro ("você sabe quem
   descobriu o Brasil?") era sequestrada pelo fato dos OLHOS. A busca do
   autoconhecimento passou a casar só em título e perguntas esperadas, nunca
   no texto da resposta. Mesma lição do item 4: onde casou vale mais que
   quanto pontuou.
6. **`0613d86` — as EMENTAS**: o mapa de 13 matérias e 312 assuntos, com o
   robô sabendo dizer o que ainda não sabe. Ementa é o mapa; aula é o
   território.
7. **`d542f00`** — o PLANO DE AULA e o gerador de conteúdo da Wikipédia
   (`tools/gerar_aula.py`): rascunho em `content/rascunhos/`, NUNCA direto
   nas aulas, com fonte colada (CC BY-SA). Publicar é ato humano separado.
8. **`ddd1384`** — o gerador monta a aula pelo OBJETIVO do tópico na ementa,
   não pelo índice do artigo.
9. **`71a025e`** — o REDATOR (`tools/redigir_aula.py`): UMA chamada de modelo
   por AULA, na autoria, nunca por pergunta. A conta: 308 aulas = 308
   chamadas uma vez; o caminho oposto acabaria com a cota numa tarde (e já
   acabou, às 10h de 07/08). Três travas contra invenção: vocabulário
   fechado de blocos, validação pelo mesmo carregador do robô antes de
   gravar, e fonte preservada do rascunho, não pedida ao modelo.
10. **`2c8b1d8` [internet]** — a busca para de responder com confiança sobre
    o assunto errado.
11. **`e0c752e`** — a aula PAUSA e RETOMA, e o intervalo entre passos vira
    janela de escuta.
12. **`0d624c7`** — a janela de escuta sobe para 4 segundos, e ele AVISA no
    começo da aula que ela existe: quem não sabe que o robô não escuta
    enquanto fala repete por cima da voz dele. Registrado como paliativo: a
    solução de verdade é barge-in, mudança no Voice Core, não feita.

---

## Sessão de 2026-08-08 (noite, entrando pela madrugada de 09) — o autoconhecimento estava sequestrando a conversa, e a conversa não roda onde eu achava

Sessão conduzida do PC Windows por SSH. Duas frentes: consertar o robô
respondendo texto fixo fora de hora, e produzir as aulas de programação
avançada.

### A análise do chat (260 mensagens de 07 e 08/08, tabela `conversas`)

Funcionando: a aula pausa/retoma/para na voz; comandos físicos diretos
("acenda a luz", "modo patrulha"); a memória em uso ("você já me viu hoje?" →
"vi, às 10:21" — correto); frases picotadas pelo Whisper contornadas com
pedido de esclarecimento.

Os defeitos, todos reproduzidos depois em teste:

1. **O autoconhecimento sequestrava a conversa.** "Você consegue fazer
   isso?" (sobre livre-arbítrio) recebia o fato da LUZ; "você sente falta de
   ter braço?" recebia o fato dos BRAÇOS; e pior: "gire para o lado esquerdo",
   um COMANDO, virava a palestra sobre servos — o robô narrava o corpo em vez
   de obedecer, e só girava quando o dono insistia ("Isso, FAÇA isso").
2. "Olhando pra frente." prefixando quase toda fala, às vezes duplicado.
   **Não atacado nesta sessão.**
3. A tag `#acao:deixar_recado ...` vazou no texto visível de uma resposta em
   07/08. **Não atacado nesta sessão.**

### A causa, medida

Uma palavra solta autorizava a resposta pronta. Os casos reais, reproduzidos
contra o `sobre_mim.yaml` de produção:

- "Você consegue fazer isso?" → casou "consegue" com a pergunta indexada "o
  que voce consegue ligar" → fato da LUZ.
- "gire para o lado esquerdo" → casou "lado" com "olha para os lados" →
  fato da CABEÇA.
- "você ainda não tem essa capacidade" → casou "nao" com o TÍTULO "O que eu
  NAO tenho" → fato dos BRAÇOS. Nem a negativa da frase era sobre braço.

É a mesma família perseguida no dia anterior (buscar no texto explicativo faz
qualquer palavra encostar em qualquer coisa), desta vez na direção oposta:
palavra genérica demais da PERGUNTA encostando no índice curado.

### A correção (`autoconhecimento.py`, não commitada ainda)

- **Cobertura mínima**: o fato vencedor precisa cobrir ao menos DUAS palavras
  de conteúdo da frase (ou uma, se a frase só tiver uma — "você tem câmera?"
  segue respondida). Uma palavra genérica encosta em qualquer coisa; duas já
  apontam um assunto.
- **"nao"** virou palavra-de-pergunta: negar não diz sobre o que se fala.
- **3 testes novos com as frases reais do chat**, datados, incluindo os dois
  comandos de movimento que viravam palestra.

Medição: as 6 frases sequestradas de 08/08 agora seguem para a IA; as 4
perguntas legítimas sobre o corpo continuam respondidas. Suíte do Pi: 843
passando, 0 falhando, ruff limpo. No Notebook: 16/16 do módulo, ruff limpo.

### Onde eu errei nesta sessão

**Reiniciei o serviço errado e quase declarei vitória.** Apliquei a correção
no Pi e reiniciei o `orion-motion` — mas a CONVERSA roda no **Notebook**: o
`orion-avatar.service` executa `tools/conversar_fofao.py`, e a chave da IA
(`OPENAI_API_KEY`, que guarda uma chave do Google/Gemini) só existe no
`secrets.env` do Notebook. O Pi roda movimento, banco e web; a reinicialização
era inócua para o defeito. Quem denunciou foi a verificação: o processo do Pi
não tinha a variável no ambiente, e o `ai_manager` a exige. A correção foi
espelhada no Notebook, testada lá, e o avatar reiniciado — só então a correção
passou a valer no caminho real.

Lição alinhada com a rubrica do projeto: antes de "está no ar", confirmar
ONDE o código roda, não assumir pela topologia do relatório anterior.

### Aulas de programação avançada (pedido do dono nesta sessão)

A ementa de programação já tinha o nível avançado mapeado (8 tópicos); só
havia aula do básico. Feito:

- **8 rascunhos gerados** da Wikipédia (`gerar_aula.py`), com fonte e data.
- **`prog.orion` excluído da leva**: a ementa diz "material que só ele tem",
  e a Wikipédia entregou o artigo genérico "Caso de estudo" — fonte errada,
  conteúdo errado. Essa aula tem de ser escrita do journal e dos commits do
  próprio projeto; fica como trabalho de autoria separado.
- **A redação só roda no Notebook**: a primeira tentativa no Pi falhou 7/7
  com "Missing credentials" — o `secrets.env` do Pi só tem
  `ORION_SENHA_CONFIG`. Rascunhos copiados para o Notebook, redação por lá
  (1 chamada Gemini por aula), resultado a publicar de volta no Pi.
- **AUDITORIA DE FONTE PEGOU 5 DE 8 ERRADAS.** A revisão pré-publicação
  conferiu o campo `fonte` de cada rascunho: a busca por termo do gerador
  pescou "Direto (**boxe**)" para arquitetura (veio de "módulos que não se
  chamam *direto*" na ementa), "Entrega" para redes (de "não *entrega*
  nada"), "Concorrência (**economia**)" para concorrência, uma página de
  desambiguação para hardware e "Modelo" (genérico) para IA aplicada. O
  guarda `_parecidos` confere se o título divide ALGUMA palavra com o termo
  — e artigo com parênteses de outro domínio passa. **É o mesmo defeito do
  `2c8b1d8` (busca respondendo com confiança sobre o assunto errado), agora
  dentro do gerador de aula.** Só `assincrono` ("Laço de eventos") e
  `banco-dados` tinham fonte certa.
- **Regeração com artigos apontados a mão** (`/tmp/regerar_avancado.py`,
  reusando `_artigo_completo` e `_montar_rascunho` do próprio gerador):
  cada candidato foi conferido na API da Wikipédia antes — "Arquitetura de
  software", "Concorrência (ciência da computação)", "Rede de computadores",
  "Interface serial"/"Baud"/"Microcontrolador", "Inteligência artificial
  generativa"/"Aprendizado de máquina". Detalhe que custou uma chamada: a
  Wikimedia devolve 403 sem o User-Agent com contato do projeto — estava
  documentado no `internet.py` e eu inventei um UA novo mesmo assim.
- **Fica aberto (defeito real)**: o gerador não tem como APONTAR o artigo;
  o `--artigo` ou um campo `fonte_artigo` na ementa evitaria a pescaria
  errada. O script de uso único não é solução, é registro do buraco.
- **Resultado final: 7 aulas publicadas** (`assincrono`, `arquitetura`,
  `banco-dados`, `redes`, `concorrencia`, `hardware`, `ia-aplicada`), lidas
  antes de publicar, com os 7 tópicos apontados na ementa. Programação saiu
  de "faltam 23" para "faltam 16". `saber.db` reconstruído nas DUAS máquinas
  (o desenho já prevê: o banco é índice, cada máquina constrói o seu).
  Validação pelo caminho real do professor, no Notebook: 7 pedidos falados
  simulados, 7 aulas certas. Suítes: 843 no Pi, 970 no Notebook, ruff limpo.
  `orion-avatar` reiniciado para o processo ler o banco novo.
- **Resistência do modelo à validação**: `hardware` precisou de 5 tentativas
  (insistia num bloco `formula` sem `texto`, ou devolvia JSON quebrado) e
  `arquitetura` de 7 — só passou depois de o rascunho ganhar mais dois
  artigos de base (a intro de "Arquitetura de software" tem 467 caracteres;
  com material escasso o modelo enfeitava e errava o vocabulário de blocos).
  A trava de validação segurou TODAS as tentativas ruins: nenhuma aula
  inválida chegou ao banco.
- Havia rascunhos antigos parados (`fisica.cinematica.basico` no Pi,
  `fisica.leis-newton.basico` no Notebook) — não tocados.

### Pendências, na ordem em que mordem

1. **Commitar a correção do autoconhecimento** (Pi + Notebook, mesmos 2
   arquivos) — esta entrada existe justamente porque 07/08 commitou sem
   journal; 08/08 não pode terminar com journal sem commit.
2. ~~Publicar as 7 aulas avançadas~~ — **feito nesta sessão** (ver acima).
3. **Escrever a aula `prog.orion`** a partir do journal e dos commits.
4. **O gerador não tem como apontar o artigo** — `--artigo` ou campo na
   ementa; a pescaria por termo já errou 5 de 8 numa leva.
4. "Olhando pra frente." duplicado na fala; tag `#acao:` vazada no texto.
5. O fecho repetitivo "Quer que eu...?" em quase toda resposta.
6. Rascunhos antigos de física parados nas duas máquinas.
7. O que as entradas anteriores já listavam: ressincronizar pan/tilt na
   reconexão, destinatário do recado na criação, rotação de log, Mission
   Memory, heartbeat que mente, pisca da lanterna, reboot do Notebook,
   encoder de roda, Gmail.
