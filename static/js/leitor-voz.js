/* Leitor em voz alta do texto de estudo + modo foco.
   Lê o conteúdo frase a frase (os <span class="frase"> criados por
   estudo-texto.js), destacando a frase corrente e rolando até ela. Uma
   utterance por frase: o evento `boundary` de palavra é instável entre
   navegadores, mas o start/end por frase é confiável e já entrega o
   "destaca frase a frase". Voz, velocidade e tom escolhidos ficam no
   localStorage. Sem SpeechSynthesis, a barra some. */
(function () {
  'use strict';

  var LS_VOZ = 'questoes-leitor-voz';
  var LS_RATE = 'questoes-leitor-rate';
  var LS_PITCH = 'questoes-leitor-pitch';
  var LS_FOCO = 'questoes-foco';

  function guardar(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function ler(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

  function initLeitor(barra) {
    if (barra.dataset.leitorBound === '1') return;
    barra.dataset.leitorBound = '1';

    var alvo = document.querySelector(barra.dataset.alvo || '.js-md-topico');
    var temTTS = 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined';
    if (!alvo || !temTTS) { barra.hidden = true; return; }

    var synth = window.speechSynthesis;
    var btnPlay = barra.querySelector('.js-leitor-play');
    var btnStop = barra.querySelector('.js-leitor-stop');
    var selVoz = barra.querySelector('.js-leitor-voz');
    var selRate = barra.querySelector('.js-leitor-rate');
    var selPitch = barra.querySelector('.js-leitor-pitch');

    var frases = [], idx = -1;
    var tocando = false, pausado = false, vozes = [];
    var keepalive = null;

    // -- Vozes (carregam de forma assíncrona no Chrome) --------------------
    function popularVozes() {
      vozes = synth.getVoices();
      var pt = vozes.filter(function (v) { return /^pt/i.test(v.lang); });
      var lista = pt.length ? pt : vozes;
      if (!selVoz) return;
      var salva = ler(LS_VOZ);
      selVoz.innerHTML = '';
      lista.forEach(function (v) {
        var o = document.createElement('option');
        o.value = v.voiceURI;
        o.textContent = v.name + (/^pt/i.test(v.lang) ? '' : ' (' + v.lang + ')');
        if (v.voiceURI === salva) o.selected = true;
        selVoz.appendChild(o);
      });
    }
    popularVozes();
    if (typeof synth.onvoiceschanged !== 'undefined') synth.onvoiceschanged = popularVozes;

    function vozAtual() {
      if (!selVoz || !selVoz.value) return null;
      for (var i = 0; i < vozes.length; i++) if (vozes[i].voiceURI === selVoz.value) return vozes[i];
      return null;
    }

    // -- Destaque ----------------------------------------------------------
    function limparDestaque() {
      alvo.querySelectorAll('.frase.is-falando').forEach(function (f) { f.classList.remove('is-falando'); });
    }
    function destacar(i) {
      limparDestaque();
      var f = frases[i];
      if (!f) return;
      f.classList.add('is-falando');
      var r = f.getBoundingClientRect();
      if (r.top < 80 || r.bottom > window.innerHeight - 80) {
        f.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    }

    // Contorna o corte de fala do Chrome depois de ~15s: cutuca o motor.
    function ligarKeepalive() {
      pararKeepalive();
      keepalive = setInterval(function () {
        if (tocando && !pausado && synth.speaking) { synth.pause(); synth.resume(); }
      }, 10000);
    }
    function pararKeepalive() { if (keepalive) { clearInterval(keepalive); keepalive = null; } }

    function falarDe(i) {
      if (i >= frases.length) { parar(); return; }
      idx = i;
      destacar(i);
      var texto = (frases[i].textContent || '').trim();
      if (!texto) { falarDe(i + 1); return; }
      var u = new SpeechSynthesisUtterance(texto);
      var v = vozAtual();
      if (v) { u.voice = v; u.lang = v.lang; } else { u.lang = 'pt-BR'; }
      u.rate = parseFloat(selRate && selRate.value) || 1;
      u.pitch = parseFloat(selPitch && selPitch.value) || 1;
      u.onend = function () { if (tocando && !pausado) falarDe(idx + 1); };
      u.onerror = function () { if (tocando && !pausado) falarDe(idx + 1); };
      synth.speak(u);
    }

    function coletarFrases() {
      frases = Array.prototype.slice.call(alvo.querySelectorAll('.frase'));
    }

    function refletirBotao() {
      if (!btnPlay) return;
      var falando = tocando && !pausado;
      btnPlay.setAttribute('aria-pressed', falando ? 'true' : 'false');
      btnPlay.querySelector('.js-leitor-icone').textContent = falando ? '⏸' : '▶';
      btnPlay.querySelector('.js-leitor-rotulo').textContent = falando ? 'Pausar' : (pausado ? 'Continuar' : 'Ouvir');
      barra.classList.toggle('is-tocando', tocando);
    }

    function tocar() {
      if (pausado) { synth.resume(); pausado = false; tocando = true; refletirBotao(); return; }
      coletarFrases();
      if (!frases.length) return;
      synth.cancel();
      tocando = true; pausado = false;
      ligarKeepalive();
      refletirBotao();
      falarDe(idx >= 0 && idx < frases.length ? idx : 0);
    }
    function pausar() { synth.pause(); pausado = true; refletirBotao(); }
    function parar() {
      tocando = false; pausado = false; idx = -1;
      synth.cancel(); pararKeepalive(); limparDestaque(); refletirBotao();
    }

    if (btnPlay) btnPlay.addEventListener('click', function () {
      if (tocando && !pausado) pausar(); else tocar();
    });
    if (btnStop) btnStop.addEventListener('click', parar);

    // Trocar voz/velocidade/tom no meio recomeça a frase atual com o novo som.
    [selRate, selPitch].forEach(function (sel) {
      if (!sel) return;
      sel.addEventListener('change', function () {
        guardar(sel === selRate ? LS_RATE : LS_PITCH, sel.value);
        if (tocando && !pausado) { synth.cancel(); falarDe(idx < 0 ? 0 : idx); }
      });
    });
    if (selVoz) selVoz.addEventListener('change', function () {
      guardar(LS_VOZ, selVoz.value);
      if (tocando && !pausado) { synth.cancel(); falarDe(idx < 0 ? 0 : idx); }
    });

    // Restaura preferências salvas.
    var rSalvo = ler(LS_RATE); if (rSalvo && selRate) selRate.value = rSalvo;
    var pSalvo = ler(LS_PITCH); if (pSalvo && selPitch) selPitch.value = pSalvo;

    // Ao sair da página, silenciar (o motor continua falando fora dela).
    window.addEventListener('beforeunload', function () { synth.cancel(); });

    // -- Modo foco (feature 3) --------------------------------------------
    var btnFoco = barra.querySelector('.js-leitor-foco');
    if (btnFoco) {
      function aplicarFoco(ativo) {
        alvo.classList.toggle('foco-ativo', ativo);
        btnFoco.setAttribute('aria-pressed', ativo ? 'true' : 'false');
        guardar(LS_FOCO, ativo ? '1' : '0');
      }
      btnFoco.addEventListener('click', function () {
        aplicarFoco(!alvo.classList.contains('foco-ativo'));
      });
      if (ler(LS_FOCO) === '1') aplicarFoco(true);
    }
  }

  function init() {
    document.querySelectorAll('.js-leitor-barra').forEach(initLeitor);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
