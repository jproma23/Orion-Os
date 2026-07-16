# ORION OS — System Engineering Specification (SES)
## Capítulo 18 — Segurança Operacional
Versão 1.0 — Especificação Oficial
## 1. Objetivo
Definir as regras de segurança física e operacional do ORION X: comportamento fail-safe, resposta a perda de comunicação, tratamento de obstáculos, recuperação de falhas e modos degradados. A segurança tem prioridade sobre qualquer missão.
## 2. Princípios de Segurança
1. Em dúvida, parar.
2. A camada reativa nunca depende do Notebook.
3. Toda falha deve levar o sistema a um estado conhecido e seguro.
4. Nenhuma recuperação automática pode criar risco novo.
5. Todo evento de segurança é registrado e reportado.
## 3. Hierarquia Fail-safe
Camada 1 — Reativa (Arduino, autônoma):
- Parada imediata por obstáculo abaixo da distância mínima.
- Parada por inclinação ou impacto (IMU).
- Timeout de missão sem progresso → STOP.
- Comando inválido → NACK + estado preservado.
Camada 2 — Supervisão (Kernel/Diagnostics):
- Watchdog de módulos e enlaces.
- Limiares críticos de recursos.
Camada 3 — Estratégica (Mission Core):
- Rejeição de missões conflitantes ou perigosas.
- Decisão de abortar, replanejar ou aguardar.
## 4. Perda de Comunicação
Arduino sem heartbeat do Raspberry (3 intervalos):
1. Concluir ou abortar com segurança a missão atual.
2. Entrar em SAFE_MODE (motores parados, sensores ativos, lanterna conforme último estado seguro).
3. Aguardar reconexão; ao reconectar, reportar estado completo antes de aceitar novas missões.
Raspberry sem comunicação com o Notebook:
- Motion Core marcado DEGRADED; missões em curso são pausadas com segurança (HOLD) e FOLLOW é suspenso; a camada reativa do Arduino permanece ativa.
Notebook perde ambos:
- Sistema em modo diagnóstico; interface exibe alerta; tentativas contínuas de reconexão.
## 5. Tratamento de Obstáculos
- Distância crítica frontal/traseira → parada reativa imediata (Camada 1) + evento motion.obstacle_*.
- Obstáculo persistente → Navigation Core tenta rota alternativa (máximo 3 tentativas).
- Sem alternativa → missão pausada, aviso por voz e interface, aguardando decisão.
- Obstáculo móvel aproximando-se → parada preventiva e reavaliação.
## 6. Modos Degradados
- SEM_VISÃO: navegação apenas por radar; velocidade reduzida a 50%; FOLLOW desabilitado.
- SEM_VOZ: operação por interface gráfica.
- SEM_MOTION: robô estático; conversação, visão e diagnóstico permanecem ativos.
- SEM_IA: comandos diretos apenas (mover, parar, status); sem planejamento por linguagem natural.
- MEMÓRIA_REDUZIDA: banco reconstruído; funções que exigem histórico ficam limitadas.
Cada modo degradado é anunciado por voz e exibido na interface.
## 7. Recuperação
- Ordem de recuperação: comunicação → módulo → missão.
- Missões interrompidas nunca retomam automaticamente movimento; exigem confirmação do Mission Core.
- Após SAFE_MODE, é obrigatório SCAN_FRONT antes de qualquer movimento.
- Três falhas do mesmo tipo em 10 minutos → função em quarentena até intervenção.
## 8. Segurança com Pessoas
- Velocidade reduzida quando pessoa detectada no raio configurado.
- Distância mínima de aproximação em FOLLOW.
- Comando de voz "Fofão, pare" tem prioridade máxima e efeito imediato.
- Crianças detectadas (configurável) → perfil SAFE automático.
## 9. Eventos de Segurança
safety.emergency_stop
safety.safe_mode_entered
safety.safe_mode_exited
safety.comm_lost
safety.degraded_mode
safety.recovered
## 10. EDR-0015
Decisão: implementar segurança em três camadas, com a camada reativa residente no Arduino e independente do restante do sistema.
Motivação:
- Proteção garantida mesmo com falha total do software de alto nível.
- Tempo de reação determinístico.
- Conformidade com o princípio "em dúvida, parar".
## Conclusão
A segurança operacional do ORION X não é um módulo, mas uma propriedade da arquitetura: cada camada protege as demais, e nenhuma falha isolada pode resultar em movimento sem controle.
