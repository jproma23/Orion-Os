// Fofão — Câmera + joystick da cabeça, COMPARTILHADO (2026-08-03).
//
// Este arquivo nasceu de um recorte: o mesmo código vivia dentro de
// dashboard.js e o dono pediu a câmera e o joystick da cabeça TAMBÉM na aba de
// conversa. Copiar seria criar duas versões que divergem na primeira correção -
// o defeito que o projeto já nomeou (empilhar camadas em vez de arrumar a
// fonte). Aqui é UMA fonte, usada pelas duas páginas.
//
// Não usa `import`/`export` de propósito: as páginas carregam scripts simples
// com <script src>, e trocar o esquema de módulo do site inteiro não é o
// assunto de hoje. As duas funções só montam se os elementos existirem, então
// o mesmo arquivo serve para uma página que tem só a câmera, só o joystick, ou
// os dois.

// --- Câmera ----------------------------------------------------------------
// O host vem do location, nunca da config: assim a mesma página funciona pelo
// IP da LAN, por pi-os.local e por fofao-pi (Tailscale). A porta vem do
// /estado, porque porta é config e não número no código.
//
// AO VIVO É O PADRÃO desde 2026-08-04, por decisão do dono - antes era FOTO.
// O stream contínuo custa ~16 KB/s (~58 MB/h) contra ~13 KB por quadro, e a
// razão de existir esta página é olhar de fora de casa, muitas vezes em dados
// móveis. O custo continua real: quem abrir a página no celular e esquecer
// aberta paga por hora. Ficou assim mesmo assim, porque na prática o modo foto
// era trocado para vivo em toda visita.
//
// Cuidado ao depurar aqui: o ustreamer roda com --persistent e, com a câmera
// DESPLUGADA, ele serve uma imagem "NO SIGNAL" com HTTP 200. Ou seja, o onerror
// NUNCA dispara nesse caso - a imagem chega, só não é a câmera.
let cameraBase = null;
let cameraModo = 'vivo';

function cameraElemento(id) {
  return document.getElementById(id);
}

function cameraAtualizarFoto() {
  if (!cameraBase) return;
  // o timestamp existe só para furar o cache do navegador
  cameraElemento('camera-img').src = `${cameraBase}/snapshot?t=${Date.now()}`;
}

function cameraDefinirModo(modo) {
  cameraModo = modo;
  for (const botao of document.querySelectorAll('.camera-opcao[data-modo]')) {
    botao.classList.toggle('ativo', botao.dataset.modo === modo);
  }
  const status = cameraElemento('camera-status');
  if (modo === 'vivo') {
    cameraElemento('camera-img').src = `${cameraBase}/stream`;
    if (status) status.textContent = 'ao vivo — ~16 KB/s';
  } else {
    cameraAtualizarFoto();
    if (status) status.textContent = 'foto — toque para atualizar';
  }
}

// Chamada a cada atualização de estado; só monta uma vez.
function montarCamera(camera) {
  const painel = cameraElemento('painel-camera');
  if (!painel) return;             // esta página não tem câmera
  if (!camera || !camera.porta) {
    painel.hidden = true;
    return;
  }
  painel.hidden = false;
  if (cameraBase) return;          // já montado, não remonta a cada evento
  cameraBase = `${location.protocol}//${location.hostname}:${camera.porta}`;
  // SEMPRE AO VIVO (2026-08-04). Os botões Foto | Ao vivo saíram da página a
  // pedido do dono; `cameraDefinirModo` fica, porque é ela que monta a URL do
  // stream - e continua servindo se um dia a escolha voltar.
  cameraDefinirModo('vivo');
}

// --- Joystick da cabeça ----------------------------------------------------
// IGUAL ao das rodas, por pedido do dono (2026-08-03): mesmo tamanho, mesma
// zona morta, mesma saturação na borda, mesmos eventos de ponteiro. Antes era
// menor e mais sensível ("olhar é gesto fino"); dois joysticks que respondem
// diferente ao mesmo gesto confundem mais do que a sensibilidade extra ajuda.
//
// A ÚNICA diferença que continua de pé é o que se manda, e ela é de fundo: aqui
// o comando é ÂNGULO ABSOLUTO, não incremento. A posição do punho É a posição
// da cabeça. Incremento acumularia erro a cada comando perdido pela rede, e a
// interface passaria a discordar do servo - exatamente a doença que o pan/tilt
// já tem (dois donos comandando por caminhos independentes, sem nunca
// ressincronizar). Absoluto não deriva: cada comando corrige o anterior.
//
// ATENÇÃO CONHECIDA: enquanto a visão estiver rastreando alguém, ela também
// comanda estes servos. Mexer aqui no meio disso faz os dois disputarem a
// cabeça. Não resolvido - está anotado.
function montarJoystickCabeca() {
  const base = document.getElementById('joystick-cabeca');
  const punho = document.getElementById('joystick-cabeca-punho');
  const status = document.getElementById('cabeca-status');
  const centro = document.getElementById('cabeca-centro');
  if (!base || !punho) return;     // esta página não tem o joystick

  const RAIO_MAX_PX = 62;          // idêntico ao das rodas
  const ZONA_MORTA = 0.18;         // idêntico ao das rodas
  const INTERVALO_MS = 120;        // servo aceita mais ritmo que o motor de passo
  const PAN_MAX = 80;              // iguais aos limites do servidor
  const TILT_MIN = -30, TILT_MAX = 45;

  let arrastando = false;
  let ultimoEnvio = 0;

  // DE 10 EM 10 GRAUS (2026-08-04), a pedido do dono: *"um toquinho e o servo
  // faz um movimento muito grande"*, *"tem q ser de 10 em 10 graus"*.
  //
  // O punho é pequeno e o curso inteiro vale 160° de pan: encostar o dedo
  // 8 px fora do centro já mandava ~10°, e o ângulo mudava a cada pixel. O
  // servo recebia um alvo novo a cada 120 ms e a cabeça tremia em vez de ir
  // para um lugar.
  //
  // Arredondar para o múltiplo de 10 mais próximo dá degraus estáveis: dentro
  // do mesmo degrau o valor não muda, então NADA é enviado (ver `ultimoPan`)
  // e a cabeça fica parada onde o dono a deixou. É a mesma ideia do ângulo
  // absoluto do handler: um alvo claro, não uma correção contínua.
  const PASSO_GRAUS = 10;
  const emDegraus = (graus) => Math.round(graus / PASSO_GRAUS) * PASSO_GRAUS;

  //: O último ângulo REALMENTE enviado. Sem ele o arredondamento sozinho não
  //: resolveria nada: o mesmo 30° sairia dez vezes por segundo enquanto o
  //: dedo passeasse dentro do degrau.
  let ultimoPan = null;
  let ultimoTilt = null;

  async function enviarCabeca(pan, tilt) {
    try {
      const r = await fetch('/cabeca', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pan, tilt }),
      });
      if (!r.ok) {
        const corpo = await r.json().catch(() => ({}));
        status.className = 'erro';
        status.textContent = corpo.erro || `recusado (HTTP ${r.status})`;
        return;
      }
      status.className = '';
      status.textContent = `cabeça: ${Math.round(pan)}° / ${Math.round(tilt)}°`;
    } catch (e) {
      status.className = 'erro';
      status.textContent = 'sem resposta do robô';
    }
  }

  // Mesma normalização das rodas: satura na borda do círculo em vez de deixar
  // o punho sair pelo canto da caixa.
  function normalizar(evento) {
    const r = base.getBoundingClientRect();
    const x = (evento.clientX - (r.left + r.width / 2)) / RAIO_MAX_PX;
    const y = (evento.clientY - (r.top + r.height / 2)) / RAIO_MAX_PX;
    const m = Math.hypot(x, y);
    return m > 1 ? [x / m, y / m] : [x, y];
  }

  function aplicar(x, y) {
    punho.style.transform = `translate(${x * RAIO_MAX_PX}px, ${y * RAIO_MAX_PX}px)`;
    if (Math.hypot(x, y) < ZONA_MORTA) return;
    const agora = Date.now();
    if (agora - ultimoEnvio < INTERVALO_MS) return;
    ultimoEnvio = agora;
    // y para CIMA na tela (negativo) = olhar para cima = tilt positivo.
    const tilt = y < 0 ? -y * TILT_MAX : -y * -TILT_MIN;
    const panDegrau = emDegraus(x * PAN_MAX);
    const tiltDegrau = emDegraus(tilt);
    // Mesmo degrau que o último = nada a dizer ao servo.
    if (panDegrau === ultimoPan && tiltDegrau === ultimoTilt) return;
    ultimoPan = panDegrau;
    ultimoTilt = tiltDegrau;
    enviarCabeca(panDegrau, tiltDegrau);
  }

  // SOLTAR: espelha `soltar()` do joystick das rodas, passo a passo - mesma
  // ordem, mesma classe visual, mesma rede de segurança. A única diferença é o
  // que se manda no fim: lá é STOP (parar as rodas), aqui é voltar ao centro,
  // que é o equivalente de repouso para uma cabeça.
  //
  // MANTER O ÂNGULO AO SOLTAR FOI TENTADO E RECUSADO (2026-08-04). A cabeça
  // voltando ao centro parecia defeito (*"eu aperto e volta"*), e a correção
  // chegou a ser escrita: soltar deixaria a cabeça onde estava, com o botão
  // CENTRO como único caminho de volta. O dono usou o joystick nesse meio
  // tempo, entendeu que **tem que segurar**, e decidiu ficar como está:
  // *"até que tá bom assim"*, *"eu gostei, aprendi usar"*.
  //
  // Fica registrado para ninguém "consertar" isto de novo achando que é
  // descuido: segurar para olhar é a escolha do dono, não um bug pendente.
  function soltar() {
    if (!arrastando) return;
    arrastando = false;
    base.classList.remove('ativo');
    punho.style.transform = 'translate(0, 0)';
    status.className = '';
    status.textContent = 'cabeça: centro';
    ultimoEnvio = 0;               // voltar ao centro nunca espera o intervalo
    // O degrau lembrado volta a zero junto: sem isto, arrastar de novo até 0°
    // não enviaria nada, porque o último degrau registrado seria o de antes.
    ultimoPan = 0;
    ultimoTilt = 0;
    enviarCabeca(0, 0);
  }

  base.addEventListener('pointerdown', (evento) => {
    arrastando = true;
    base.classList.add('ativo');
    base.setPointerCapture(evento.pointerId);
    aplicar(...normalizar(evento));
  });
  base.addEventListener('pointermove', (evento) => {
    if (arrastando) aplicar(...normalizar(evento));
  });
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach((tipo) =>
    base.addEventListener(tipo, soltar));
  // Mesma rede extra das rodas: perder o foco da aba com o dedo apertado não
  // pode deixar a cabeça virada para um canto.
  window.addEventListener('blur', soltar);

  centro?.addEventListener('click', () => {
    punho.style.transform = 'translate(0, 0)';
    // Zera o degrau lembrado junto com o comando: sem isto, mandar a cabeça ao
    // centro e depois arrastar de volta para 0° não enviaria nada, porque o
    // último degrau REGISTRADO ainda seria o de antes do clique.
    ultimoPan = 0;
    ultimoTilt = 0;
    enviarCabeca(0, 0);
  });
}

montarJoystickCabeca();
