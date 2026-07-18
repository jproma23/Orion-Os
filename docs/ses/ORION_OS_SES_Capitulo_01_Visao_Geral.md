# ORION OS --- System Engineering Specification (SES)

# Capítulo 1 --- Visão Geral do Projeto

**Versão:** 1.0 (Rascunho Oficial)

## 1. Objetivo

O ORION OS é uma plataforma de robótica modular destinada ao
desenvolvimento de robôs autônomos capazes de perceber, compreender e
interagir com o ambiente de forma inteligente.

O primeiro robô baseado nesta plataforma será denominado **Fofão**.

A filosofia do projeto é separar claramente as responsabilidades entre
os dispositivos físicos:

-   **Notebook (Mission Core):** inteligência artificial, visão
    computacional, reconhecimento facial, planejamento, memória, voz,
    banco de dados, interface e coordenação geral.
-   **Raspberry Pi (Motion Core):** navegação, fusão de sensores,
    planejamento de trajetória e comunicação com o hardware.
-   **Arduino Mega (Hardware Core):** controle em tempo real dos
    motores, servos, sensores e execução das ações físicas.

------------------------------------------------------------------------

## 2. Missão do Projeto

Construir uma plataforma robótica profissional, totalmente modular,
expansível e **offline-first**: toda função essencial opera sem
Internet. Quando houver conexão, recursos adicionais são habilitados
(acesso remoto via Raspberry Pi Connect, notificações, atualizações),
sem nunca se tornarem dependência.

O robô deverá:

-   Conversar naturalmente.
-   Reconhecer pessoas.
-   Aprender ambientes.
-   Patrulhar de forma autônoma.
-   Acompanhar usuários autorizados.
-   Auxiliar a família.
-   Entreter crianças quando solicitado.
-   Evoluir continuamente sem reescrever sua arquitetura.

------------------------------------------------------------------------

## 3. Princípios de Engenharia

Todo desenvolvimento deverá obedecer aos seguintes princípios:

1.  Modularidade.
2.  Responsabilidade única por módulo.
3.  Comunicação por interfaces bem definidas.
4.  Baixo acoplamento.
5.  Alta coesão.
6.  Código documentado.
7.  Testes automatizados sempre que possível.
8.  Segurança operacional.
9.  Recuperação automática de falhas.
10. Evolução sem quebra de compatibilidade.

------------------------------------------------------------------------

## 4. Arquitetura Geral

Mission Core (Notebook) - Ollama - OpenCV - YOLO - Reconhecimento
facial - Whisper - Piper - Banco de dados - Planejamento - Memória -
Interface - Coordenação

Motion Core (Raspberry Pi) - Navegação - Fusão de sensores -
Planejamento de trajetória - Ponte de comunicação com o Arduino

Hardware Core (Arduino Mega) - TB6600 - Motores NEMA17 - Servos -
MPU6050 - Ultrassônico frontal em servo (radar 180°) - Ultrassônico
traseiro - Encoders - Temperatura/Umidade - Lanterna

------------------------------------------------------------------------

## 5. Objetivos da Versão 1.0

-   Estrutura completa do ORION OS.
-   Comunicação Notebook ↔ Raspberry via Ethernet.
-   Comunicação Raspberry ↔ Arduino via USB Serial.
-   Patrulha autônoma.
-   Reconhecimento de pessoas e objetos.
-   Controle por voz.
-   Palavra de ativação: "Fofão".
-   Controle automático da lanterna usando a câmera do notebook como
    estimativa de luminosidade.
-   Avatar na tela do notebook e interface web servida pelo Raspberry
    (dashboard, mapa, diagnóstico), acessível de qualquer dispositivo
    da rede local.

------------------------------------------------------------------------

## 6. Escalabilidade

O ORION OS deverá permitir futuramente:

-   LiDAR.
-   SLAM.
-   GPS.
-   Braço robótico.
-   Câmeras IP.
-   Automação residencial.
-   Novos módulos de IA.
-   Novos sensores sem alteração do núcleo do sistema.

------------------------------------------------------------------------

## 7. Regras para o Desenvolvimento

Nenhuma funcionalidade poderá ser implementada sem respeitar esta
especificação.

Toda decisão arquitetural deverá priorizar:

-   Robustez.
-   Simplicidade.
-   Manutenção.
-   Escalabilidade.
-   Segurança.

Este documento é a referência oficial do projeto ORION OS.

**Fim do Capítulo 1**
