# ORION OS — System Engineering Specification (SES)
## Capítulo 14 — Communication Core
Versão 1.1 — alinhada ao TCC (EDR-0018)

## 1. Objetivo
O Communication Core implementa fisicamente o protocolo definido no Capítulo 5, gerenciando os enlaces Notebook ↔ Raspberry Pi (Ethernet) e Raspberry Pi ↔ Arduino Mega (USB Serial), o transporte de eventos, o heartbeat e a recuperação automática das conexões.

## 2. Topologia Física
- Notebook ↔ Raspberry Pi: Ethernet (TCP), cabo direto ponto a ponto, IPs fixos em sub-rede exclusiva do robô (ex.: 192.168.50.1 e 192.168.50.2).
- Raspberry Pi ↔ Arduino Mega: USB Serial, 115200 baud (configurável).
- Notebook ↔ Arduino: proibido (sem enlace direto).
- USB direto Notebook ↔ Raspberry: apenas manutenção e diagnóstico.

## 3. Camadas
1. Camada física: socket TCP (Ethernet) e porta serial, gerenciados pela HAL.
2. Camada de enquadramento: delimitação de pacotes, escape de bytes, CRC (necessária no enlace serial; no TCP o framing delimita mensagens no stream).
3. Camada de mensagem: JSON compacto conforme estrutura do Capítulo 5 (header, versão, origem, destino, tipo, id, timestamp, payload, checksum) — idêntica nos dois enlaces.
4. Camada de serviço: APIs send_command, publish_event, request_response.

## 4. Tipos de Mensagem
- COMMAND — missão ou instrução (exige ACK).
- ACK / NACK — confirmação de recebimento.
- EVENT — publicação assíncrona.
- TELEMETRY — dados periódicos (inclui o pacote Radar Inteligente).
- RESPONSE — resposta a uma solicitação.
- HEARTBEAT — sinal de vida.

## 5. Confiabilidade
- Todo COMMAND possui id único; ACK obrigatório em até 500 ms.
- Ausência de ACK → até 3 retransmissões → evento comm.link_degraded.
- CRC inválido → descarte silencioso + NACK.
- Fila de mensagens pendentes com prioridade (crítico > comando > telemetria).
- Mensagens críticas (STOP, SAFE_MODE) furam a fila e são propagadas ponta a ponta (Notebook → Raspberry → Arduino).

## 6. Heartbeat
- Intervalo padrão: 1 s (configurável), em cada enlace.
- 3 heartbeats perdidos → módulo marcado como DEGRADED e evento comm.module_lost.
- Reconexão automática contínua; ao restabelecer, evento comm.module_recovered e ressincronização de estado.
- O Raspberry reporta ao Notebook o estado do enlace serial com o Arduino.

## 7. APIs para os Módulos
- comm.send(destino, mensagem) → aguarda ACK.
- comm.publish(evento) → difusão pelo Event Bus.
- comm.request(destino, solicitacao, timeout) → resposta síncrona.
- comm.status() → estado dos enlaces.
Os módulos nunca acessam sockets ou portas seriais diretamente. Mensagens do Notebook para o Arduino são roteadas pelo Raspberry de forma transparente (campo destino).

## 8. Descoberta de Dispositivos
No boot:
1. O Notebook conecta ao Raspberry pelo endereço configurado (ou descoberta na sub-rede).
2. O Raspberry enumera portas seriais e envia WHO_ARE_YOU ao Arduino.
3. Cada módulo responde com nome, versão de firmware/software e versão de protocolo.
4. Versões incompatíveis → modo degradado + alerta na interface.

## 9. Segurança
- Somente mensagens com origem registrada no Service Registry são aceitas.
- Missões de movimento exigem origem Mission Core; comandos ao Arduino exigem origem Motion Core.
- Todo tráfego relevante é registrado em log estruturado.

## 10. Eventos Publicados
comm.link_up
comm.link_down
comm.link_degraded
comm.module_lost
comm.module_recovered
comm.protocol_mismatch

## 11. EDR-0011 (atualizado por EDR-0018)
Decisão: transporte como serviço com API uniforme e duas variantes — TcpTransport (Ethernet) e SerialTransport (USB) — sob o mesmo formato de mensagem.
Motivação:
- Isolamento total entre módulos e hardware de comunicação.
- Ethernet elimina o gargalo de banda entre Notebook e Raspberry.
- Confiabilidade com ACK, CRC e retransmissão.

## Conclusão
O Communication Core materializa o princípio central do ORION OS: módulos independentes conversando apenas por mensagens confiáveis e versionadas, na cadeia Notebook → Raspberry → Arduino.
