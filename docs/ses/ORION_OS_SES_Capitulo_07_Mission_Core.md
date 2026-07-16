# ORION OS — System Engineering Specification (SES)

## Capítulo 7 — Mission Core (Notebook)

Versão 1.0 — Especificação Oficial

## 1. Objetivo

O Mission Core é o cérebro do ORION OS. Ele coordena todos os módulos, executa a IA, interpreta eventos, toma decisões e distribui missões aos demais componentes do robô.

## 2. Responsabilidades

• Coordenar todo o sistema
• Executar Ollama
• Executar o Vision Core (OpenCV/YOLO/reconhecimento facial)
• Executar Whisper
• Executar Piper
• Consultar a memória e o banco (hospedados no Raspberry) via API
• Planejar missões
• Coordenar o Raspberry Pi (Motion Core), que coordena o Arduino
• Gerenciar interface gráfica
• Registrar logs e diagnósticos

## 3. Arquitetura Interna

Mission Core
├── AI Manager
├── Mission Planner
├── Memory Manager
├── Database Manager
├── Voice Manager
├── Display Manager
├── Communication Manager
├── Diagnostics Manager
└── Security Manager

## 4. Fluxo de Decisão

1. Receber evento.
2. Consultar contexto e memória.
3. Definir prioridade.
4. Consultar IA quando necessário.
5. Criar plano de ação.
6. Enviar missão ao módulo apropriado.
7. Monitorar execução.
8. Registrar resultado.

## 5. Gerenciamento de Memória

Curto prazo:
• Conversa atual
• Missão atual
• Estado do robô

Longo prazo:
• Pessoas conhecidas
• Ambientes
• Objetos
• Preferências
• Histórico de missões
• Eventos relevantes

## 6. Planejamento de Missões

Toda missão possui:
• Identificador
• Objetivo
• Prioridade
• Pré-condições
• Etapas
• Critérios de sucesso
• Critérios de cancelamento
• Resultado

## 7. Comunicação

Recebe eventos do Vision Core (local) e do Motion Core (Raspberry, via Ethernet).
Publica missões.
Atualiza a interface.
Armazena todas as decisões relevantes no banco de dados.

## 8. Interface Gráfica

A tela do notebook representa o estado do robô.
Modos:
• Patrulha
• Conversação
• Diagnóstico
• Mapa
• Reprodução multimídia
• Configuração

## 9. Segurança

Missões conflitantes são rejeitadas.
Eventos críticos têm prioridade máxima.
A perda do Vision Core ou do Motion Core coloca o sistema em modo degradado, preservando os demais serviços.

## 10. EDR-0004

Decisão: centralizar toda a inteligência no Mission Core.
Motivação:
• Simplificar coordenação.
• Facilitar manutenção.
• Permitir evolução independente do Vision Core, Motion Core e Hardware Core.

## Conclusão

O Mission Core é o elemento responsável pela inteligência estratégica do ORION OS. Nenhum módulo toma decisões globais sem sua coordenação.