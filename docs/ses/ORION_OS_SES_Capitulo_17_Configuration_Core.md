# ORION OS — System Engineering Specification (SES)
## Capítulo 17 — Configuration Core
Versão 1.0 — Especificação Oficial
## 1. Objetivo
O Configuration Core centraliza toda a parametrização do ORION OS em uma fonte única, versionada e validada, eliminando valores fixos no código e permitindo ajustar o comportamento do robô sem recompilar módulos.
## 2. Arquivo Único de Configuração
- Formato: YAML (orion.yaml) no Notebook.
- Carregado pelo Configuration Manager na etapa 1 do boot.
- Validado contra esquema oficial (tipos, faixas, obrigatoriedade).
- Valores efetivos espelhados na tabela configuracao para auditoria.
- Configuração inválida → boot abortado com mensagem clara.
## 3. Estrutura por Domínios
system: nome do robô, idioma, fuso, nível de log.
communication: portas, baud rate, timeouts, tentativas, intervalo de heartbeat.
motion: velocidade máxima, aceleração, passos por metro, distâncias de segurança, timeout de missão.
navigation: distância de seguimento, parâmetros de patrulha, limites da IMU.
vision: resolução, FPS, limiar de confiança do YOLO, limites Pan/Tilt.
voice: palavra de ativação, dispositivo preferencial, modelo Whisper, voz do Piper, volume.
ai: modelo Ollama, temperatura, tamanho de contexto, prompt de sistema.
display: modo inicial, brilho, timeout de SLEEP.
diagnostics: intervalos de coleta, limiares WARNING/CRITICAL.
database: caminho, política de backup, retenções.
security: usuários autorizados, ações restritas.
## 4. Perfis
Perfis pré-definidos que sobrepõem o arquivo base:
- HOME — operação normal residencial.
- SILENT — voz reduzida, sem patrulha noturna.
- DEMO — respostas rápidas, movimentos limitados.
- SAFE — velocidades mínimas, distâncias ampliadas.
- DEV — logs verbosos, autotestes estendidos.
Troca de perfil por interface ou voz, com aplicação a quente quando possível.
## 5. Distribuição aos Módulos
- Cada módulo declara os parâmetros que consome.
- No boot, o Kernel entrega a cada módulo apenas sua seção.
- Arduino recebe parâmetros via mensagem CONFIG_SET encaminhada pelo Raspberry (Capítulo 14) e confirma com ACK.
- Raspberry recebe sua seção (navegação e comunicação) na inicialização do Motion Core.
## 6. Atualizações em Tempo de Execução
- Parâmetros marcados como hot-reload aplicam-se imediatamente (ex.: volume, FPS, limiares).
- Parâmetros críticos (portas, modelo de IA) exigem reinício do módulo afetado.
- Toda alteração gera evento config.changed com chave, valor anterior e novo valor.
- Alterações são registradas com autor e timestamp.
## 7. Segurança da Configuração
- Seção security editável apenas por usuário autorizado.
- Backup automático do orion.yaml a cada alteração (últimas 10 versões).
- Rollback por interface: "restaurar configuração anterior".
## 8. Eventos Publicados
config.loaded
config.changed
config.profile_changed
config.validation_error
config.rollback_executed
## 9. EDR-0014
Decisão: adotar arquivo YAML único com esquema validado, perfis sobrepostos e distribuição por seção via Kernel.
Motivação:
- Fonte única de verdade para parametrização.
- Ajuste de campo sem recompilação.
- Auditoria completa de mudanças.
- Perfis simplificam operação por não-técnicos.
## Conclusão
O Configuration Core torna o Fofão ajustável, auditável e seguro de operar, garantindo que todo comportamento configurável do sistema tenha origem única e controlada.
