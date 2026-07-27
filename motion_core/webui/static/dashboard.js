// Fofão — Dashboard (Cap 13 s.4-5): consumidor puro do Event Bus via
// SSE, igual o avatar (Cap 13 s.2) - nenhuma decisao acontece aqui.

const $ = (id) => document.getElementById(id);
const conexaoEl = $('conexao');
const listaEventos = $('lista-eventos');

function formatar(valor, sufixo = '') {
  return valor === null || valor === undefined ? '—' : `${valor}${sufixo}`;
}

function renderizarEstado(estado) {
  $('robot-name').textContent = estado.sistema.robot_name ? `— ${estado.sistema.robot_name}` : '';
  $('sistema-modo').textContent = formatar(estado.sistema.modo);
  $('nav-modo').textContent = formatar(estado.navegacao.modo);
  $('hw-estado').textContent = formatar(estado.hardware.estado);
  $('hw-movimento').textContent =
    estado.hardware.em_movimento === null ? '—' : (estado.hardware.em_movimento ? 'sim' : 'não');

  const segEl = $('seg-ativo');
  segEl.textContent = estado.seguranca.safe_mode_ativo ? 'ATIVO' : 'inativo';
  segEl.className = estado.seguranca.safe_mode_ativo ? 'valor-alerta' : 'valor-ok';
  $('seg-motivo').textContent = formatar(estado.seguranca.motivo);

  $('tel-distancia').textContent = formatar(estado.hardware.distancia_frontal_cm, ' cm');
  $('tel-distancia-tras').textContent = formatar(estado.hardware.distancia_traseira_cm, ' cm');
  // "sem eco" NAO e uma medida: o pulso saiu e nada voltou, e o firmware
  // reporta o teto de alcance no lugar (ver sensor_ultrassonico.h). Mostrar
  // 517 cm como se fosse parede engana quem le a tela.
  if (estado.hardware.frontal_sem_eco) $('tel-distancia').textContent = 'sem eco (nada por perto)';
  if (estado.hardware.traseiro_sem_eco) $('tel-distancia-tras').textContent = 'sem eco (nada por perto)';
  $('tel-temp').textContent = formatar(estado.hardware.temperatura_c, ' °C');
  $('tel-umidade').textContent = formatar(estado.hardware.umidade_percent, ' %');
  $('tel-inclinacao').textContent = formatar(estado.hardware.inclinacao_graus, '°');
  $('tel-aceleracao').textContent = formatar(estado.hardware.aceleracao_g, ' G');
  const imp = estado.hardware.impacto_detectado;
  $('tel-impacto').textContent = imp === null ? '—' : (imp ? 'IMPACTO!' : 'ok');

  atualizarBussola(estado.hardware);

  if (estado.posicao) {
    $('pos-x').textContent = formatar(estado.posicao.x_m, ' m');
    $('pos-y').textContent = formatar(estado.posicao.y_m, ' m');
    $('pos-orientacao').textContent = formatar(estado.posicao.orientacao_graus, '°');
    $('pos-velocidade').textContent = formatar(estado.posicao.velocidade_m_s, ' m/s');
  }

  $('missao-ultimo').textContent = estado.navegacao.ultimo_plano
    ? estado.navegacao.ultimo_plano.evento
    : '—';
  // estado.voz / estado.visao continuam disponiveis em /estado (podem ser
  // uteis pra outras paginas no futuro - CONVERSA, por exemplo) - so nao
  // sao mostrados neste dashboard simplificado.
}

const PONTOS_CARDEAIS = ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO'];

function pontoCardeal(graus) {
  return PONTOS_CARDEAIS[Math.round(graus / 45) % 8];
}

// Bussola QMC6310. A regra desta tela: NUNCA mostrar um rumo como se fosse
// direcao confiavel enquanto `rumo_valido` for falso. Sem calibracao o
// numero existe (o sensor esta lendo), mas ele carrega o offset do ferro do
// robo junto - os imas permanentes dos motores de passo sao a maior fonte,
// nao o chassi, que e de aluminio e nao e ferromagnetico.
function atualizarBussola(hardware) {
  const conectada = hardware.bussola_conectada;

  if (conectada === undefined || conectada === null) {
    $('bus-rumo').textContent = '—';
    $('bus-campo').textContent = '—';
    $('bus-estado').textContent = 'sem telemetria';
    return;
  }

  if (!conectada) {
    $('bus-rumo').textContent = '—';
    $('bus-campo').textContent = '—';
    $('bus-estado').textContent = 'desconectada (não respondeu no I2C 0x1C)';
    return;
  }

  const rumo = hardware.rumo_graus;
  const temRumo = rumo !== undefined && rumo !== null;

  if (hardware.bussola_calibrando) {
    $('bus-estado').textContent = 'calibrando — gire o robô';
    $('bus-rumo').textContent = 'aguardando calibração';
  } else if (hardware.rumo_valido && temRumo) {
    $('bus-estado').textContent = 'calibrada';
    $('bus-rumo').textContent = `${rumo.toFixed(1)}° ${pontoCardeal(rumo)}`;
  } else {
    $('bus-estado').textContent = 'NÃO calibrada — rode CALIBRATE_COMPASS';
    // mostra o valor cru, mas marcado: some quem acha que ja e direcao boa
    $('bus-rumo').textContent = temRumo
      ? `${rumo.toFixed(1)}° (cru, não confiável)`
      : '—';
  }

  // O campo tem que ficar PARADO por mais que o robo gire - e o melhor teste
  // de qualidade que existe. No Brasil o campo da Terra e ~23 uT; muito acima
  // disso e offset de ferro por perto (hard-iron), que a calibracao remove.
  const campo = hardware.campo_ut;
  if (campo === undefined || campo === null) {
    $('bus-campo').textContent = '—';
  } else {
    const fora = campo < 15 || campo > 40;
    $('bus-campo').textContent =
      `${campo.toFixed(1)} µT` + (fora ? ' (fora da faixa da Terra, ~23 µT)' : '');
  }
}

function adicionarEventoNaLista(topico, dados, timestamp) {
  const item = document.createElement('li');
  const hora = new Date(timestamp * 1000).toLocaleTimeString('pt-BR');
  item.innerHTML = `<span class="hora">${hora}</span><span class="topico">${topico}</span><span class="dados"></span>`;
  item.querySelector('.dados').textContent = JSON.stringify(dados);
  listaEventos.prepend(item);
  while (listaEventos.children.length > 30) {
    listaEventos.removeChild(listaEventos.lastChild);
  }
}

async function atualizarEstadoAgregado() {
  const resposta = await fetch('/estado');
  const corpo = await resposta.json();
  renderizarEstado(corpo.estado);
  return corpo;
}

async function carregarEstadoInicial() {
  const corpo = await atualizarEstadoAgregado();
  for (const evento of corpo.eventos_recentes) {
    adicionarEventoNaLista(evento.topico, evento.dados, evento.timestamp);
  }
}

// campos do estado agregado que cada topico de evento afeta - usado so
// pra saber quais atualizar sem reconstruir tudo a cada evento
const AFETA_ESTADO = new Set([
  'system.ready', 'navigation.mode_changed', 'navigation.plan_created',
  'navigation.segment_started', 'navigation.segment_completed',
  'navigation.obstacle_avoided', 'motion.status', 'motion.position',
  'comm.mensagem.telemetry', 'safety.safe_mode_entered', 'safety.safe_mode_exited',
  'vision.person_detected', 'voice.status',
]);

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

  for (const topico of AFETA_ESTADO) {
    fonte.addEventListener(topico, (evento) => {
      const dados = JSON.parse(evento.data);
      adicionarEventoNaLista(topico, dados, Date.now() / 1000);
      // re-busca so o estado agregado (sem historico) do servidor, em vez
      // de duplicar a logica de merge em dois lugares (server.py ja sabe
      // fazer isso) - nao usa carregarEstadoInicial() aqui pra nao
      // reinserir o historico de eventos e duplicar o item que acabamos
      // de adicionar acima
      atualizarEstadoAgregado();
    });
  }
}

carregarEstadoInicial();
conectar();
