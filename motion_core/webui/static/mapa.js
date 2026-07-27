// Fofão — Mapa polar 360° (Cap 13 s.4). Consumidor puro via SSE: só desenha
// o que o servidor manda.
//
// Mudou de meia-lua para círculo completo em 2026-07-27, para caber o que o
// robô realmente enxerga ao redor:
//
//   - ARCO FRONTAL varrido pelo servo do radar. NÃO são 180°: o braço do
//     servo colide com o suporte da webcam fora de 30–150° (achado físico de
//     2026-07-24), então o arco real é de 120°. Desenhar 180° prometeria uma
//     cobertura que não existe.
//   - CAMPO TRASEIRO fixo. O ultrassom de trás não tem servo: é um cone só,
//     apontado para trás. A largura desenhada é o feixe do HC-SR04 (~30°) -
//     ele não devolve um ponto, devolve "há algo dentro deste cone".
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

const ALCANCE_MAXIMO_CM = 200; // escala do mapa - leituras maiores são cortadas na borda

//: arco físico do servo do radar (radar_manager.h) - 120°, não 180°
const ARCO_RADAR_MIN = 30;
const ARCO_RADAR_MAX = 150;
//: ângulos que a varredura reporta de fato (7 leituras, de 20 em 20 graus)
const ANGULOS_ESPERADOS = [30, 50, 70, 90, 110, 130, 150];

//: abertura do feixe de um HC-SR04. Ele não enxerga uma direção, enxerga um
//: cone - o desenho tem que dizer isso, senão a leitura de trás parece muito
//: mais precisa do que é.
const FEIXE_ULTRASSOM_GRAUS = 30;
const ANGULO_TRASEIRO = 270; // para baixo na tela

const CENTRO_X = canvas.width / 2;
const CENTRO_Y = canvas.height / 2;
const RAIO_MAX_PX = Math.min(CENTRO_X, CENTRO_Y) - 34; // folga para as letras da rosa

const COR_ACENTO = '#4fd1c5';
const COR_FRACA = 'rgba(138, 147, 166, 0.7)';
const COR_ALERTA = '#e85d4a';

let rumoAtual = null;      // graus da bússola, ou null
let rumoConfiavel = false; // rumo_valido - sem calibração a rosa não vale
let ultimoMapa = { leituras: [] };
let traseiraCm = null;
let traseiraSemEco = false;

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

function setorPreenchido(deGraus, ateGraus, raioPx, preenchimento) {
  ctx.beginPath();
  ctx.moveTo(CENTRO_X, CENTRO_Y);
  ctx.arc(CENTRO_X, CENTRO_Y, raioPx, paraArcoCanvas(deGraus), paraArcoCanvas(ateGraus), true);
  ctx.closePath();
  ctx.fillStyle = preenchimento;
  ctx.fill();
}

function desenharGrade() {
  ctx.lineWidth = 1;

  // anéis de alcance a cada 50cm, agora círculos inteiros
  ctx.strokeStyle = 'rgba(79, 209, 197, 0.15)';
  ctx.font = '10px monospace';
  for (let alcance = 50; alcance <= ALCANCE_MAXIMO_CM; alcance += 50) {
    const raioPx = (alcance / ALCANCE_MAXIMO_CM) * RAIO_MAX_PX;
    ctx.beginPath();
    ctx.arc(CENTRO_X, CENTRO_Y, raioPx, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.fillStyle = 'rgba(138, 147, 166, 0.55)';
    ctx.fillText(`${alcance}cm`, CENTRO_X + 4, CENTRO_Y - raioPx - 3);
  }

  // Zona que o radar cobre de verdade, marcada por baixo das leituras: deixa
  // óbvio que existe um vão cego entre o arco frontal e o cone traseiro.
  setorPreenchido(ARCO_RADAR_MIN, ARCO_RADAR_MAX, RAIO_MAX_PX, 'rgba(79, 209, 197, 0.05)');

  // raios nos ângulos de leitura reais
  ctx.strokeStyle = 'rgba(79, 209, 197, 0.16)';
  ANGULOS_ESPERADOS.forEach((angulo) => {
    const [x, y] = pontoNoRaio(angulo, RAIO_MAX_PX);
    ctx.beginPath();
    ctx.moveTo(CENTRO_X, CENTRO_Y);
    ctx.lineTo(x, y);
    ctx.stroke();
  });
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

function desenharCampoTraseiro() {
  const de = ANGULO_TRASEIRO - FEIXE_ULTRASSOM_GRAUS / 2;
  const ate = ANGULO_TRASEIRO + FEIXE_ULTRASSOM_GRAUS / 2;

  // O cone SEMPRE aparece, mesmo sem leitura: ele é a cobertura do sensor,
  // não a medida. Assim fica visível que aquela fatia é vigiada.
  setorPreenchido(de, ate, RAIO_MAX_PX, 'rgba(138, 147, 166, 0.07)');

  if (traseiraCm === null || traseiraCm === undefined) return;

  if (traseiraSemEco) {
    // Nada refletiu: o firmware reporta o teto de alcance, que não é medida.
    // Preencher o cone ali seria inventar uma parede a 5 metros.
    ctx.strokeStyle = 'rgba(138, 147, 166, 0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.arc(CENTRO_X, CENTRO_Y, RAIO_MAX_PX, paraArcoCanvas(de), paraArcoCanvas(ate), true);
    ctx.stroke();
    ctx.setLineDash([]);
    return;
  }

  const raioPx = (Math.min(traseiraCm, ALCANCE_MAXIMO_CM) / ALCANCE_MAXIMO_CM) * RAIO_MAX_PX;
  setorPreenchido(de, ate, raioPx, 'rgba(79, 209, 197, 0.22)');
  ctx.strokeStyle = COR_ACENTO;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(CENTRO_X, CENTRO_Y, raioPx, paraArcoCanvas(de), paraArcoCanvas(ate), true);
  ctx.stroke();
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

function desenharLeituras(leituras) {
  if (!leituras || leituras.length === 0) return;

  const validas = leituras.filter((l) => l.valida);
  if (validas.length > 1) {
    ctx.beginPath();
    validas.forEach((leitura, indice) => {
      const [x, y] = paraXY(leitura.angulo, leitura.distancia_cm);
      indice === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = COR_ACENTO;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  leituras.forEach((leitura) => {
    const [x, y] = leitura.valida
      ? paraXY(leitura.angulo, leitura.distancia_cm)
      : paraXY(leitura.angulo, ALCANCE_MAXIMO_CM);
    ctx.beginPath();
    ctx.arc(x, y, leitura.valida ? 4 : 3, 0, 2 * Math.PI);
    ctx.fillStyle = leitura.valida ? COR_ACENTO : 'rgba(138, 147, 166, 0.5)';
    ctx.fill();
  });
}

function redesenhar() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  desenharGrade();
  desenharCampoTraseiro();
  desenharLeituras(ultimoMapa.leituras);
  desenharRosaDosVentos();
  desenharRobo();

  const leituras = ultimoMapa.leituras;
  if (!leituras || leituras.length === 0) {
    statusEl.textContent = 'nenhuma varredura ainda';
  } else {
    const validas = leituras.filter((l) => l.valida).length;
    statusEl.textContent = `última varredura: ${validas}/${leituras.length} leituras válidas`;
  }
}

function renderizarPosicao(posicao) {
  if (!posicao) return;
  $('pos-x').textContent = `${posicao.x_m} m`;
  $('pos-y').textContent = `${posicao.y_m} m`;
  $('pos-orientacao').textContent = `${posicao.orientacao_graus}°`;
}

function fmtCm(v, semEco) {
  if (semEco) return 'sem eco';
  return v === null || v === undefined ? '—' : `${Number(v).toFixed(1)} cm`;
}

function aplicarTelemetria(t) {
  // `in` e não truthiness: o pacote REDUZIDO que o firmware manda com o robô
  // em movimento omite campos de propósito - ausente mantém o último valor,
  // não zera a tela.
  if ('distancia_traseira_cm' in t) traseiraCm = t.distancia_traseira_cm;
  if ('traseiro_sem_eco' in t) traseiraSemEco = !!t.traseiro_sem_eco;
  if ('rumo_graus' in t) rumoAtual = t.rumo_graus;
  if ('rumo_valido' in t) rumoConfiavel = !!t.rumo_valido;
  if (t.bussola_conectada === false) rumoAtual = null;

  $('dist-frente').textContent = fmtCm(t.distancia_frontal_cm, t.frontal_sem_eco);
  $('dist-tras').textContent = fmtCm(traseiraCm, traseiraSemEco);
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
