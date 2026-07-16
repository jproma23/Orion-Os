# ORION OS — System Engineering Specification (SES)

## Capítulo 10 — Hardware Core (Arduino Mega)

Versão 1.1 — alinhada ao TCC (EDR-0018). O papel de Motion Core passou ao Raspberry Pi (Cap 12).

## 1. Objetivo

O Hardware Core é o subsistema de tempo real do ORION OS. Sua missão é executar movimentos com precisão, ler sensores críticos e garantir a segurança física do robô. Nenhuma decisão estratégica ou de navegação é tomada neste módulo; ele executa comandos definidos pelo Motion Core (Raspberry Pi).

## 2. Hardware Oficial

• Arduino Mega 2560
• 2 drivers TB6600
• 2 motores NEMA17
• Encoders de roda
• MPU6050
• Sensor ultrassônico frontal montado em servo (radar 180°)
• Sensor ultrassônico traseiro
• Servos (radar e Pan/Tilt)
• Sensor de temperatura e umidade
• Saída para lanterna LED
• Comunicação USB Serial com o Raspberry Pi (Motion Core)

## 3. Responsabilidades

• Gerar pulsos STEP/DIR para os TB6600.
• Controlar velocidade e aceleração (lógica de motores encapsulada aqui).
• Ler sensores e encoders em tempo real.
• Executar comandos simples com ACK e progresso.
• Enviar o pacote Radar Inteligente periodicamente (Cap 5, seção 7).
• Entrar em modo seguro quando necessário.

## 4. Máquina de Estados

BOOT
READY
IDLE
EXECUTING_MISSION
OBSTACLE_DETECTED
MISSION_PAUSED
ERROR
SAFE_MODE
SHUTDOWN

As transições deverão ser registradas em log e reportadas ao Motion Core.

## 5. Comandos Aceitos

MOVE_FORWARD
MOVE_DISTANCE
MOVE_CONTINUOUS
TURN_LEFT
TURN_RIGHT
STOP
DOCK
SCAN_FRONT
LIGHT_ON
LIGHT_OFF
RETURN_STATUS
Cada comando possui ACK, execução, progresso e conclusão.

## 6. Radar Frontal

O servo executará varreduras configuráveis entre 0° e 180°.
Cada leitura conterá:
• Ângulo
• Distância
• Timestamp
O Motion Core reconstrói o mapa polar do espaço à frente.

## 7. Uso da MPU6050

A IMU será utilizada para:
• Correção fina da execução local.
• Detecção de inclinação.
• Identificação de impactos.
• Detecção de tombamento.
O Hardware Core apenas mede e corrige a execução local; a navegação permanece no Motion Core (Raspberry).

## 8. Telemetria

O pacote periódico Radar Inteligente contém distâncias dos ultrassons, orientação do MPU, estado dos motores, velocidade estimada (encoders), temperatura, umidade e estado geral. O Raspberry converte esses dados nos eventos:
motion.status
motion.position
motion.obstacle_front
motion.obstacle_rear
motion.temperature
motion.humidity
motion.imu
motion.light_state
motion.error

## 9. Segurança (Fail-safe)

• Parada reativa por distância mínima, inclinação e timeout — sem depender do Raspberry ou do Notebook.
• Proteção contra comandos inválidos.
• Timeout configurável para comandos.
• Validação de parâmetros antes da execução.
• Recuperação automática quando possível.

## 10. Firmware

O firmware será modular:
• Motor Manager
• Sensor Manager
• Radar Manager
• IMU Manager
• Encoder Manager
• Command Executor
• Telemetry Manager
• Communication Manager
• Safety Manager

## 11. EDR-0007 (mantido) + EDR-0018

Decisão: manter o Arduino Mega exclusivamente como controlador de tempo real, agora subordinado ao Raspberry Pi.
Motivação:
• Determinismo.
• Simplicidade.
• Alta confiabilidade.
• Facilidade para futura substituição por outro controlador mantendo o mesmo protocolo.

## Conclusão

O Hardware Core constitui a camada física do ORION OS, executando comandos com precisão e fornecendo telemetria contínua ao Motion Core, preservando a separação entre inteligência, navegação e controle em tempo real.
