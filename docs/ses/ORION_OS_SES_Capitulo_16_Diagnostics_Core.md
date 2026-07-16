# ORION OS — System Engineering Specification (SES)
## Capítulo 16 — Diagnostics Core
Versão 1.0 — Especificação Oficial
## 1. Objetivo
O Diagnostics Core monitora continuamente a saúde do ORION OS, executa autotestes, acompanha recursos computacionais e alimenta o Watchdog do Kernel, permitindo detecção precoce de falhas e recuperação automática.
## 2. Autotestes de Boot
Executados na etapa 11 da sequência de boot (Capítulo 6):
- Comunicação: Raspberry respondendo ao Notebook (Ethernet) e Arduino respondendo WHO_ARE_YOU ao Raspberry (Serial).
- Hardware Core: leitura de todos os sensores + micro-movimento de verificação dos motores (opcional, configurável).
- Motion Core: fusão de sensores e odometria coerentes.
- Vision Core (Notebook): captura de frame + inferência de teste.
- Voice Core: captura de áudio + síntese de teste.
- Banco de dados: integrity_check.
- IA: prompt de verificação no Ollama.
Resultado consolidado publicado em diagnostic.selftest_completed; falhas críticas impedem a entrada em modo operacional.
## 3. Monitoramento Contínuo
Métricas coletadas periodicamente:
Notebook:
- CPU (total e por processo crítico: Ollama, Whisper, Vision bridge).
- RAM disponível.
- Temperatura da CPU.
- Espaço em disco.
Raspberry Pi (inclui saúde do SSD e espaço em disco):
- CPU, RAM, temperatura do SoC, FPS do pipeline de visão.
Arduino (via telemetria):
- Temperatura/umidade ambiente, estado dos motores, erros de missão.
Intervalo padrão: 5 s (configurável).
## 4. Limiares e Ações
Cada métrica possui limiares WARNING e CRITICAL no Configuration Core.
Exemplos de ações automáticas:
- CPU do notebook CRITICAL → reduzir FPS do Vision Core e pausar multimídia.
- Temperatura do Raspberry CRITICAL → reduzir frequência do laço de navegação.
- RAM baixa → reiniciar módulo com vazamento identificado.
- Disco baixo → antecipar limpeza de retenção (Capítulo 15).
## 5. Watchdog e Saúde dos Módulos
- Cada módulo envia heartbeat com estado (RUNNING, DEGRADED) e métricas resumidas.
- O Diagnostics Core mantém o quadro de saúde consumido pelo Display Core.
- Ausência de heartbeat → escalonamento do Kernel: reconexão → reinício do módulo → registro → notificação (Capítulo 6).
- Reinícios repetidos (3 em 10 minutos) → módulo em quarentena + modo degradado.
## 6. Diagnóstico Sob Demanda
Comandos disponíveis por voz ou interface:
- "Fofão, autoteste" → executa bateria completa.
- "Fofão, status" → resumo falado + tela de diagnóstico.
- Teste individual por módulo via interface.
## 7. Registro
- Todos os resultados gravados na tabela diagnosticos.
- Falhas críticas geram log nível ERROR com contexto completo.
- Relatório diário de saúde consolidado no banco.
## 8. Eventos Publicados
diagnostic.selftest_completed
diagnostic.metric_warning
diagnostic.metric_critical
diagnostic.module_unhealthy
diagnostic.module_quarantined
diagnostic.report_ready
## 9. EDR-0013
Decisão: centralizar o diagnóstico no Mission Core, com módulos reportando métricas via heartbeat em vez de agentes independentes.
Motivação:
- Visão única de saúde do sistema.
- Ações corretivas coordenadas com o Kernel.
- Simplicidade nos módulos remotos (Arduino e Raspberry apenas reportam).
## Conclusão
O Diagnostics Core dá ao ORION OS a capacidade de se auto-observar e se auto-corrigir, condição essencial para operação autônoma prolongada e para os requisitos de recuperação automática definidos desde o Capítulo 1.
