/* Marcação do texto de estudo depois do render do Markdown.
   Tudo aqui deriva do conteúdo — não é enfeite:
   - a seção final de pegadinhas é o único trecho com função distinta;
   - citações normativas são o que sustenta o estudo e precisam ser achadas
     de relance. */
(function (global) {
  'use strict';

  // "art. 64", "arts. 133 a 137", "art. 5º, LXXVIII", "Súmula 123",
  // "Súmula Vinculante 10", "Lei 9.868/99". Conservador de propósito:
  // só pega o que começa por uma dessas palavras.
  var RE_REF = /\b(arts?\.?\s*\d+[\dºª\-A-Z]*(?:\s*(?:a|e)\s*\d+[\dºª\-A-Z]*)?|súmulas?\s+(?:vinculante\s+)?n?[º°]?\s*\d+|lei\s+n?[º°]?\s*[\d.]+\s*\/\s*\d{2,4}|lei\s+n?[º°]?\s*[\d.]{4,})/gi;

  function marcarReferencias(raiz) {
    var and = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        // não mexe dentro de código nem do que já foi marcado
        if (n.parentElement.closest('code, pre, a, .ref-legal')) return NodeFilter.FILTER_REJECT;
        return RE_REF.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    var alvos = [];
    while (and.nextNode()) alvos.push(and.currentNode);
    alvos.forEach(function (no) {
      var frag = document.createDocumentFragment();
      var resto = no.nodeValue;
      var m, ultimo = 0;
      RE_REF.lastIndex = 0;
      while ((m = RE_REF.exec(resto)) !== null) {
        if (m.index > ultimo) frag.appendChild(document.createTextNode(resto.slice(ultimo, m.index)));
        var span = document.createElement('span');
        span.className = 'ref-legal';
        span.textContent = m[0];
        frag.appendChild(span);
        ultimo = m.index + m[0].length;
      }
      if (ultimo < resto.length) frag.appendChild(document.createTextNode(resto.slice(ultimo)));
      no.parentNode.replaceChild(frag, no);
    });
  }

  // A síntese sempre fecha com "## Erros comuns e pegadinhas" (é exigência do
  // prompt). Do título até o fim vira um painel de alerta.
  function agruparPegadinhas(raiz) {
    var titulos = raiz.querySelectorAll('h2, h3');
    var alvo = null;
    for (var i = 0; i < titulos.length; i++) {
      var t = (titulos[i].textContent || '').toLowerCase();
      if (t.indexOf('pegadinha') !== -1 || (t.indexOf('erro') !== -1 && t.indexOf('comum') !== -1)) {
        alvo = titulos[i];
        break;
      }
    }
    if (!alvo) return;
    var bloco = document.createElement('section');
    bloco.className = 'bloco-pegadinhas';
    alvo.parentNode.insertBefore(bloco, alvo);
    while (alvo && alvo.nextSibling !== bloco) {
      var proximo = bloco.nextSibling;
      bloco.appendChild(alvo);
      alvo = proximo;
      if (!alvo) break;
    }
  }

  // Tabela larga rola no próprio bloco em vez de esticar a página.
  function envolverTabelas(raiz) {
    raiz.querySelectorAll('table').forEach(function (tb) {
      if (tb.parentElement.classList.contains('table-scroll')) return;
      var wrap = document.createElement('div');
      wrap.className = 'table-scroll';
      tb.parentNode.insertBefore(wrap, tb);
      wrap.appendChild(tb);
    });
  }

  // A IA costuma abrir repetindo o nome do tópico, que já é o <h1> da página.
  function removerTituloDuplicado(raiz, nome) {
    var primeiro = raiz.firstElementChild;
    if (!primeiro || !/^H[12]$/.test(primeiro.tagName) || !nome) return;
    var norm = function (s) {
      return (s || '').toLowerCase().normalize('NFD')
        .replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
    };
    var titulo = norm(primeiro.textContent), alvo = norm(nome);
    if (titulo === alvo || titulo.indexOf(alvo) === 0) primeiro.remove();
  }

  global.formatarTextoDeEstudo = function (el) {
    removerTituloDuplicado(el, el.dataset.nome);
    agruparPegadinhas(el);
    marcarReferencias(el);
    envolverTabelas(el);
  };
})(window);
