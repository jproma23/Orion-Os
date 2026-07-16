# ORION OS — System Engineering Specification (SES)

## Capítulo 5 — Arquitetura de Comunicação Distribuída

Versão 1.1 — alinhada ao TCC (EDR-0018)

## 1. Objetivo

Definir o protocolo oficial de comunicação entre o Mission Core (Notebook), o Motion Core (Raspberry Pi) e o Hardware Core (Arduino Mega). O protocolo deverá ser escalável, resiliente, versionado, totalmente local e preparado para futuras expansões.

## 2. Princípios

• O Notebook é o coordenador estratégico; o Raspberry é o coordenador de movimento.
• A comunicação é em cadeia: Notebook ↔ Raspberry ↔ Arduino.
• O Notebook nunca fala diretamente com o Arduino.
• Toda comunicação é baseada em eventos e comandos.
• Todo pacote possui identificação, versão e verificação de integridade.

## 3. Topologia

Notebook ↔ Raspberry Pi: Ethernet (TCP), totalmente local.
Raspberry Pi ↔ Arduino Mega: USB Serial.
Notebook ↔ Raspberry Pi (USB direto): opcional, apenas para manutenção e diagnóstico.

## 4. Event Bus

Todos os módulos publicam eventos em um barramento lógico no Notebook. O Raspberry republica na forma de eventos os dados que recebe do Arduino.
Exemplos:
vision.person_detected
vision.object_detected
voice.wake_word
motion.obstacle_front
motion.obstacle_rear
motion.mission_complete
system.boot_complete
system.error
battery.low
light.dark_environment

## 5. Estrutura do Pacote

Cada pacote conterá:
- Header
- Versão do protocolo
- Origem
- Destino
- Tipo (Comando, Evento, Resposta, Telemetria)
- Identificador único
- Timestamp
- Payload
- CRC/Checksum

O mesmo formato de mensagem é usado nos dois enlaces (TCP e Serial); muda apenas o transporte.

## 6. Missões e Comandos

O Mission Core envia missões de alto nível ao Motion Core:
MOVE_TO
FOLLOW_TARGET
PATROL
STOP
LIGHT_ON
LIGHT_OFF
SCAN_FRONT
HOME_POSITION / DOCK

O Motion Core decompõe as missões em comandos simples ao Hardware Core:
MOVE_FORWARD
MOVE_DISTANCE
TURN_LEFT
TURN_RIGHT
STOP
DOCK
SCAN_FRONT
LIGHT_ON / LIGHT_OFF
RETURN_STATUS

Toda a lógica de controle dos motores permanece encapsulada no Arduino. Cada comando possui ACK, execução, progresso e conclusão.

## 7. Radar Inteligente (Telemetria)

O Arduino envia periodicamente ao Raspberry um pacote resumido contendo:
• Distâncias medidas pelos ultrassons (frontal por ângulo e traseiro)
• Orientação do MPU
• Estado dos motores
• Velocidade estimada
• Temperatura e umidade
• Demais parâmetros necessários para a navegação

O Raspberry converte esse pacote em eventos motion.* e telemetria consolidada para o Notebook.

## 8. Comunicação do Motion Core com o Notebook

O Raspberry envia apenas resultados:
• Posição estimada (x, y, orientação)
• Estado da navegação e progresso das missões
• Obstáculos e mapa polar do radar
• Telemetria consolidada do hardware
Nunca transmite dados brutos quando não solicitado.

## 9. Recuperação de Falhas

Heartbeat periódico nos dois enlaces.
Reconexão automática.
Fila de mensagens pendentes.
Registro de falhas no banco de dados.
Reinicialização isolada de módulos sem reiniciar o sistema completo.

## 10. Engineering Decision Records

EDR-0002: arquitetura baseada em Event Bus (baixo acoplamento, expansão, plugins).
EDR-0018: topologia em cadeia com Ethernet Notebook↔Raspberry e Serial Raspberry↔Arduino.

## Conclusão

Este protocolo é obrigatório para todos os módulos do ORION OS e permanecerá compatível entre versões por meio de versionamento do protocolo.
