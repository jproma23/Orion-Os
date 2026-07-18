// Fofão — Mapa polar do radar (Cap 13 s.4: "MAPA — radar polar (0°–180°),
// posição e orientação estimadas"). Consumidor puro via SSE, igual as
// outras páginas - só desenha o que o servidor manda (motion.scan_complete
// vem de NavigationCore.executar_goto/patrol/explore, Cap 12 s.7).

const $ = (id) => document.getElementById(id);
const canvas = $('radar');
const ctx = canvas.getContext('2d');
const statusEl = $('radar-status');
const conexaoEl = $('conexao');

const ALCANCE_MAXIMO_CM = 200; // escala do mapa - leituras maiores sao cortadas na borda
const ANGULOS_ESPERADOS = [0, 30, 60, 90, 120, 150, 180];

const CENTRO_X = canvas.width / 2;
const CENTRO_Y = canvas.height - 24;
const RAIO_MAX_PX = Math.min(CENTRO_X, CENTRO_Y) - 24;

function paraXY(anguloGraus, distanciaCm) {
  const distancia = Math.min(distanciaCm, ALCANCE_MAXIMO_CM);
  const raioPx = (distancia / ALCANCE_MAXIMO_CM) * RAIO_MAX_PX;
  const anguloRad = (anguloGraus * Math.PI) / 180;
  return [CENTRO_X - raioPx * Math.cos(anguloRad), CENTRO_Y - raioPx * Math.sin(anguloRad)];
}

function desenharGrade() {
  ctx.strokeStyle = 'rgba(79, 209, 197, 0.18)';
  ctx.fillStyle = 'rgba(138, 147, 166, 0.7)';
  ctx.font = '10px monospace';
  ctx.lineWidth = 1;

  // aneis de alcance (a cada 50cm)
  for (let alcance = 50; alcance <= ALCANCE_MAXIMO_CM; alcance += 50) {
    ctx.beginPath();
    for (let angulo = 0; angulo <= 180; angulo += 2) {
      const [x, y] = paraXY(angulo, alcance);
      angulo === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
    const [lx, ly] = paraXY(90, alcance);
    ctx.fillText(`${alcance}cm`, lx + 4, ly - 2);
  }

  // raios nos 7 angulos de leitura
  ANGULOS_ESPERADOS.forEach((angulo) => {
    const [x, y] = paraXY(angulo, ALCANCE_MAXIMO_CM);
    ctx.beginPath();
    ctx.moveTo(CENTRO_X, CENTRO_Y);
    ctx.lineTo(x, y);
    ctx.stroke();
  });

  // robo (centro, apontando "pra frente" = 90 graus = pra cima)
  ctx.fillStyle = '#4fd1c5';
  ctx.beginPath();
  ctx.moveTo(CENTRO_X, CENTRO_Y - 10);
  ctx.lineTo(CENTRO_X - 7, CENTRO_Y + 6);
  ctx.lineTo(CENTRO_X + 7, CENTRO_Y + 6);
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
    ctx.strokeStyle = '#4fd1c5';
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  leituras.forEach((leitura) => {
    const [x, y] = leitura.valida
      ? paraXY(leitura.angulo, leitura.distancia_cm)
      : paraXY(leitura.angulo, ALCANCE_MAXIMO_CM);
    ctx.beginPath();
    ctx.arc(x, y, leitura.valida ? 4 : 3, 0, 2 * Math.PI);
    ctx.fillStyle = leitura.valida ? '#4fd1c5' : 'rgba(138, 147, 166, 0.5)';
    ctx.fill();
  });
}

function redesenhar(mapa) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  desenharGrade();
  desenharLeituras(mapa.leituras);

  if (!mapa.leituras || mapa.leituras.length === 0) {
    statusEl.textContent = 'nenhuma varredura ainda';
  } else {
    const validas = mapa.leituras.filter((l) => l.valida).length;
    statusEl.textContent = `última varredura: ${validas}/${mapa.leituras.length} leituras válidas`;
  }
}

function renderizarPosicao(posicao) {
  if (!posicao) return;
  $('pos-x').textContent = `${posicao.x_m} m`;
  $('pos-y').textContent = `${posicao.y_m} m`;
  $('pos-orientacao').textContent = `${posicao.orientacao_graus}°`;
}

async function carregarEstadoInicial() {
  const resposta = await fetch('/estado');
  const corpo = await resposta.json();
  $('robot-name').textContent = corpo.estado.sistema.robot_name
    ? `— ${corpo.estado.sistema.robot_name}`
    : '';
  redesenhar(corpo.estado.mapa);
  renderizarPosicao(corpo.estado.posicao);
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
    redesenhar(JSON.parse(evento.data));
  });
  fonte.addEventListener('motion.position', (evento) => {
    renderizarPosicao(JSON.parse(evento.data));
  });
}

desenharGrade();
carregarEstadoInicial();
conectar();
