# EDR-0033 — Provisionamento de rede sem tela nem teclado

**Data:** 13/09/2026
**Status:** Decidido
**Fase:** 3.5

---

## Contexto

Levar o robô a um lugar novo exige informar credenciais de WiFi a um Raspberry Pi sem tela
nem teclado. Hoje isso significa editar arquivo por SSH — impossível justamente quando não
há rede.

## Alternativa considerada e rejeitada

**ESP32 como roteador NAT para o Pi.** O lwIP tem suporte a NAPT e o ESP32 consegue operar
em AP+STA, então tecnicamente funciona.

Rejeitada por quatro motivos:

1. Throughput de poucos Mbps e latência instável, com frames de câmera e chamadas de API
   passando por um gargalo de 240 MHz.
2. Ponto único de falha para toda a internet do robô, permanentemente.
3. Descarta o rádio do Pi 4, que é melhor.
4. **O ESP32 não enxerga 5 GHz.** Se ele fizesse a varredura, a rede 192.168.1.x do
   desenvolvedor nem apareceria na lista.

## Decisão

**O ESP32-S3 é interface de entrada. O Pi continua dono da própria conexão.**

| Quem | Responsabilidade |
|---|---|
| **Pi** | Escanear (único rádio que vê 5 GHz), aplicar com `nmcli`, testar internet real |
| **ESP32-S3** | Subir o AP `ORION-SETUP` (WPA2), servir a página, coletar a senha |
| **UART** | Lista de redes desce, senha sobe, status volta |

**Nenhum tráfego de operação passa pelo ESP32.** Ele transporta texto e sai de cena.

### Fluxo

1. Toque na tela → ESP32-S3 sobe o AP
2. Celular conecta no AP, abre a página
3. A lista exibida **veio do Pi** pela UART — por isso redes de 5 GHz aparecem
4. Usuário escolhe e digita a senha
5. Senha sobe pela UART → Pi aplica com `nmcli`
6. **Pi testa internet de verdade**, buscando um endpoint conhecido
7. Sucesso → AP cai, a tela volta a ser o rosto da LULU

### Regras

**O passo 6 não é opcional.** Rede de hotel ou pousada com portal cativo deixa o Pi associado,
com IP e sem internet. Confiar na associação faz o robô reportar sucesso e a IA falhar em
silêncio.

**Falha no passo 6 não derruba o AP.** A página fica aberta para nova tentativa. Derrubar o
AP antes da confirmação deixa o operador sem nada na mão num lugar estranho.

**AP sempre com WPA2, nunca aberto.**

**A senha nunca é logada** — nem no ESP32, nem no log do Pi, nem no SQLite.

### mDNS

Avahi no Pi publica `fofao.local`, válido em qualquer rede. Isso encerra a caça a IP que já
custou tempo neste projeto (o relógio W699 com IP dinâmico). Anunciar também `_orion._tcp`
com a porta do FastAPI.

### Entrada pela tela como alternativa

A tela de 3,5" é capacitiva e o LVGL tem widget de teclado. Dá para digitar a senha no rosto
do robô, sem celular e sem AP.

Fica como **caminho alternativo, não principal**: senha longa em teclado de 3,5" é sofrido.
O AP com celular é mais confortável no dia a dia.

## Consequências

- Trocar de rede não toca em navegação, motor nem EventBus: os ESP32 falam UART e o ROS2
  roda com `ROS_LOCALHOST_ONLY=1`. Só a API de visão e voz depende disso.
- **Provisionamento é conveniência, não camada estrutural** — coerente com o offline-first.
- Nenhuma fase do plano depende da F3.5. Pode ser adiada sem custo.

## Critério de aceite (Gate 3.5)

- Provisionar numa rede 2,4 GHz e numa 5 GHz (a de 5 GHz prova que o scan veio do Pi)
- Senha errada de propósito: AP continua de pé, tela informa a falha
- Rede sem link: o passo 6 detecta e reporta
- `ping fofao.local` responde das duas redes
- **Desligar o ESP32-S3 com o Pi conectado: a internet continua funcionando** — prova de que
  ele é interface e não infraestrutura
