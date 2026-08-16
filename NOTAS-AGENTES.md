# NOTAS-AGENTES — coordenação entre agentes de IA

Arquivo compartilhado entre os agentes que trabalham neste projeto
(Kimi Code CLI no Windows, opencode/DeepSeek, etc.). **Leia antes de mexer
no projeto e atualize ao terminar.** Entradas novas vão NO TOPO, com data e
assinatura do agente. Não apague entradas antigas — marque como resolvidas.

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

Em paralelo no barramento I2C da MPU6050. Sem conflito de endereço
(`0x68` IMU, `0x1C` QMC6310 antigo se ainda estiver ligado, `0x1E`/`0x0D` novo).

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
