# ORION OS — System Engineering Specification (SES)
## Capítulo 19 — Testes e Validação
Versão 1.0 — Especificação Oficial
## 1. Objetivo
Definir a estratégia oficial de testes do ORION OS, cobrindo testes unitários, de integração e de campo, além dos critérios objetivos de aprovação para a versão 1.0.
## 2. Princípios
- Nenhum módulo é considerado pronto sem testes.
- Serviços críticos exigem testes automatizados (Capítulo 6).
- Todo bug corrigido gera um teste de regressão.
- Testes de campo só ocorrem após aprovação em bancada.
## 3. Testes Unitários
Escopo por módulo:
- Kernel: Event Bus, Service Registry, Watchdog, Configuration Manager.
- Communication Core: enquadramento, CRC, retransmissão, heartbeat (com portas seriais simuladas).
- Memory/Database Core: API de memória, migrações, integridade, retenção.
- Navigation Core: planejamento de segmentos, desvio, odometria (simulada).
- Voice Core: detecção de wake word e pipeline com áudios gravados.
- Vision Core (Notebook): inferência com imagens de referência, cálculo Pan/Tilt.
- Motion Core (Raspberry): fusão de sensores, odometria e planejamento com telemetria simulada.
- Firmware Hardware Core: parsers de mensagem, máquina de estados, validação de parâmetros (testes em host quando possível).
Meta de cobertura: 80% nos serviços críticos.
## 4. Testes de Integração
Executados em bancada com hardware real:
- INT-01: boot completo até system.ready.
- INT-02: comando de voz → missão → movimento → telemetria → interface.
- INT-03: vision.person_detected → FOLLOW com alvo em movimento controlado.
- INT-04: obstáculo durante MOVE → parada reativa → replanejamento.
- INT-05: desconexão do cabo USB Arduino↔Raspberry durante missão → SAFE_MODE → reconexão → ressincronização.
- INT-06: desconexão do cabo Ethernet Notebook↔Raspberry → HOLD seguro no Raspberry + modo degradado no Notebook → recuperação.
- INT-07: corrupção simulada do banco → restauração de backup.
- INT-08: troca de perfil de configuração a quente.
- INT-09: 24 horas em IDLE sem vazamento de memória (métricas do Diagnostics Core).
Cada cenário possui roteiro escrito, resultado esperado e registro em banco.
## 5. Testes de Campo
Ambiente residencial real, com supervisão:
- CAMPO-01: patrulha completa em rota conhecida (10 ciclos sem intervenção).
- CAMPO-02: seguimento de pessoa autorizada por 10 minutos, incluindo curvas e paradas.
- CAMPO-03: operação em baixa luminosidade com acionamento automático da lanterna.
- CAMPO-04: conversação por voz a 1 m e a 3 m, com ruído doméstico normal.
- CAMPO-05: convivência com crianças/animais sob perfil SAFE (avaliação de segurança).
- CAMPO-06: 7 dias de operação diária com relatório de saúde sem eventos críticos não tratados.
## 6. Critérios de Aprovação da Versão 1.0
- 100% dos testes de integração INT-01 a INT-08 aprovados.
- INT-09 sem degradação de memória superior a 5%.
- Testes de campo CAMPO-01 a CAMPO-04 aprovados em 3 execuções consecutivas.
- Nenhuma falha de segurança de Camada 1 durante todo o ciclo.
- Taxa de reconhecimento da wake word ≥ 90% a 1 m.
- Latência voz→resposta ≤ 5 s em conversa simples.
- Documentação e logs completos de todas as execuções.
## 7. Ferramentas e Infraestrutura
- Framework de testes da linguagem de cada módulo (pytest para Python; framework compatível para firmware).
- Simuladores de porta serial para CI local.
- Massa de dados de referência (imagens, áudios, mapas) versionada com o projeto.
- Relatórios de teste gravados na tabela diagnosticos.
## 8. EDR-0016
Decisão: condicionar o release 1.0 a critérios objetivos e mensuráveis, com testes de campo obrigatórios após bancada.
Motivação:
- Robô doméstico convive com pessoas; segurança exige evidência, não intenção.
- Critérios objetivos eliminam ambiguidade sobre "pronto".
- Regressões detectadas antes do campo.
## Conclusão
A estratégia de testes garante que cada promessa da especificação seja verificada na prática, transformando o ORION OS 1.0 em um sistema comprovadamente seguro e confiável antes de operar em ambiente familiar.
