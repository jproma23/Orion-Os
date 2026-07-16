# ORION OS — System Engineering Specification (SES)

## Capítulo 3 — Arquitetura Física Completa

**Versão:** 1.1 (alinhada ao TCC — ver EDR-0018)

## 1. Objetivo

Definir a arquitetura física oficial do ORION OS, estabelecendo responsabilidades, barramentos, comunicação e fluxo operacional entre Notebook (Mission Core), Raspberry Pi (Motion Core) e Arduino Mega (Hardware Core).

## 2. Filosofia da Arquitetura

O ORION OS utiliza processamento distribuído em cadeia. Cada computador executa apenas as funções nas quais possui melhor desempenho. O Notebook pensa, o Raspberry navega e coordena o hardware, e o Arduino executa e monitora o hardware. O objetivo é maximizar desempenho, modularidade, confiabilidade e facilidade de evolução, mantendo independência de conexão com a Internet.

## 3. Mission Core (Notebook)

Responsável por IA (Ollama), visão computacional (OpenCV/YOLO), reconhecimento facial, Whisper, Piper, planejamento de missão, avatar na tela integrada, reprodução multimídia e gerenciamento dos demais módulos. Comunica-se com o Raspberry Pi via Ethernet.

## 4. Motion Core (Raspberry Pi)

Atua como núcleo de movimento e de dados: navegação, fusão de sensores, planejamento de trajetória e comunicação, além de hospedar a memória, o aprendizado e o banco SQLite no SSD de 500 GB e servir a interface web. Recebe missões de alto nível do Notebook, converte os dados físicos do Arduino em informações de navegação e as combina com o mapa produzido pelo Notebook. É o único módulo que conversa com o Arduino.

## 5. Hardware Core (Arduino Mega)

Controla dois TB6600, dois motores NEMA17, servos, MPU6050, radar ultrassônico frontal de 180° com servo, ultrassônico traseiro, encoders, temperatura/umidade e lanterna LED. Executa comandos recebidos do Raspberry Pi e envia dados físicos periodicamente.

## 6. Fluxo de Inicialização

1. Notebook inicia o ORION OS.
2. Detecta o Raspberry (Ethernet).
3. O Raspberry detecta o Arduino (USB Serial).
4. Inicializa IA e Vision Core no Notebook.
5. Inicializa Motion Core no Raspberry.
6. Inicializa Hardware Core no Arduino.
7. Executa autotestes (e autocalibração na primeira inicialização — Cap 12).
8. Entra em modo operacional.

## 7. Fluxo de Dados

O Arduino envia dados físicos (ultrassom, MPU, estado dos motores e sensores). O Raspberry converte esses dados em informações de navegação e combina essas informações com o mapa produzido pelo Notebook. O Notebook transforma essas informações em decisões de alto nível e novas missões.

Visão (Notebook) → IA → Planejamento → Missão → Raspberry → Comandos → Arduino → Movimento → Telemetria → Raspberry → Notebook → Banco de Dados → Interface.

## 8. Comunicação

- Notebook ↔ Raspberry: **Ethernet (TCP)** para missões, eventos e sincronização.
- Raspberry ↔ Arduino: **USB Serial** para comandos, telemetria e eventos.
- Notebook ↔ Arduino: **não existe enlace direto**.
- Opcional: conexão USB direta Notebook ↔ Raspberry para manutenção e diagnóstico.

## 9. Estados do Sistema

BOOT, SELF_TEST, IDLE, PATROL, FOLLOW, INTERACTION, ALERT, DIAGNOSTIC, SHUTDOWN.

## 10. Regras Arquiteturais

O Notebook nunca acessa sensores de movimento diretamente; utiliza o Raspberry, que utiliza o Arduino. O Raspberry nunca executa IA generativa nem visão computacional pesada. O Arduino nunca executa IA nem navegação; toda a lógica de controle dos motores permanece encapsulada nele. Todos os módulos devem registrar logs e suportar recuperação após falhas.

## Diagrama Conceitual

                 ORION OS

        +----------------------+
        |   Notebook           |
        |   Mission Core       |
        +----------+-----------+
                   |
              Ethernet (TCP)
                   |
        +----------+-----------+
        |   Raspberry Pi       |
        |   Motion Core        |
        +----------+-----------+
                   |
               USB Serial
                   |
        +----------+-----------+
        |   Arduino Mega       |
        |   Hardware Core      |
        +----------------------+

## Conclusão

Esta arquitetura distribui inteligência, navegação e controle de hardware em camadas independentes, servindo como base para todos os capítulos seguintes.
