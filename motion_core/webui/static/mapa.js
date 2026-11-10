// Jarvis — Mapa do LiDAR (refeito em 2026-11-01). Consumidor puro via SSE: só
// desenha o que o servidor manda.
//
// POR QUE FOI REFEITO. O dono: "o mapa é complicado, não mostra os pontos nem
// o que está livre, e tem que marcar a frente do robô". O mapa antigo desenhava
// 12 fatias de 30° (a menor distância de cada uma), descartava os 360 raios que
// o LiDAR já mandava, punha a FRENTE na divisa entre duas fatias, usava escala
// de 6 m (a cômoda a 30 cm virava um risco no centro) e o robô era um triângulo
// de 7 px.
//
// O QUE ELE MOSTRA AGORA, com os `raios_cm` de cada `lidar.scan` (o dono
// pediu "mapeamento por pontos, com a distância de cada ponto ao robô"):
//   - UM PONTO POR GRAU, colorido pela distância (vermelho = dentro da trava);
//   - A DISTÂNCIA ESCRITA ao lado do ponto a cada 10 graus, e de QUALQUER grau
//     passando o mouse (ângulo a partir da frente + cm até o LiDAR);
//   - SEM ECO em cinza no aro: ali o LiDAR não mediu - pode ser vidro, longe
//     demais ou PERTO DEMAIS (abaixo de 5 cm, o lidar.min_distancia_mm 50).
//     Nunca é "livre";
//   - O ROBÔ EM TAMANHO REAL (39 x 30 cm, LiDAR 8 cm atrás da ponta da frente),
//     com a FRENTE (lado da câmera) para cima;
//   - AS LINHAS DA TRAVA: 50 cm à frente e atrás, no corredor da largura do
//     corpo - o que a trava do LiDAR usa para parar as rodas;
//   - o espaço livre em cm nos quatro lados, pela mesma conta de corredor da
//     trava e do desvio (não é a menor distância de um cone).
//
// COMO O ROBÔ SABE ONDE É A FRENTE: o LiDAR mede em relação à marca dele, não
// à do robô. O `lidar.angulo_frente_graus` (19 desde 01/11, medido com parede a
// 30 cm e confirmado pelo dono; o 345 de 31/10 era a borda da faixa sem eco)
// gira toda leitura antes de publicar, e daí em diante 90° = frente. Se o LiDAR for remontado, o teste da caixa
// (EDR-0038, passo 1b) diz se a frente desta tela ainda é a frente de verdade.
//
// Convenção de ângulo: 0° = esquerda, 90° = frente (para cima), 180° = direita,
// 270° = trás.

const $ = (id) => document.getElementById(id);
const canvas = $('radar');
const ctx = canvas.getContext('2d');
const statusEl = $('radar-status');
const conexaoEl = $('conexao');

// Medidas do corpo (orion.yaml: chassi, 31/10, fita métrica). O LiDAR é a
// origem do mapa; o corpo é desenhado em volta dele.
const CORPO_LARGURA_CM = 39;
const CORPO_FRENTE_CM = 8;   // da cabeça do LiDAR até a ponta da frente
const CORPO_TRAS_CM = 22;    // 30 cm de profundidade - 8 à frente
// Os números da trava (orion.yaml: motion.trava_lidar, EDR-0037).
const TRAVA_CM = 50;
const MEIA_CORREDOR_CM = 25;

const COR_PONTO = '#4fd1c5';
const COR_ATENCAO = '#f0ad4e';
const COR_ALERTA = '#e85d4a';
const COR_SEM_ECO = 'rgba(138, 147, 166, 0.35)';
const COR_TEXTO = '#e7ecf3';
const COR_FRACA = 'rgba(138, 147, 166, 0.75)';

let alcanceCm = 200;       // zoom: 100, 200 ou 600
let rumoAtual = null;
let rumoConfiavel = false;
let ultimoScan = null;     // {raios_cm, setores, pontos, ...}

const CENTRO_X = canvas.width / 2;
const CENTRO_Y = canvas.height / 2;
const RAIO_MAX_PX = Math.min(CENTRO_X, CENTRO_Y) - 30;

function rad(graus) {
  return (graus * Math.PI) / 180;
}

function escala() {
  return RAIO_MAX_PX / alcanceCm; // px por cm
}

// Ângulo do projeto + distância -> pixel. 90° vai para cima, 0° para a esquerda.
function paraXY(anguloGraus, distanciaCm) {
  const r = Math.min(distanciaCm, alcanceCm) * escala();
  const a = rad(anguloGraus);
  return [CENTRO_X - r * Math.cos(a), CENTRO_Y - r * Math.sin(a)];
}

// Coordenada no referencial do robô (x = para a direita, y = para a frente,
// em cm, a partir do LiDAR) -> pixel.
function paraTela(xCm, yCm) {
  return [CENTRO_X + xCm * escala(), CENTRO_Y - yCm * escala()];
}

function corDaDistancia(distanciaCm) {
  if (distanciaCm < TRAVA_CM) return COR_ALERTA;
  if (distanciaCm < 2 * TRAVA_CM) return COR_ATENCAO;
  return COR_PONTO;
}

function desenharGrade() {
  ctx.lineWidth = 1;
  ctx.font = '11px monospace';
  const passo = alcanceCm <= 100 ? 25 : alcanceCm <= 200 ? 50 : 100;
  for (let r = passo; r <= alcanceCm; r += passo) {
    ctx.strokeStyle = 'rgba(79, 209, 197, 0.12)';
    ctx.beginPath();
    ctx.arc(CENTRO_X, CENTRO_Y, r * escala(), 0, 2 * Math.PI);
    ctx.stroke();
    ctx.fillStyle = 'rgba(138, 147, 166, 0.6)';
    // rotulo na diagonal de baixo-direita: a frente e o eixo vertical ficam limpos
    ctx.fillText(`${r} cm`, CENTRO_X + r * escala() * 0.71 + 3, CENTRO_Y + r * escala() * 0.71 + 12);
  }
}

//: De quantos em quantos graus a distância vai escrita ao lado do ponto.
const ROTULO_A_CADA_GRAUS = 10;

function desenharPontos(raios) {
  // sem eco: risquinho cinza no aro - ali o LiDAR não mediu, não é "livre"
  ctx.strokeStyle = COR_SEM_ECO;
  ctx.lineWidth = 3;
  for (let g = 0; g < 360; g += 1) {
    if (raios[g] !== null && raios[g] !== undefined) continue;
    const [x1, y1] = paraXY(g, alcanceCm * 0.94);
    const [x2, y2] = paraXY(g, alcanceCm);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  }

  // um ponto por grau; o que passa do alcance do zoom fica na borda, menor
  for (let g = 0; g < 360; g += 1) {
    const d = raios[g];
    if (d === null || d === undefined) continue;
    const [x, y] = paraXY(g, d);
    ctx.beginPath();
    ctx.arc(x, y, d > alcanceCm ? 1.5 : 2.8, 0, 2 * Math.PI);
    ctx.fillStyle = corDaDistancia(d);
    ctx.fill();
  }

  // a distância de cada ponto, escrita a cada ROTULO_A_CADA_GRAUS
  ctx.font = '10px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (let g = 0; g < 360; g += ROTULO_A_CADA_GRAUS) {
    const d = raios[g];
    if (d === null || d === undefined) continue;
    const longe = d > alcanceCm;
    // o número fica 14 px para fora do ponto, na mesma direção
    const raioPx = Math.min(d, alcanceCm) * escala() + 14;
    const a = rad(g);
    const x = CENTRO_X - raioPx * Math.cos(a);
    const y = CENTRO_Y - raioPx * Math.sin(a);
    ctx.fillStyle = longe ? COR_FRACA : corDaDistancia(d);
    ctx.fillText(longe ? `>${Math.round(alcanceCm)}` : `${Math.round(d)}`, x, y);
  }
  ctx.textAlign = 'start';
  ctx.textBaseline = 'alphabetic';
}

// Ângulo do projeto -> texto a partir da FRENTE ("30° esq", "frente", "trás").
function anguloDaFrente(g) {
  let delta = g - 90; // positivo = para a direita
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  if (Math.abs(delta) < 1) return 'frente';
  if (Math.abs(Math.abs(delta) - 180) < 1) return 'trás';
  return `${Math.abs(Math.round(delta))}° ${delta > 0 ? 'dir' : 'esq'}`;
}

// Mouse em cima do mapa: o grau sob o cursor e a distância medida nele.
let mouse = null;

function desenharLeituraDoMouse(raios) {
  if (!mouse || !raios) return;
  const dx = CENTRO_X - mouse.x;
  const dy = CENTRO_Y - mouse.y;
  if (Math.hypot(dx, dy) < 8) return;
  const g = (Math.round((Math.atan2(dy, dx) * 180) / Math.PI) + 360) % 360;
  const d = raios[g];
  const texto = `${anguloDaFrente(g)} · ${d === null || d === undefined ? 'sem eco' : `${Math.round(d)} cm`}`;

  // linha do LiDAR até o ponto daquele grau
  if (d !== null && d !== undefined) {
    const [px, py] = paraXY(g, d);
    ctx.strokeStyle = 'rgba(231, 236, 243, 0.5)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(CENTRO_X, CENTRO_Y);
    ctx.lineTo(px, py);
    ctx.stroke();
  }
  ctx.font = 'bold 13px monospace';
  const largura = ctx.measureText(texto).width + 12;
  const x = Math.min(mouse.x + 12, canvas.width - largura - 4);
  const y = Math.max(mouse.y - 26, 4);
  ctx.fillStyle = 'rgba(6, 8, 16, 0.9)';
  ctx.fillRect(x, y, largura, 22);
  ctx.fillStyle = COR_TEXTO;
  ctx.fillText(texto, x + 6, y + 15);
}

function ligarMouse() {
  canvas.addEventListener('mousemove', (evento) => {
    const caixa = canvas.getBoundingClientRect();
    mouse = {
      x: ((evento.clientX - caixa.left) * canvas.width) / caixa.width,
      y: ((evento.clientY - caixa.top) * canvas.height) / caixa.height,
    };
    redesenhar();
  });
  canvas.addEventListener('mouseleave', () => {
    mouse = null;
    redesenhar();
  });
}

// O corredor da trava: retângulos da largura do corpo, à frente e atrás, até
// 50 cm do LiDAR. Ponto dentro deles = a trava para as rodas.
function desenharTrava() {
  ctx.setLineDash([6, 5]);
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = 'rgba(232, 93, 74, 0.75)';
  for (const sinal of [1, -1]) {
    const [x1, y1] = paraTela(-MEIA_CORREDOR_CM, 0);
    const [x2, y2] = paraTela(MEIA_CORREDOR_CM, sinal * TRAVA_CM);
    ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
  }
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(232, 93, 74, 0.9)';
  ctx.font = '11px monospace';
  ctx.textAlign = 'center';
  const [xf, yf] = paraTela(0, TRAVA_CM);
  ctx.fillText(`trava ${TRAVA_CM} cm`, xf, yf - 5);
  ctx.textAlign = 'start';
}

function desenharRobo() {
  const [x1, y1] = paraTela(-CORPO_LARGURA_CM / 2, CORPO_FRENTE_CM);
  const [x2, y2] = paraTela(CORPO_LARGURA_CM / 2, -CORPO_TRAS_CM);
  ctx.fillStyle = 'rgba(231, 236, 243, 0.85)';
  ctx.fillRect(x1, y1, x2 - x1, y2 - y1);

  // seta da frente, saindo da ponta do corpo
  const [xa, ya] = paraTela(0, CORPO_FRENTE_CM);
  ctx.fillStyle = COR_ALERTA;
  ctx.beginPath();
  ctx.moveTo(xa, ya - 14);
  ctx.lineTo(xa - 8, ya);
  ctx.lineTo(xa + 8, ya);
  ctx.closePath();
  ctx.fill();

  // o LiDAR (origem das distâncias)
  ctx.fillStyle = '#060810';
  ctx.beginPath();
  ctx.arc(CENTRO_X, CENTRO_Y, 3, 0, 2 * Math.PI);
  ctx.fill();
}

function desenharRotulos() {
  ctx.font = 'bold 13px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = COR_TEXTO;
  ctx.fillText('▲ FRENTE (câmera)', CENTRO_X, 14);
  ctx.fillStyle = COR_FRACA;
  ctx.fillText('TRÁS', CENTRO_X, canvas.height - 12);
  ctx.save();
  ctx.translate(12, CENTRO_Y);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('ESQUERDA', 0, 0);
  ctx.restore();
  ctx.save();
  ctx.translate(canvas.width - 12, CENTRO_Y);
  ctx.rotate(Math.PI / 2);
  ctx.fillText('DIREITA', 0, 0);
  ctx.restore();
  ctx.textAlign = 'start';
  ctx.textBaseline = 'alphabetic';
}

function desenharNorte() {
  if (rumoAtual === null) return;
  // onde fica o Norte em relação à frente (frente = 90° na tela)
  const anguloTela = 90 - rumoAtual;
  const [x, y] = paraXY(anguloTela, alcanceCm * 0.86);
  ctx.font = 'bold 12px monospace';
  ctx.textAlign = 'center';
  ctx.fillStyle = rumoConfiavel ? COR_TEXTO : COR_ALERTA;
  ctx.fillText(rumoConfiavel ? 'N' : 'N?', x, y);
  ctx.textAlign = 'start';
}

// A MESMA conta de corredor da trava e do desvio: quanto dá para andar no eixo
// antes de um ponto entrar na faixa da largura do corpo. null = não mediu.
function livreNoCorredor(raios, eixoGraus) {
  let menor = null;
  for (let g = 0; g < 360; g += 1) {
    const d = raios[g];
    if (d === null || d === undefined) continue;
    const desvio = rad(g - eixoGraus);
    const avanco = d * Math.cos(desvio);
    if (avanco > 0 && Math.abs(d * Math.sin(desvio)) < MEIA_CORREDOR_CM) {
      if (menor === null || avanco < menor) menor = avanco;
    }
  }
  return menor;
}

function mostrarLivres(raios) {
  const lados = { 'livre-frente': 90, 'livre-tras': 270, 'livre-esquerda': 0, 'livre-direita': 180 };
  for (const [id, eixo] of Object.entries(lados)) {
    const el = $(id);
    if (!el) continue;
    if (!raios) {
      el.textContent = '—';
      el.className = '';
      continue;
    }
    const livre = livreNoCorredor(raios, eixo);
    el.textContent = livre === null ? 'sem medida' : `${Math.round(livre)} cm`;
    el.className = livre !== null && livre < TRAVA_CM ? 'livre--perto' : '';
  }
}

function redesenhar() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  desenharGrade();
  const raios = ultimoScan && Array.isArray(ultimoScan.raios_cm) && ultimoScan.raios_cm.length === 360
    ? ultimoScan.raios_cm
    : null;
  desenharTrava();
  if (raios) {
    desenharPontos(raios);
  }
  desenharRobo();
  desenharNorte();
  desenharRotulos();
  desenharLeituraDoMouse(raios);
  mostrarLivres(raios);

  if (!ultimoScan) {
    statusEl.textContent = 'nenhuma varredura ainda';
  } else if (!raios) {
    statusEl.textContent = 'a varredura chegou sem os 360 raios - LiDAR antigo?';
  } else {
    const medidos = raios.filter((d) => d !== null && d !== undefined).length;
    statusEl.textContent = `LiDAR: ${medidos} de 360 graus medidos · cinza no aro = sem eco (não é livre)`;
  }
}

// Mesma regra do dashboard: sem medida, a página não inventa número.
function renderizarPosicao(posicao) {
  if (!posicao) return;
  const medida = posicao.medida === true;
  const aviso = $('pos-aviso');
  $('pos-x').textContent = medida ? `${posicao.x_m} m` : '—';
  $('pos-y').textContent = medida ? `${posicao.y_m} m` : '—';
  $('pos-orientacao').textContent = medida ? `${posicao.orientacao_graus}°` : '—';
  if (aviso) {
    aviso.textContent = medida ? '' : 'deslocamento não medido';
  }
}

function aplicarTelemetria(t) {
  if (!t) return;
  if ('rumo_graus' in t) rumoAtual = t.rumo_graus;
  if ('rumo_valido' in t) rumoConfiavel = !!t.rumo_valido;
  if (t.bussola_conectada === false) rumoAtual = null;
  redesenhar();
}

function ligarZoom() {
  document.querySelectorAll('[data-alcance]').forEach((botao) => {
    botao.addEventListener('click', () => {
      alcanceCm = Number(botao.dataset.alcance);
      document.querySelectorAll('[data-alcance]').forEach((b) => b.classList.toggle('ativo', b === botao));
      redesenhar();
    });
  });
}

async function carregarEstadoInicial() {
  const corpo = await fetch('/estado').then((r) => r.json());
  $('robot-name').textContent = corpo.estado.sistema.robot_name
    ? `— ${corpo.estado.sistema.robot_name}`
    : '';
  renderizarPosicao(corpo.estado.posicao);
  aplicarTelemetria(corpo.estado.hardware);
  const lidar = await fetch('/api/lidar').then((r) => r.json());
  if (lidar && lidar.raios_cm) {
    ultimoScan = lidar;
    redesenhar();
  }
}

function conectar() {
  const fonte = new EventSource('/eventos');
  fonte.addEventListener('open', () => {
    conexaoEl.textContent = 'conectado';
    conexaoEl.className = 'badge badge--on';
  });
  fonte.addEventListener('error', () => {
    conexaoEl.textContent = 'sem conexão';
    conexaoEl.className = 'badge badge--off';
    fonte.close();
    setTimeout(conectar, 2000);
  });
  fonte.addEventListener('lidar.scan', (evento) => {
    ultimoScan = JSON.parse(evento.data);
    redesenhar();
  });
  fonte.addEventListener('motion.position', (evento) => {
    renderizarPosicao(JSON.parse(evento.data));
  });
  fonte.addEventListener('comm.mensagem.telemetry', (evento) => {
    aplicarTelemetria(JSON.parse(evento.data));
  });
}

ligarZoom();
ligarMouse();
redesenhar();
carregarEstadoInicial();
conectar();
