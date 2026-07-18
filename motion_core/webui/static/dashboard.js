// ORION X — Dashboard (Cap 13 s.4-5): consumidor puro do Event Bus via
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
  $('tel-temp').textContent = formatar(estado.hardware.temperatura_c, ' °C');
  $('tel-umidade').textContent = formatar(estado.hardware.umidade_percent, ' %');
  $('tel-inclinacao').textContent = formatar(estado.hardware.inclinacao_graus, '°');

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
