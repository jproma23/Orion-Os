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

// ---- Rosa dos ventos ----
//
// Rosa FIXA (Norte sempre em cima) e seta girando: a seta aponta para onde o
// robo esta virado. A alternativa - girar a rosa e travar a seta - confunde
// mais do que ajuda em tela de dashboard, onde quem olha quer saber "para
// onde ele esta olhando em relacao ao Norte".
//
// Sem numeros nem angulos escritos, de proposito: o desenho comunica direto.

function desenharMarcasDaRosa() {
  const grupo = document.getElementById('rosa-marcas');
  if (!grupo || grupo.childElementCount) return;  // ja desenhadas
  for (let graus = 0; graus < 360; graus += 15) {
    const cardeal = graus % 45 === 0;
    const risco = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    risco.setAttribute('x1', '0');
    risco.setAttribute('y1', '-88');
    risco.setAttribute('x2', '0');
    risco.setAttribute('y2', cardeal ? '-78' : '-83');
    risco.setAttribute('transform', `rotate(${graus})`);
    risco.setAttribute('class', cardeal ? 'rosa-marca rosa-marca--cardeal' : 'rosa-marca');
    grupo.appendChild(risco);
  }
}

// Caminho mais curto entre dois rumos. Sem isto, ir de 350 para 10 graus faz
// a agulha girar 340 graus para tras em vez de 20 para frente - fica feio e
// da impressao de leitura errada. Acumula o giro num contador continuo.
let rumoAcumulado = 0;
let rumoAnterior = null;

function rotacaoContinua(rumo) {
  if (rumoAnterior === null) {
    rumoAnterior = rumo;
    rumoAcumulado = rumo;
    return rumoAcumulado;
  }
  let delta = rumo - rumoAnterior;
  while (delta > 180) delta -= 360;
  while (delta < -180) delta += 360;
  rumoAcumulado += delta;
  rumoAnterior = rumo;
  return rumoAcumulado;
}

function atualizarBussola(hardware) {
  desenharMarcasDaRosa();

  const rosa = document.getElementById('rosa-ventos');
  const agulha = document.getElementById('rosa-agulha');
  const aviso = $('bus-aviso');
  if (!rosa || !agulha) return;

  const conectada = hardware.bussola_conectada;
  rosa.classList.remove('crua', 'ausente');
  aviso.classList.remove('alerta');

  if (conectada === undefined || conectada === null) {
    rosa.classList.add('ausente');
    aviso.textContent = 'sem telemetria';
    return;
  }

  if (!conectada) {
    rosa.classList.add('ausente');
    aviso.classList.add('alerta');
    aviso.textContent = 'desconectada — não respondeu no I2C 0x1C';
    return;
  }

  const rumo = hardware.rumo_graus;
  if (rumo !== undefined && rumo !== null) {
    agulha.setAttribute('transform', `rotate(${rotacaoContinua(rumo).toFixed(1)})`);
  }

  if (hardware.bussola_calibrando) {
    rosa.classList.add('crua');
    aviso.textContent = 'calibrando — gire o robô devagar';
  } else if (hardware.rumo_valido) {
    aviso.textContent = '';
  } else {
    // Agulha vermelha e apagada: a direcao existe mas carrega o offset dos
    // imas dos motores junto. O aviso e curto porque o desenho ja diz.
    rosa.classList.add('crua');
    aviso.classList.add('alerta');
    aviso.textContent = 'não calibrada — direção não confiável';
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
