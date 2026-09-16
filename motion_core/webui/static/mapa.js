// Fofão — Mapa polar 360° (Cap 13 s.4). Consumidor puro via SSE: só desenha
// o que o servidor manda.
//
// Mudou de meia-lua para círculo completo em 2026-07-27, para caber o que o
// robô realmente enxerga ao redor, e em 2026-08-16 a fonte dos pontos mudou:
//
//   - ANTES: arco frontal varrido pelo servo do radar (ultrassom) + campo
//     traseiro fixo do HC-SR04 de trás. Sensores removidos junto com a IMU.
//   - AGORA: nuvem de pontos do LiDAR LD06. O lidar varre 360° num único
//     frame, então o mapa é um círculo inteiro de leituras
//     {angulo, distancia_cm, valida} - sem vão cego, sem cone.
//   - ROSA DOS VENTOS no aro externo, girando com o rumo da bússola: mostra
//     onde fica o Norte em relação à frente do robô.
//
// Convenção de ângulo (herdada do paraXY original): 0° = esquerda do robô,
// 90° = frente (para cima na tela), 180° = direita, 270° = trás. O ângulo
// cresce no sentido horário na tela.

const $ = (id) => document.getElementById(id);
const canvas = $('radar');
const ctx = canvas.getContext('2d');
const statusEl = $('radar-status');
const conexaoEl = $('conexao');

//: alcance do mapa. O LD06 enxerga até ~12 m; valores acima são cortados na
//: borda para o mapa não se espremer por causa de um ponto longe demais.
const ALCANCE_MAXIMO_CM = 600;

const CENTRO_X = canvas.width / 2;
const CENTRO_Y = canvas.height / 2;
const RAIO_MAX_PX = Math.min(CENTRO_X, CENTRO_Y) - 34; // folga para as letras da rosa

const COR_ACENTO = '#4fd1c5';
const COR_FRACA = 'rgba(138, 147, 166, 0.7)';
const COR_ALERTA = '#e85d4a';

let rumoAtual = null;      // graus da bússola, ou null
let rumoConfiavel = false; // rumo_valido - sem calibração a rosa não vale
let ultimoMapa = { leituras: [] };
let ultimoScan = null;     // setores do LiDAR LD06 ({inicio_graus, fim_graus, distancia_cm})

function rad(graus) {
  return (graus * Math.PI) / 180;
}

function pontoNoRaio(anguloGraus, raioPx) {
  const a = rad(anguloGraus);
  return [CENTRO_X - raioPx * Math.cos(a), CENTRO_Y - raioPx * Math.sin(a)];
}

function paraXY(anguloGraus, distanciaCm) {
  const distancia = Math.min(distanciaCm, ALCANCE_MAXIMO_CM);
  return pontoNoRaio(anguloGraus, (distancia / ALCANCE_MAXIMO_CM) * RAIO_MAX_PX);
}

// O canvas conta ângulo a partir de outro eixo e no sentido oposto ao nosso -
// esta função traduz o nosso ângulo para o do arc().
function paraArcoCanvas(anguloGraus) {
  return rad(180 - anguloGraus);
}

function desenharGrade() {
  ctx.lineWidth = 1;

  // anéis de alcance a cada 1m, círculos inteiros
  ctx.strokeStyle = 'rgba(79, 209, 197, 0.15)';
  ctx.font = '10px monospace';
  for (let alcance = 100; alcance <= ALCANCE_MAXIMO_CM; alcance += 100) {
    const raioPx = (alcance / ALCANCE_MAXIMO_CM) * RAIO_MAX_PX;
    ctx.beginPath();
    ctx.arc(CENTRO_X, CENTRO_Y, raioPx, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.fillStyle = 'rgba(138, 147, 166, 0.55)';
    ctx.fillText(`${alcance}cm`, CENTRO_X + 4, CENTRO_Y - raioPx - 3);
  }

  // eixos N/S/L/O, para orientar a nuvem do lidar quando a bússola ainda
  // não está calibrada
  ctx.strokeStyle = 'rgba(79, 209, 197, 0.16)';
  for (const angulo of [0, 45, 90, 135, 180, 225, 270, 315]) {
    const [x, y] = pontoNoRaio(angulo, RAIO_MAX_PX);
    ctx.beginPath();
    ctx.moveTo(CENTRO_X, CENTRO_Y);
    ctx.lineTo(x, y);
    ctx.stroke();
  }
}

function desenharRosaDosVentos() {
  const letras = ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO'];
  const raioLetras = RAIO_MAX_PX + 20;

  ctx.font = '11px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  if (rumoAtual === null) {
    // Sem bússola não há Norte para mostrar - melhor não desenhar nada do
    // que desenhar uma rosa parada, que pareceria informação.
    ctx.fillStyle = 'rgba(138, 147, 166, 0.35)';
    ctx.fillText('sem bússola', CENTRO_X, 14);
    ctx.textAlign = 'start';
    ctx.textBaseline = 'alphabetic';
    return;
  }

  letras.forEach((letra, indice) => {
    const rumoDaLetra = indice * 45;
    // frente do robô = 90° na tela, e o ângulo relativo cresce no sentido
    // horário, igual à convenção de rumo (N→L→S→O).
    const anguloTela = 90 + (rumoDaLetra - rumoAtual);
    const [x, y] = pontoNoRaio(anguloTela, raioLetras);
    const norte = indice === 0;
    // Sem calibração a rosa inteira fica em cor de alerta: a direção existe,
    // mas carrega junto o campo dos ímãs dos motores.
    ctx.fillStyle = rumoConfiavel
      ? (norte ? '#e7ecf3' : COR_FRACA)
      : (norte ? COR_ALERTA : 'rgba(232, 93, 74, 0.45)');
    ctx.font = norte ? 'bold 12px monospace' : '11px monospace';
    ctx.fillText(letra, x, y);
  });

  ctx.textAlign = 'start';
  ctx.textBaseline = 'alphabetic';
}

function desenharRobo() {
  ctx.fillStyle = COR_ACENTO;
  ctx.beginPath();
  ctx.moveTo(CENTRO_X, CENTRO_Y - 11);
  ctx.lineTo(CENTRO_X - 7, CENTRO_Y + 7);
  ctx.lineTo(CENTRO_X + 7, CENTRO_Y + 7);
  ctx.closePath();
  ctx.fill();
}

// A nuvem de pontos do lidar: cada leitura é UM ponto, não um vértice de
// polígono. O LD06 entrega centenas de pontos por frame - ligar um ponto ao
// outro desenharia paredes onde só há leituras independentes.
function desenharLeituras(leituras) {
  if (!leituras || leituras.length === 0) return;

  leituras.forEach((leitura) => {
    if (!leitura.valida) return;
    const [x, y] = paraXY(leitura.angulo, leitura.distancia_cm);
    ctx.beginPath();
    ctx.arc(x, y, 2.5, 0, 2 * Math.PI);
    ctx.fillStyle = COR_ACENTO;
    ctx.fill();
  });
}

// Setores do LiDAR LD06 (2026-08-17): cada setor é um leque de 30° com raio
// igual à menor distância medida ali. Cor por proximidade - perto vira
// alerta antes de a navegação agir, para o dono ver o que o robô vê.
function corDoSetor(distanciaCm) {
  if (distanciaCm === null || distanciaCm === undefined) return 'rgba(138, 147, 166, 0.18)';
  if (distanciaCm < 60) return 'rgba(232, 93, 74, 0.75)';
  if (distanciaCm < 120) return 'rgba(240, 173, 78, 0.65)';
  return 'rgba(79, 209, 197, 0.55)';
}

function desenharScan(scan) {
  if (!scan || !Array.isArray(scan.setores) || scan.setores.length === 0) return;

  scan.setores.forEach((setor) => {
    const distancia = setor.distancia_cm;
    const raioPx = distancia === null
      ? 6
      : (Math.min(distancia, ALCANCE_MAXIMO_CM) / ALCANCE_MAXIMO_CM) * RAIO_MAX_PX;

    // Dois arcos (inicio e fim) + a área entre eles, na convenção da tela.
    ctx.beginPath();
    ctx.moveTo(CENTRO_X, CENTRO_Y);
    ctx.arc(CENTRO_X, CENTRO_Y, raioPx, paraArcoCanvas(setor.fim_graus), paraArcoCanvas(setor.inicio_graus), true);
    ctx.closePath();
    ctx.fillStyle = corDoSetor(distancia);
    ctx.fill();
  });
}

function redesenhar() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  desenharGrade();
  if (ultimoScan) {
    desenharScan(ultimoScan);
  } else {
    desenharLeituras(ultimoMapa.leituras);
  }
  desenharRosaDosVentos();
  desenharRobo();

  if (ultimoScan && Array.isArray(ultimoScan.setores)) {
    const validos = ultimoScan.setores.filter((s) => s.distancia_cm !== null).length;
    statusEl.textContent = `lidar: ${validos}/${ultimoScan.setores.length} setores · ${ultimoScan.pontos ?? 0} pontos · ${ultimoScan.rpm ?? 0} rpm`;
  } else {
    const leituras = ultimoMapa.leituras;
    if (!leituras || leituras.length === 0) {
      statusEl.textContent = 'nenhuma varredura ainda';
    } else {
      const validas = leituras.filter((l) => l.valida).length;
      statusEl.textContent = `última varredura: ${validas}/${leituras.length} pontos`;
    }
  }
}

// Mesma regra do dashboard (2026-07-30): sem encoder de roda, o deslocamento é
// COMANDADO e não medido - a página não mostra número. Aqui pesa ainda mais que
// no dashboard: esta é a tela do mapa, onde um X/Y falso vira uma casa
// desenhada errada. Ver o comentário em fusao_sensores.py.
function renderizarPosicao(posicao) {
  if (!posicao) return;
  const medida = posicao.medida === true;
  const aviso = document.getElementById('pos-aviso');
  $('pos-x').textContent = medida ? `${posicao.x_m} m` : '—';
  $('pos-y').textContent = medida ? `${posicao.y_m} m` : '—';
  $('pos-orientacao').textContent = medida ? `${posicao.orientacao_graus}°` : '—';
  if (aviso) {
    aviso.textContent = medida
      ? ''
      : 'sem encoder de roda: deslocamento não medido, só comandado';
  }
}

function aplicarTelemetria(t) {
  // `in` e não truthiness: o pacote REDUZIDO que o firmware manda com o robô
  // em movimento omite campos de propósito - ausente mantém o último valor,
  // não zera a tela.
  if ('lidar_leituras' in t && Array.isArray(t.lidar_leituras)) {
    ultimoMapa = { leituras: t.lidar_leituras };
  }
  if ('rumo_graus' in t) rumoAtual = t.rumo_graus;
  if ('rumo_valido' in t) rumoConfiavel = !!t.rumo_valido;
  if (t.bussola_conectada === false) rumoAtual = null;

  redesenhar();
}

async function carregarEstadoInicial() {
  const resposta = await fetch('/estado');
  const corpo = await resposta.json();
  $('robot-name').textContent = corpo.estado.sistema.robot_name
    ? `— ${corpo.estado.sistema.robot_name}`
    : '';
  ultimoMapa = corpo.estado.mapa || { leituras: [] };
  renderizarPosicao(corpo.estado.posicao);
  aplicarTelemetria(corpo.estado.hardware);

  const lidar = await fetch('/api/lidar').then((r) => r.json());
  if (lidar && Array.isArray(lidar.setores)) {
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
  fonte.addEventListener('motion.scan_complete', (evento) => {
    ultimoMapa = JSON.parse(evento.data);
    redesenhar();
  });
  fonte.addEventListener('motion.position', (evento) => {
    renderizarPosicao(JSON.parse(evento.data));
  });
  fonte.addEventListener('comm.mensagem.telemetry', (evento) => {
    aplicarTelemetria(JSON.parse(evento.data));
  });
}

redesenhar();
carregarEstadoInicial();
conectar();