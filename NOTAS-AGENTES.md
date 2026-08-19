# NOTAS-AGENTES — coordenação entre agentes de IA

Arquivo compartilhado entre os agentes que trabalham neste projeto
(Kimi Code CLI no Windows, opencode/DeepSeek, etc.). **Leia antes de mexer
no projeto e atualize ao terminar.** Entradas novas vão NO TOPO, com data e
assinatura do agente. Não apague entradas antigas — marque como resolvidas.

---
## 2026-08-19 — Reconhecimento facial ATIVO (rostos recadastrados por foto) + protocolo anti-colisão

**Agente:** Kimi Code CLI (Windows)
**Estado:** concluído e validado em produção. Nada commitado nesta sessão; working trees intactos.

### Feito

1. **Embeddings de João Paulo, Marah e Kamal regenerados A PARTIR DE FOTOS**
   (o dono mandou as fotos; ninguém precisou ficar na frente da câmera).
   Script temporário: `/tmp/gen_embeddings.py` (Notebook, tenta rotações -
   a foto do Kamal estava inclinada 10°) e `/tmp/atualizar_faces.py` (Pi).
   UPDATE direto na tabela `pessoas`, sem inserir duplicado.
   **Backup ANTES de gravar:** `/mnt/ssd/orion/orion_backup_pre_faces_2026-08-18.db`.
2. **Validado em produção (19/08 ~06:23):** avatar logou
   `Vision Core: 3 rosto(s) conhecido(s) carregado(s)` e, com o dono na
   frente da câmera, `diário: pessoa_vista {'nome': 'João Paulo'}` +
   `conversa creditada a 'João Paulo' (pessoa_id=1)`.

### Lições (corrigindo conclusões erradas que eu mesmo quase espalhei)

- **O banco fica em `/mnt/ssd/orion/orion.db`, não em `data/`.** A tabela
  `pessoas` JÁ tinha embeddings válidos dos três (distância antigo→novo
  0.18-0.33 = mesmos rostos). Quase concluí "zero rostos cadastrados"
  procurando no lugar errado.
- **A "câmera do Notebook" é o STREAM DO PI** (`http://pi-os.local:8091/stream`).
  Pi fora do ar = Notebook sem visão E sem rostos (o `_insistir_familia`
  do `modulo.py` cobre: retenta 30x a cada 10 s - hoje funcionou, carregou
  na 3ª tentativa quando o Pi voltou de reboot).
- **Saudação espontânea tem DUAS travas** (saudacao.py): 30 min entre
  cumprimentos à mesma pessoa e janela de 15 min após qualquer conversa.
  No teste do dono o wake word veio 6 s ANTES do reconhecimento - a
  supressão foi correta, não bug. Teste limpo = aparecer em silêncio 15 min
  depois da última conversa.

### PROTOCOLO ANTI-COLISÃO (pedido do dono, 2026-08-19: "tem outro agente trabalhando; se precisar reiniciar, não atrapalhar um ao outro")

Vale para TODOS os agentes a partir desta data:

1. **Antes de REINICIAR serviço** (orion-avatar / orion-motion /
   orion-camera-stream): `git log --oneline -3` + `git status --short` na
   máquina. Se houver commit novo de outro agente ou arquivo mexido há
   minutos (`stat`), NÃO reiniciar - deixar nota aqui e aguardar.
2. **Antes de COMMITAR:** `git log` primeiro; commitar SÓ os próprios
   arquivos, nunca `git add -A` (lição da Mission Memory).
3. **Ao terminar a sessão:** entrada aqui no topo + doc de estado no
   Windows (`Desktop/ORION_estado_2026-08-09.md`), que hoje carrega a
   TRILHA DE EVOLUÇÃO (auto-reparação em níveis + discernimento como
   camada transversal - direção do dono de 2026-08-19, ler antes de
   planejar trabalho novo).
4. Reinício feito por MIM nesta sessão: `orion-avatar` ~06:18 (recarregar
   rostos). Na ocasião não havia trabalho recente de outro agente no tree.

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
