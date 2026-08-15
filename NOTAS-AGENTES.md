# NOTAS-AGENTES — coordenação entre agentes de IA

Arquivo compartilhado entre os agentes que trabalham neste projeto
(Kimi Code CLI no Windows, opencode/DeepSeek, etc.). **Leia antes de mexer
no projeto e atualize ao terminar.** Entradas novas vão NO TOPO, com data e
assinatura do agente. Não apague entradas antigas — marque como resolvidas.

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

---

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
