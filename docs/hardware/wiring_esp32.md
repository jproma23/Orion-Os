# Fofão — Guia de ligação elétrica do ESP32 (Hardware Core)

Fonte canônica dos pinos: `firmware/hardware_core/include/pins.h`. Este
documento é a versão legível para montagem de bancada — se os dois
divergirem no futuro, `pins.h` é que vale.

**Placa:** ESP32-D0WD-V3 (DevKit 38 pinos), desde 2026-08-02. Substituiu o
Arduino Mega, que morreu. Os pinos do Mega ficam preservados no `pins.h`
como comentários `(Mega: N)` — o que mudou foi o número do pino, não a
ligação física do periférico.

**Status:** motores e relés validados em bancada; sensores I2C (bússola)
validados em 2026-08-16.

---

## 1. Domínios de energia — ler antes de ligar qualquer coisa

Os motores **não** são alimentados pelo ESP32 nem pela Raspberry Pi.

| Domínio | Fonte | Tensão | Alimenta |
|---|---|---|---|
| **Lógica** | USB da Raspberry Pi | 3,3V/5V | ESP32 (3,3V nativo), DHT, bússola, lado de controle dos TB6600 |
| **Motor** | Fonte externa dedicada | 12–24V (conforme o NEMA17) | 2× TB6600 → NEMA17 |

Os TB6600 são opto-isolados e **não** precisam de GND comum com o ESP32
para STEP/DIR. (Já os encoders indutivos exigem GND comum — ver seção 6.)

**Atenção de tensão do ESP32:** a lógica é **3,3V** nativa. Qualquer sinal
de 5V vindo de periférico exige divisor resistivo ou level shifter (ECHO
do HC-SR04, LiDAR STL-50B2, sensores Cotrinex) — ligar 5V direto no GPIO
queima a placa.

## 2. Raspberry ↔ ESP32

Um único cabo USB comum (aparece como `/dev/serial/by-id/usb-Silicon_Labs_CP2102_...`
nesta montagem, baud 250000) — sem fiação GPIO entre as duas placas. O
protocolo ORION responde WHO_ARE_YOU, ACK e HEARTBEAT por esse cabo. A
UART0 (GPIO 1/3) pertence ao protocolo e não pode ser reaproveitada.

## 3. Motores — 2× NEMA17 via 2× TB6600 (pinos confirmados)

Ligação real: PUL+/DIR+ nos pinos abaixo e PUL-/DIR- no GND da placa
(retorno do optoacoplador — sem ele o driver não vê pulso nenhum).

| Pino ESP32 | Sinal | Destino no driver |
|---|---|---|
| 27 | STEP esquerdo | PUL+ (driver esquerdo) |
| 14 | DIR esquerdo | DIR+ (driver esquerdo) |
| 25 | STEP direito | PUL+ (driver direito) |
| 26 | DIR direito | DIR+ (driver direito) |
| 13 | ENABLE (compartilhado) | **NAO CONECTADO** (decisão do dono, drivers sempre habilitados) |
| GND | Referência comum | PUL-, DIR-, EN- dos dois drivers |

> **LADOS TROCADOS EM 2026-08-05:** o driver nos pinos 25/26 aciona a roda
> DIREITA e o de 27/14 a ESQUERDA. O nome da constante segue a RODA, não o
> fio — `STEP_ESQUERDO` fala da roda esquerda de verdade.
>
> **RISCO ABERTO:** o opto do TB6600 foi dimensionado para 5V; o ESP32
> entrega 3,3V, derrubando a corrente no LED do opto de ~14mA para ~8mA.
> Pode ser suficiente ou não — se não disparar, a correção é um level
> shifter na linha de PUL/DIR, não mexer no firmware.

TB6600 costuma ter EN+ ativo em nível baixo — o firmware já assume isso
(`ENABLE_ATIVO_EM_BAIXO = true` em `motor_manager.h`).

Sentido de rotação: os motores se olham (montagem espelhada), então o mesmo
nível de DIR gira um pra frente e outro pra trás. O firmware já inverte os
lados (`DIR_INVERTIDO_DIREITO = true`). Se um motor girar ao contrário,
inverta o par A+/A- ou ajuste o sinal de DIR.

## 4. Sensores I2C — bússola GY-271 (HMC5883L ou clone QMC5883L)

Barramento I2C: **SDA = GPIO 21, SCL = GPIO 22**. O módulo é alimentado com
**3,3V** (VCC do ESP32, sem passar pelo regulador da plaquinha).

| Sinal | Fio GY-271 | Destino (ESP32) |
|---|---|---|
| VCC | vermelho | 3V3 |
| GND | preto | GND |
| SCL | verde | GPIO 22 |
| SDA | branco | GPIO 21 |

> **Fiação corrigida em 2026-08-16:** SDA/SCL estavam invertidos; o módulo
> deve ficar **deitado horizontal** com o eixo X apontando para a frente.
> A bússola só é detectada no scan I2C do **boot** — após mexer nos fios é
> preciso dar reset no ESP32.

O firmware faz autodetecção: tenta o HMC5883L verdadeiro (0x1E, identidade
"H43") e cai para o clone QMC5883L (0x0D). Calibração (giro de 25s) usa
mágico `0x5883`.

> **IMU (MPU6050):** descartada pelo dono em 2026-08-16 — segue sem ela.

### DHT11 / DHT22 — temperatura e umidade

| Pino ESP32 | Sinal |
|---|---|
| 4 | DATA |
| 3V3/5V | VCC (DHT22 aceita; DHT11: 3,3V evita lixo no pull-up) |
| GND | GND |

Firmware assume **DHT11** por padrão (`dht_manager.h`).

## 5. Servos Pan/Tilt

Servo hobby padrão de 3 fios (SG90 ou similar). Servos aceitam sinal de
3,3V sem adaptação; a alimentação continua em 5V dedicada.

| Fio do servo (cor comum) | Sinal | Pino ESP32/fonte |
|---|---|---|
| Laranja ou amarelo | PWM (sinal) | **Pan → GPIO 19** / **Tilt → GPIO 23** |
| Vermelho | VCC (+5V) | fonte de 5V dedicada aos servos |
| Marrom ou preto | GND | comum com o GND do ESP32 |

**Domínio de energia dos servos:** picos de corrente (500mA–1A) que o 5V
lógico do ESP32 não aguenta — use fonte 5V dedicada, com GND comum. Não
ligue o VCC dos servos direto no pino 5V.

Comando: `SET_PAN_TILT` com payload `{"pan_graus": <-80..80>, "tilt_graus":
<-30..45>}` (centro em 90°). **Fiação corrigida em 2026-07-20:** os fios de
sinal estavam cruzados (pan pino 19, tilt pino 23 agora batem).

## 6. Periféricos reservados / com fiação prevista

| Periférico | Pino ESP32 | Observação |
|---|---|---|
| Encoder esquerdo (indutivo NPN) | 15 | pull-up interno 3,3V; GND comum obrigatório. NPN só puxa pra baixo — seguro |
| Encoder direito (indutivo NPN) | 16 | idem |
| Rele motores (alimenta TB6600) | 5 | ativo em LOW; nasce HIGH = motores SEM energia (fail-safe no silício) |
| Rele ventilador (cooler Pi) | 17 | ativo em LOW; fisicamente DESCONECTADO desde 2026-07-24 |
| Rele lanterna | 2 | ativo em LOW (LOW acende); pisca no boot (strapping) — aceito |
| Bateria (divisor) | 36 | ADC1 (continua com WiFi). Divisor do Mega **NÃO serve** (satura em 3,3V) — recalcular antes de montar |
| Servo do radar | 18 | mesma fonte dedicada dos servos |

## 7. Regras do ESP32 (não mexer sem reler `pins.h`)

1. GPIO 6..11 são a flash — usar qualquer um trava o boot.
2. GPIO 34..39 só leem (ECHO, encoder, bateria).
3. ADC2 para de funcionar com WiFi ligado — toda leitura analógica no ADC1
   (GPIO 32..39). Por isso BATERIA_SENSE = 36.
4. GPIO 12 e 15 são strapping — se estiverem em HIGH no boot, a placa não
   sobe. Encoder 15 foi escolhido de propósito (ver `pins.h`).
5. GPIO 1/3 são a UART0 (protocolo com o Raspberry). Nunca reaproveitar.

## 8. Ordem de montagem recomendada

1. **Tudo desligado** — ESP32 desconectado da Pi, fontes desligadas.
2. **Ligue os GNDs primeiro** — ESP32 GND ↔ GND dos TB6600 ↔ fonte de motor
   ↔ fonte dos servos ↔ GND dos encoders.
3. **Sinais de controle dos motores** — STEP/DIR (seção 3).
4. **Sensores** — bússola I2C (3,3V), DHT — confira 3,3V vs 5V em cada módulo.
5. **Servos pan/tilt** — sinal 19/23, VCC na fonte dedicada.
6. **Motores nos terminais dos TB6600** — bobinas NEMA17 (A+/A-/B+/B-).
7. **Dip switches de corrente do TB6600** — conforme o NEMA17.
8. **Energize só o lado lógico** — USB ESP32↔Pi. `tools/sim_hardware_core.py`
   ou o teste real deve mostrar HEARTBEAT e WHO_ARE_YOU.
9. **Por último as fontes de motor/servos** — teste movimento curto com as
   rodas suspensas.

## 9. Checklist antes de energizar

- [ ] GNDs do ESP32, TB6600, fontes e encoders todos em comum
- [ ] Nenhum sinal de 5V entrando em GPIO (divisor/level shifter onde precisar)
- [ ] Fontes de motor/servo desligadas até o passo 8
- [ ] Nenhum fio de motor encostando em outro (curto nas bobinas)
- [ ] Dip switches do TB6600 conferidos com a etiqueta do NEMA17
- [ ] Bússola deitada, SDA=21 / SCL=22, VCC=3,3V
- [ ] Cabo USB ESP32↔Pi firme
- [ ] Rodas suspensas para o primeiro teste de movimento
