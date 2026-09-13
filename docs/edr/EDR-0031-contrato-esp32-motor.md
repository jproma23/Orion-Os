# EDR-0031 — Contrato do ESP32 de motor

**Data:** 13/09/2026
**Status:** Decidido
**Relacionado:** EDR-0028 (fronteira), EDR-0027 (ACK × RESPONSE)

---

## Contexto

O controle de motor passou de steppers TB6600 para motores DC JGB37-520 com encoder, por
eficiência energética. Motor DC em malha aberta não dá odometria; a malha fechada precisa
rodar em algum lugar com temporização confiável.

O ESP32 existente já cuida de ToF, servos e sensores, e fala com o Pi por WebSocket/WiFi.

## Decisão

**Um ESP32 dedicado ao motor, separado do ESP32 de sensores.** Ele é o dono exclusivo da
fronteira `cmd_vel` ↔ `odom` — é o nó do outro lado do que o Nav2 espera.

### Transporte

**UART cabeada com o Pi. Nunca WiFi.**

Laço de velocidade sobre WiFi produz jitter, e o sintoma aparece como Nav2 instável sem
causa aparente. O ESP32 de sensores pode permanecer em WebSocket; o de motor, não.

### Contrato

| Direção | Conteúdo | Taxa |
|---|---|---|
| Pi → ESP32 | Velocidade alvo por roda, em **rad/s** | sob demanda |
| ESP32 → Pi | Contagem de ticks por roda + **timestamp** | ~50 Hz |

O timestamp é do ESP32, não do Pi — a integração de odometria precisa do instante real da
amostra, não do instante de chegada pela UART.

### Watchdog

**Sem comando novo em 200 ms, os motores param.** Implementado no firmware, autônomo.

Nunca desabilitado, nem temporariamente para debug. Sem ele, um travamento do Pi deixa o
robô andando sozinho.

### Contagem de pulsos

**PCNT em modo quadratura** (periférico de hardware do ESP32, 8 unidades, duas em uso).
Não contar por interrupção — a alguns kHz isso rouba ciclos do laço de controle.

### Pinagem

| Função | GPIO | Motivo |
|---|---|---|
| Encoders (4 canais) | 34, 35, 36, 39 | Input-only; inúteis como saída, perfeitos aqui |
| IN1–IN4, ENA/ENB | comuns | Evitar 0/2/12/15 (strapping) e 6–11 (flash) |

**Encoder alimentado com os 3,3 V do ESP32.** Nunca 5 V, nunca a tensão do motor. O encoder
tem Vcc próprio, separado do Vcc do motor. Em 5 V os pulsos saem em 5 V e os GPIOs do ESP32
não são 5 V tolerantes.

### PID

Termo integral e feedforward são necessários: motor com redução tem zona morta, e velocidade
baixa é exatamente o regime que o Regulated Pure Pursuit usa.

## Driver de potência

**Preferido: TB6612FNG.** Queda ~0,5 V, 1,2 A contínuo, 3,2 A pico.

**Se permanecer o L298N**, três ajustes obrigatórios:

1. **Remover o jumper "ATIVA 5v".** O regulador da placa (78M05) precisa de ~7 V na entrada;
   com 6 V não regula, e esse rail alimenta a lógica do L298. Alimentar o terminal 5 V por fora.
2. **Remover os jumpers ENA/ENB.** Com eles não há PWM, só liga/desliga.
3. **Alimentar a ponte com 8 V**, não 6 V, compensando a queda de 1,5–2 V para entregar os
   6 V nominais ao motor. Dissipador obrigatório: ~2 W de calor a 1 A.

## Energia

Rail de motor separado do rail lógico, com buck dedicado. Pi e ESP32s em rails próprios.
Pico de partida e ruído de escova derrubam o Pi.

## Critério de aceite (Gate da F1)

- 2 m em linha reta: odometria vs. fita métrica, erro < 5 %
- 360° no lugar: erro de heading < 10°
- Desconectar a UART e confirmar parada pelo watchdog

**Rodas no ar em todo primeiro acionamento após qualquer mudança.**

## Dados faltantes a confirmar antes da implementação

- **Redução do JGB37-520** (varia de 1:9 a 1:270) — define ticks/volta e velocidade máxima
- **Peso do Fofão montado** — define se o torque fecha
