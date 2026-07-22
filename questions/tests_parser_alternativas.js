// Extrai o parser compartilhado e testa os casos que quebravam.
const fs = require('fs');
const src = fs.readFileSync('static/js/alternativas.js', 'utf8');
const re = /var match = line\.match\((\/[^;]+\/)i\);/.exec(src);
if (!re) { console.error('FALHA: não achei o regex do parser'); process.exit(1); }
const RE = new RegExp(re[1].slice(1, -1), 'i');

const casos = [
  ['A inobservância da legislação trabalhista levou os órgãos', false, 'artigo + substantivo'],
  ['A. celebrou acordo extrajudicial com o Município',         false, 'nome de parte abreviado'],
  ['A sociedade empresária Alfa foi contratada',               false, 'artigo + substantivo'],
  ['E o juiz, nesse caso, deve decidir',                       false, 'conjunção'],
  ['A) pode ocorrer na modalidade espontânea',                 true,  'alternativa real'],
  ['B) deve ser requisitada pelo STF',                         true,  'alternativa real'],
  ['E) deve ser requisitada pelo TST',                         true,  'alternativa real'],
];
let falhas = 0;
for (const [linha, esperado, nota] of casos) {
  const casou = RE.test(linha);
  const ok = casou === esperado;
  if (!ok) falhas++;
  console.log(`${ok ? 'ok  ' : 'FALHA'} ${String(casou).padEnd(5)} (esperado ${String(esperado).padEnd(5)}) ${nota.padEnd(24)} ${JSON.stringify(linha.slice(0,40))}`);
}
console.log(falhas ? `\n${falhas} falha(s)` : '\nparser ok');
process.exit(falhas ? 1 : 0);
