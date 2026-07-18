# ORION OS — System Engineering Specification (SES)
## Capítulo 12 — Motion Core / Navegação (Raspberry Pi)
Versão 1.1 — alinhada ao TCC (EDR-0018)

## 1. Objetivo
O Motion Core é o subsistema responsável por planejar e supervisionar o deslocamento do Fofão. Ele executa no **Raspberry Pi**, transforma missões de alto nível recebidas do Notebook em sequências de comandos para o Hardware Core (Arduino) e garante navegação segura utilizando fusão de sensores: radar frontal, ultrassônico traseiro, encoders e IMU.

## 2. Princípio Fundamental
O Notebook decide; o Raspberry navega; o Arduino executa. Nenhum algoritmo de navegação roda no Arduino, que recebe apenas comandos simples (MOVE_FORWARD, MOVE_DISTANCE, TURN_LEFT, TURN_RIGHT, SCAN_FRONT, STOP, DOCK) e devolve telemetria.

## 3. Modos de Navegação
- PATROL — patrulha autônoma por rotas conhecidas.
- FOLLOW — seguimento de pessoa autorizada.
- GOTO — deslocamento até um ambiente conhecido.
- EXPLORE — exploração supervisionada de área nova.
- MANUAL — comandos diretos do usuário.
- HOLD — posição parada com sensores ativos.

## 4. Patrulha
- Rotas definidas como sequências de segmentos (distância + giro).
- Antes de cada segmento: SCAN_FRONT para atualizar o mapa local.
- Obstáculo detectado → replanejamento local ou pausa da patrulha.
- Eventos relevantes (pessoa detectada, ambiente alterado) são reportados ao Mission Core, que decide interromper ou continuar.

## 5. Seguimento de Pessoas
Fluxo:
1. Vision Core (Notebook) publica vision.person_detected com coordenadas.
2. Mission Core confirma autorização da pessoa (Memory Core) e envia FOLLOW_TARGET ao Raspberry.
3. Motion Core calcula correções de direção para manter o alvo centralizado e à distância configurada.
4. Comandos curtos e contínuos são enviados ao Hardware Core.
5. Perda do alvo → SCAN_FRONT + rotação de busca; após timeout, retorno ao modo anterior.

## 6. Desvio de Obstáculos
Camadas de proteção:
- Camada reativa (Hardware Core/Arduino): parada imediata por distância mínima, sem consultar o Raspberry.
- Camada tática (Motion Core/Raspberry): usa o mapa do radar 180° para escolher direção livre.
- Camada estratégica (Mission Core/Notebook): decide abortar, replanejar ou pedir ajuda.
Distâncias de segurança são parametrizadas no Configuration Core.

## 7. Uso do Radar Frontal
- Varredura completa (0°–180°) antes de movimentos longos.
- Varredura setorial rápida durante deslocamento.
- Cada varredura gera um mapa polar simplificado (ângulo → distância) mantido no Raspberry e sincronizado com o Notebook.
- Mapas consecutivos permitem detectar objetos móveis.

## 8. Fusão de Sensores
O Motion Core combina, a cada pacote Radar Inteligente:
- Odometria por encoders e passos dos motores.
- Orientação e aceleração da MPU6050.
- Distâncias ultrassônicas.
Resultado: posição estimada (x, y, orientação) e velocidade real, publicadas como motion.position.
A IMU também fornece detecção de inclinação perigosa, impacto e tombamento → evento crítico e SAFE_MODE.

## 9. Autocalibração (primeira inicialização)
Na primeira inicialização o robô realiza um autoteste, executa um deslocamento controlado e o Vision Core (Notebook) compara a distância real percorrida com a prevista pela odometria. Um fator de correção é calculado e armazenado na configuração para compensar tolerâncias mecânicas (diâmetro das rodas, escorregamento, folgas). A autocalibração pode ser repetida sob demanda ("Fofão, calibrar").

## 10. Planejamento de Movimento
- Todo plano possui critérios de sucesso e de cancelamento antes do envio.
- Velocidade e aceleração limitadas por perfil de segurança.
- O plano combina o mapa global produzido pelo Notebook com o mapa local do radar.

## 11. Eventos Publicados
navigation.plan_created
navigation.segment_started
navigation.segment_completed
navigation.obstacle_avoided
navigation.target_lost
navigation.mode_changed
navigation.error

## 12. Evolução Futura
A arquitetura permite incorporar LiDAR e SLAM (ORION OS 2.0+) substituindo apenas a fonte do mapa local no Raspberry, sem alterar o Hardware Core nem o protocolo.

## 13. EDR-0018 (substitui EDR-0009)
Decisão: concentrar a navegação no Raspberry Pi, mantendo no Arduino apenas reações de segurança imediatas e no Notebook apenas decisões estratégicas.
Motivação:
- Reduz carga do Notebook e latência do laço de navegação.
- Determinismo preservado no controlador de tempo real.
- Evolução para SLAM sem troca de firmware.

## Conclusão
O Motion Core dá ao Fofão a capacidade de se mover com propósito e segurança, mantendo a separação rigorosa entre decisão estratégica, navegação e execução em tempo real definida pela arquitetura do ORION OS.
