/* Separa o enunciado das alternativas (A) B) …) de uma questão.
   Vive num arquivo próprio porque tanto a página da questão quanto a
   revisão precisam do mesmo parser — antes ele era inline no template e
   só a questão o usava. O regex é o mesmo que corrigiu 124 das 1008
   questões que abriam sem enunciado ("A inobservância…" virava a
   alternativa A): só o parêntese marca alternativa, nunca o ponto ou o
   espaço, e todos os enunciados são gravados normalizados como "A) ". */
(function (global) {
  'use strict';

  function parseAlternativas(texto) {
    texto = texto || '';
    var lines = texto.split('\n');
    var enunciadoLines = [];
    var alternativas = [];
    var currentAlt = null;

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var match = line.match(/^([A-E])\s*[)]\s*(.*)/i);
      if (match) {
        if (currentAlt) alternativas.push(currentAlt);
        currentAlt = { letter: match[1].toUpperCase(), text: match[2].trim() };
      } else if (currentAlt) {
        if (line.trim()) currentAlt.text += ' ' + line.trim();
      } else {
        enunciadoLines.push(line);
      }
    }
    if (currentAlt) alternativas.push(currentAlt);

    // Fallback: questão numa linha só, "enunciado … A) … B) …".
    if (alternativas.length === 0) {
      var inlineMatch = texto.match(/^([\s\S]*?)\s*(A\s*[)])/i);
      if (inlineMatch) {
        var enuncPart = inlineMatch[1];
        var altPart = texto.slice(enuncPart.length);
        enunciadoLines = [enuncPart.trim()];
        var altMatches = altPart.match(/([A-E])\s*[)]\s*[^A-E]*/gi);
        if (altMatches) {
          alternativas = [];
          altMatches.forEach(function (a) {
            var m = a.match(/^([A-E])\s*[)]\s*(.*)/i);
            if (m) alternativas.push({ letter: m[1].toUpperCase(), text: m[2].trim() });
          });
        }
      }
    }

    return { enunciado: enunciadoLines.join('\n').trim(), alternativas: alternativas };
  }

  global.parseAlternativas = parseAlternativas;
  // Também exportável em Node, para o teste que roda o parser fora do navegador.
  if (typeof module !== 'undefined' && module.exports) module.exports = parseAlternativas;
})(typeof window !== 'undefined' ? window : this);
