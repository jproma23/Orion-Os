# ORION OS — System Engineering Specification (SES)
## Capítulo 20 — Roadmap Oficial
Versão 1.0 — Especificação Oficial
## 1. Objetivo
Estabelecer o plano oficial de evolução do ORION OS, definindo o escopo de cada versão maior e as regras que garantem crescimento sem quebra de arquitetura.
## 2. Regras de Evolução
- Nenhuma versão futura pode violar os princípios dos Capítulos 1 e 2.
- Compatibilidade do protocolo preservada por versionamento (Capítulo 5).
- Novas capacidades entram como módulos/plugins, nunca como alterações no núcleo.
- Cada versão maior possui seus próprios critérios de aprovação (modelo do Capítulo 19).
## 3. ORION OS 1.0 — Fundação (escopo congelado)
- Kernel completo: Event Bus, Service Registry, HAL, Watchdog.
- Mission, Vision, Motion, Voice, Memory, Navigation, Display, Communication, Database, Diagnostics e Configuration Cores operacionais.
- Conversação por voz offline (wake word "Fofão").
- Reconhecimento de pessoas e objetos.
- Patrulha autônoma e seguimento de pessoa autorizada.
- Lanterna automática por luminosidade.
- Segurança em três camadas e modos degradados.
- Aprovação conforme critérios do Capítulo 19.
## 4. ORION OS 2.0 — Percepção Avançada
- LiDAR integrado via nova HAL de percepção.
- SLAM: mapeamento e localização simultâneos.
- Navegação por mapa persistente (ambientes nomeados → coordenadas).
- Reconhecimento facial com identificação individual.
- Otimizações do enlace Ethernet Notebook ↔ Raspberry (já padrão desde o EDR-0018).
- Fusão de sensores (radar + LiDAR + IMU).
- Melhorias de voz: beamforming com múltiplos microfones.
## 5. ORION OS 3.0 — Manipulação e Casa Conectada
- Braço robótico como novo core (Manipulation Core) via Plugin Manager.
- Integração com automação residencial (luzes, tomadas, sensores da casa).
- Câmeras IP como fontes adicionais do Vision Core.
- GPS para áreas externas.
- Multi-robô: dois ou mais ORION X compartilhando o mesmo Mission Core lógico.
- Rotinas proativas (aprendizado de hábitos da família com consentimento).
## 6. Evolução Contínua (todas as versões)
- Atualização de modelos de IA (Ollama) sem mudança de arquitetura.
- Novos idiomas de voz.
- Otimizações de energia.
- Melhorias de interface e avatar.
- Expansão da base de conhecimento local.
## 7. Critérios para Iniciar uma Nova Versão
1. Versão anterior aprovada e estável por no mínimo 60 dias de operação.
2. Backlog de correções críticas zerado.
3. EDRs aprovados para cada nova capacidade.
4. Especificação SES atualizada antes do código.
## 8. EDR-0017
Decisão: evolução exclusivamente incremental, com o núcleo 1.0 congelado como fundação permanente da plataforma.
Motivação:
- Proteção do investimento em arquitetura.
- Redução de risco em um sistema que convive com uma família.
- Cada versão entrega valor completo e testável.
## Conclusão
Este roadmap encerra a especificação inicial do ORION OS. A plataforma nasce completa em sua fundação (1.0) e cresce por composição: percepção avançada (2.0), manipulação e casa conectada (3.0), sempre sob os mesmos princípios de modularidade, segurança e independência de nuvem que definem o projeto desde o primeiro capítulo.
