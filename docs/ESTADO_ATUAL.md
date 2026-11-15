# ORION OS — Estado atual do robô Atom

> **Conferido em 15/11/2026, ~09h30**, com as duas máquinas no ar, no commit
> `86b2b1c`. Vale o que está aqui **com data**; o que não foi conferido hoje
> está marcado como *último conhecido*. O histórico completo, com o porquê de
> cada mudança, está em [`journal.md`](journal.md). O
> [`ESTADO_DOS_MODULOS.md`](ESTADO_DOS_MODULOS.md) está **vencido** (de julho).

## 1. Quem é o robô

**Atom** (até 14/11 se chamava Jarvis, antes Fofão). Sistema: **ORION OS**.
Robô doméstico de rodas que conversa, vê, ouve, mapeia, segue pessoas e decide
sozinho o que fazer quando ninguém manda. Decisão do dono: os sensores medem e
informam, **quem decide é a IA**; as travas avisam em vez de proibir (exceção:
a folga de 15 cm na frente).

## 2. As máquinas

| Máquina | Papel | Endereço | Conferido 15/11 |
|---|---|---|---|
| **Raspberry Pi 4** (Debian 13, 3,7 GB) | Motion Core: corpo, LiDAR, navegação (Nav2/SLAM), webui, banco, IA estratégica | `10.20.20.110` (DHCP, muda) · Tailscale `100.112.10.2` / `fofao-pi` | `orion-motion` ativo, 42,8 °C, 466 MB usados |
| **Notebook** "Joao" (Debian, 7,6 GB, sem bateria própria) | Visão (YOLO + rostos), voz (Whisper `base` + Piper), conversa com a IA (Gemini) | `10.20.20.195` (alcançado pelo Pi) | `orion-avatar` ativo, roda de `~/orion-jarvis`, 2,3 GB usados |
| **ESP32 do corpo** (CP2102, `/dev/ttyUSB1` no Pi) | Rodas, encoder, bússola, servos do pan/tilt, relé dos motores | USB no Pi | telemetria viva |
| **ESP32 dos ouvidos** (2 mics I2S) | Microfone | Wi-Fi, TCP 9100 (`ouvidos.local`) | conectado |
| **LiDAR STL-50B2** (CH340, `/dev/ttyUSB0` no Pi) | 360° num plano a 45 cm do chão | USB no Pi | válido, 12/12 setores |
| **Câmera DV20** (Jieli, pan/tilt) | Visão principal | USB no Notebook | volta após power cycle; ver §5 |
| Câmera Sunplus | Reserva, sem uso | USB no Notebook, `/dev/video2` | 10/10 quadros |

O Windows (`DESKTOP-GIH7D11`) é **só terminal SSH e backup** (`C:\Users\jproma\ORION_backup`).
Serviços no Pi: `systemctl --user` (`orion-motion`; `orion-mission` e `orion-camera-stream` desligados de propósito).

## 3. O código

- **Fonte de verdade:** `~/orion-os` no Pi, branch `master`.
- **Notebook:** `~/orion-jarvis`, branch `pi-master`, remoto `pi`. Deploy = `git fetch pi && git merge --ff-only pi/master` + `systemctl --user restart orion-avatar`. **Nunca scp de arquivo solto.**
- **Pi e Notebook no mesmo commit:** `86b2b1c` (15/11).
- **GitHub:** *último conhecido* `636ef38` (14/11). O Pi não tem chave do GitHub; a subida é pelo backup do Windows.
- **Testes:** rodar só os arquivos do que mudou com o robô no ar (a suíte inteira travou o Notebook em 08/11). Hoje: 322 passando nas duas máquinas, nos arquivos afetados.

## 4. O que funciona (com prova)

| Capacidade | Prova |
|---|---|
| Conversa por voz e por chat (webui) | log de 15/11; respostas conferidas pelo `/api/conversa/mensagem` |
| A conversa sabe o espaço em volta (LiDAR) | 15/11: LiDAR 61,5/89,9/62,2 cm → "62 na frente, 90 à esquerda, 63 à direita" |
| **Autodiagnóstico** (câmera, mics, Pi, corpo, LiDAR, bússola) | 15/11: listou os 6; com falha, diz "desde as HH:MM" (só testado em teste) |
| Ações faladas (andar, girar, ré, cabeça, recado, busca, me siga, mapear) | catálogo fechado `#acao:`; "me siga" recusa com a câmera cega |
| Folga de frente: o avanço para a 15 cm da parede | `1fad862` (15/11) |
| Seguir pessoa (FOLLOW) | 15/11 09:08: seguiu o Kamal sozinho pelo LiDAR |
| Iniciativa própria (maestro + IA estratégica a cada 10 min) | 15/11 09:08: decidiu seguir sem ninguém pedir |
| **Curiosidade e companhia** (impulsos: solidão, tédio, proteção, corpo) | ligados em 15/11 (); ao vivo 09:32: "obstáculo a 18 cm [forte]". Silêncio 22h–7h |
| Mapear a casa (explorador + Nav2) | 14/11 17:44, de ponta a ponta |
| Reconhecer rostos | 3 cadastrados (João Paulo, Kamal, Marah); 15/11: "vi Kamal", "vi João Paulo" |
| A IA vê a foto quando a pergunta pede olhar | `228f96e` (14/11) |
| Diário do dia, recados, aulas no quadro, Wikipédia | *último conhecido* (ver journal) |
| Experiências gravadas | 338 no banco; o FOLLOW consulta as dele antes de decidir |

## 5. O que está quebrado ou aberto

| Item | Estado |
|---|---|
| **DV20 trava sem mandar quadros** | Aconteceu 14/11 17:40 → 15/11 ~08:50. Reset de USB não resolve; só o power cycle. Agora pelo menos é **detectado** (ERRO + autodiagnóstico). Causa não achada. |
| A conversa não lê as experiências | o "avançar falhou" foi gravado 17x e a conversa nunca viu. **Próximo passo.** |
| Aviso **falado** quando um sentido cai | ainda não; precisa de regra de horário (não falar de madrugada) |
| Voz da família | 0 vozes carregadas: nenhum código grava embedding de voz |
| Mapa | `casa.pgm` de 14/11 com as mesmas 1741 células do de 31/10 (suspeita de SLAM recarregando mapa velho); falta nomear lugares ("vai pra cozinha") |
| Frente cega | parede rasante / sem eco não é coberta pela folga de 15 cm |
| Webui sem senha | desde 01/11; `/desligar` de fora responde 200 (CSRF fechado) |
| Cota do Gemini | 500/dia; o acervo agora gasta 1 chamada por pergunta que antes saía de graça |
| Pi em DHCP | o IP muda; usar o Tailscale `100.112.10.2` |

## 6. Plano combinado com o dono (15/11)

Direção: **"C puxando para B"**, iniciativa própria só em cima de sentido verificado.

1. ~~Autodiagnóstico que conversa~~ — **feito** (`3b33201`, `6c15d62`).
2. Ler as experiências antes de repetir o que falhou (na conversa e nas decisões).
3. Iniciativa com motivo: puxar assunto ou agir só por um fato observado.

Fila de capacidades (14/11): estranho se identifica (nome → foto → cadastro → avisa o dono) · "me encontre" · tirar foto falado · nomear lugares no mapa · ronda noturna.

## 7. Atualizações de 14 e 15/11

| Commit | O quê |
|---|---|
| `c7214c1` | LiDAR: corte mínimo 70 mm (o suporte virava "parede a 5 cm") |
| `5a64976` | Trava do LiDAR só avisa (decisão do dono) |
| `ed12ea3` | `mapear_casa`: ação falada → explorador de fronteiras + Nav2 |
| `228f96e` | A IA da conversa vê a foto da câmera |
| `b1e18c8` `3760fc2` `79bb699` | Varredura do chão ao andar + detector de chão local com a IA decidindo |
| `5dc1cb7` | Tela apagada desde a subida, acende com a palavra de ativação |
| `7887281` | O HOLD do joystick não mata mais o mapeamento |
| `9d54d73` | Curiosidade: o explorador vai onde há mais desconhecido |
| `3c7b1b3` | O navegador da aula fecha quando o avatar reinicia |
| `bfa2110` `6df0e2c` `9cdb361` | Jarvis → **Atom** |
| `5aff534` | Acento de volta antes do Piper |
| `1fad862` | Folga de frente de 15 cm (dono: "encurtar o passo") |
| `9710b6c` | "para" nos hotwords do Whisper |
| `56f1e39` | Chat: fala de agora fora do diário; me siga recusa com câmera cega; câmera sem quadros vira ERRO; "ré"; prompt do microfone |
| `3b33201` `6c15d62` | **Painel dos sentidos** (autodiagnóstico) |
| `9a250fb` | O acervo vira contexto da IA, não resposta lida crua |
| `6ef2c82` | A IA do Pi não diz mais "Eu sou o Jarvis" |
| `86b2b1c` | **A conversa passa a saber a frente medida** — a causa da batida de 14/11 |
| `683fbd8` `4402c85` | **Impulsos ligados** (órfãos desde 05/08): curiosidade, tédio, busca de companhia, horário de silêncio |
