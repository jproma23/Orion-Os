# Placa 3.5" IPS ESP32-S3 (ES3C35P) — mapa de pinos completo

Fonte: `5-原理图_Schematic/ESP32-S3_IO资源分配表.xlsx` + `Example_17_echo` do
pacote do fabricante (RAR de 237 MB baixado em 2026-09-12).

> **Aviso do fabricante:** o cabeçalho da planilha diz "ESP32-WROOM-32E" — é
> erro de copy-paste deles. O conteúdo da tabela é do S3 e bate com os exemplos.

Esta é a placa que vira a **cabeça do Fofão** (decisão de 2026-09-09, ver
`project_orion_notebook_removido_tela_esp32`). O firmware é o `~/orion_face`.

---

## ⚠️ Leia isto antes de mexer no áudio

**O microfone desta placa está MORTO** (diagnosticado em 2026-09-12, ver
`docs/journal.md`, sessão de 12/09). Não repita o bring-up:

- `es8311_microphone_config(es, false)` (analógico) **já está certo** — o MEMS
  `LMA2718B381-OA7` é analógico mesmo, a datasheet prova (OUT/GND/VDD/GND,
  sem clock nem linha de dados).
- `AUDIO_EN` (GPIO1) habilita em nível **BAIXO**, não alto. Já corrigido.
- Bug do driver vendor: `REG0D` fica em `VMIDSEL=1` ("iniciando carga") e
  nunca avança pra `VMIDSEL=2` ("operação normal"). Já corrigido escrevendo
  o registro direto via I2C. **Sem efeito.**
- Registradores conferidos um a um contra a datasheet: PGA ligado, modulador
  ligado, bias/referência do ADC ligados, LINSEL no mic diferencial, mute em
  zero dos dois lados, ganho 30 dB analógico + 18 dB digital. Tudo correto.

Com o software provadamente correto e **zero sinal**, o defeito é físico:
solda, MEMS queimado, ou rede de acoplamento. **Falta passar multímetro na
saída do MEMS** — ninguém fez isso ainda.

**Quem resolve a audição hoje** são os "ouvidos ESP32" separados (2 mics I2S
16 kHz, TCP porta 9100), já ligados ao `VoiceCore`. A saída de áudio
(playback) desta placa **não** foi descartada — só o mic é que está mudo.

---

## Módulo

| Item | Valor |
|---|---|
| Módulo | ESP32-S3-WROOM-1 **N16R8** |
| Flash | 16 MB QSPI |
| PSRAM | 8 MB **OPI (octal)** — confirmado por IO26 + IO33–37 ocupados |
| Display | 3.5" IPS 320×480, driver **ST77922**, interface **QSPI** |
| Touch | capacitivo **FT6336G**, I²C |
| Codec de áudio | **ES8311** (I²C 0x18) |
| Microfone | MEMS **analógico** LMA2718B381-OA7 → ADC do ES8311 |
| Amplificador | **FM8002E** classe D |
| LED RGB | WS2812B-V5-W |
| Carga de bateria | TP4054 |

## Áudio (ES8311)

| Sinal | GPIO | Observação |
|---|---|---|
| I2S MCLK | **17** | MCLK = fs × 384 |
| I2S BCLK | **18** | |
| I2S WS / LRCK | **21** | |
| I2S DOUT | **15** | ESP → codec (playback) |
| I2S DIN | **16** | codec → ESP (mic) |
| I²C SDA | **38** | compartilhado com o touch |
| I²C SCL | **39** | compartilhado com o touch |
| PA enable | **1** | amp FM8002E — ativo BAIXO, não sai no header |

Formato do exemplo `echo`: **16 kHz / 16 bit / stereo slot / Philips**, I²C a
400 kHz. É exatamente o que o AFE do ESP-SR espera — não precisa reamostrar.

TX e RX no mesmo barramento I²S ⇒ **AEC disponível** (escutar enquanto fala).

## Display (QSPI — ST77922)

| Sinal | GPIO |
|---|---|
| CS | **10** |
| CLK | **12** |
| D0 | **11** |
| D1 | **13** |
| D2 | **14** |
| D3 | **9** |
| DC (comando/dado) | **42** |
| Backlight (PWM) | **41** |
| Reset | via pino **EN** (compartilhado com o reset do ESP32) |

## Touch (I²C — FT6336G)

| Sinal | GPIO |
|---|---|
| SDA | **38** |
| SCL | **39** |
| INT | **48** (ativo baixo) |
| RST | **47** (ativo baixo) |

## Cartão SD (SDIO)

| Sinal | GPIO |
|---|---|
| CLK | **5** |
| CMD | **4** |
| D0 | **6** |
| D1 | **7** |
| D2 | **2** |
| D3 | **3** |

## Diversos

| Função | GPIO |
|---|---|
| Botão BOOT | **0** |
| LED RGB (WS2812, 1-wire) | **40** |
| ADC de bateria | **8** |
| USB D− / D+ | **19 / 20** |
| UART0 TX / RX | **43 / 44** |

## PSRAM / Flash (não usar)

`IO26`, `IO27`–`IO32`, `IO33`–`IO37`

---

## GPIOs disponíveis — o gargalo da placa

| GPIO | Sai no conector? | Situação |
|---|---|---|
| **38** | Sim | I²C SDA (touch + codec) — só como I²C |
| **39** | Sim | I²C SCL (touch + codec) — só como I²C |
| **43** | Sim | UART0 TX — livre se o console for por USB-CDC |
| **44** | Sim | UART0 RX — idem |
| 45 | **Não** | livre no chip, sem conector (strapping pin) |
| 46 | **Não** | livre no chip, sem conector (strapping pin) |

Todo o resto está comprometido.

### Opções para o servo

1. **GPIO43 ou 44** — habilitar `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG` para
   mover o log pro USB e liberar a UART0. Um servo, solução direta.
2. **PCA9685 no I²C (38/39)** — 16 canais PWM, endereço configurável, não
   conflita com o touch (0x38) nem com o codec (0x18). Caminho certo se for
   mais de um servo.

Em qualquer caso: **fonte separada para o servo**. A placa sozinha já puxa
~560 mA com tela + áudio + carga de bateria. Ver
`project_orion_servo_derruba_esp32`: servo em estol derruba o ESP32.

---

## Ordem de trabalho do ESP-SR (plano original do fabricante)

Registrado por completude, mas **o passo 1 já foi executado e reprovou** —
ver o aviso no topo.

1. ~~Subir `Example_17_echo` sem alteração → valida mic, codec e alto-falante.~~
   Feito em 12/09: playback OK, **mic morto**.
2. Trocar a camada de I/O do projeto do tutorial (INMP441 / MAX98357A) pelo
   `es8311.c/h` do pacote + acionamento do GPIO1.
3. `menuconfig`: flash 16 MB, PSRAM octal habilitada, partição de modelos do
   ESP-SR com 4–5 MB.
4. Fixar o AFE em um core e o LVGL no outro (`xTaskCreatePinnedToCore`).
5. Só então ativar WakeNet + MultiNet.

Enquanto o mic não for consertado no ferro de solda, WakeNet/MultiNet **nesta
placa** não têm entrada de áudio. A palavra de ativação roda hoje no Pi, com
os ouvidos ESP32 separados.
