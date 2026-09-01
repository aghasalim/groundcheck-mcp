// A randomised differential test of the check_math grammar, in Node.
//
// verify/matheval.c holds check_math to twenty four expressions I chose. I
// chose them, so they cover the mistakes I thought of. A precedence or
// associativity bug in a case I did not think of would pass every one of them.
//
// This generates random expressions instead, parses each one with a second
// parser written here, and then asks the real check_math whether its own value
// equals the value this parser computed. Two parsers reading the same string
// have to agree on precedence, on associativity, and on what Python's // and %
// do with negative operands, or the tool refuses the claim and this fails.
//
// JavaScript is a useful language to write the second parser in precisely
// because its arithmetic is not Python's: % truncates toward zero here and
// floors there, and // is a comment rather than an operator. Nothing can be
// inherited by accident; every rule has to be written out and can therefore be
// wrong in a way the harness would catch.
//
//     node verify/mathfuzz.js <repo-root> [iterations]

'use strict';
const { spawnSync } = require('child_process');
const path = require('path');

const ITERATIONS_DEFAULT = 200000;
const MAGNITUDE_CAP = 1e7;

// xorshift32, so the run is reproducible and needs no dependency.
let rngState = 0x9e3779b9;
function rnd() {
  let x = rngState;
  x ^= x << 13; x >>>= 0;
  x ^= x >> 17;
  x ^= x << 5;  x >>>= 0;
  rngState = x;
  return x;
}
function below(n) { return rnd() % n; }

// ---- Python's float semantics, written out ------------------------------
// JavaScript's % is C's fmod: it takes the sign of the dividend. Python's
// takes the sign of the divisor, and its // is derived from the same fmod
// rather than from floor(a / b), which is not the same number when a / b lands
// just under an integer.
function pyMod(a, b) {
  let mod = a % b;
  if (mod !== 0 && (b < 0) !== (mod < 0)) mod += b;
  return mod;
}
function pyFloorDiv(a, b) {
  let mod = a % b;
  let div = (a - mod) / b;
  if (mod !== 0 && (b < 0) !== (mod < 0)) div -= 1.0;
  if (div !== 0) {
    const fl = Math.floor(div);
    return (div - fl > 0.5) ? fl + 1.0 : fl;
  }
  return div;
}

// ---- the second parser --------------------------------------------------
// expr := term (('+'|'-') term)*
// term := factor (('*'|'/'|'//'|'%') factor)*
// factor := ('+'|'-') factor | power
// power := atom ['**' factor]          right associative
// atom := number | '(' expr ')'
function evaluate(src) {
  let i = 0;
  let failed = false;
  const ws = () => { while (src[i] === ' ' || src[i] === '\t') i++; };
  // Any step that leaves the finite doubles is abandoned. Python raises on an
  // overflowing ** and returns unverifiable for an infinite result, so those
  // expressions have no value for the two sides to agree on and are not what
  // this is testing.
  //
  // Intermediates are also kept under MAGNITUDE_CAP. CPython and V8 do not
  // return the same double for pow: 29.95 ** 8.0 differs between them by one
  // ulp, measured, and a later % or // turns that one ulp into a difference
  // big enough to look like a disagreement about the grammar. It is not one,
  // it is the two platforms' libm, so the generator stays where an ulp is
  // small enough that % and // cannot amplify it past the tolerance.
  const fin = (v) => {
    if (!Number.isFinite(v) || Math.abs(v) > MAGNITUDE_CAP) failed = true;
    return v;
  };

  function atom() {
    ws();
    if (src[i] === '(') {
      i++;
      const v = expr();
      ws();
      if (src[i] !== ')') { failed = true; return 0; }
      i++;
      return v;
    }
    const m = /^[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?/.exec(src.slice(i));
    if (!m) { failed = true; return 0; }
    i += m[0].length;
    return parseFloat(m[0]);
  }
  function power() {
    const base = atom();
    if (failed) return 0;
    ws();
    if (src[i] === '*' && src[i + 1] === '*') {
      i += 2;
      const e = factor();
      if (failed) return 0;
      return fin(Math.pow(base, e));
    }
    return base;
  }
  function factor() {
    ws();
    if (src[i] === '-') { i++; return -factor(); }
    if (src[i] === '+') { i++; return factor(); }
    return power();
  }
  function term() {
    let v = factor();
    for (;;) {
      if (failed) return 0;
      ws();
      if (src[i] === '*' && src[i + 1] === '*') return v;
      if (src[i] === '/' && src[i + 1] === '/') { i += 2; v = fin(pyFloorDiv(v, factor())); }
      else if (src[i] === '*') { i++; v = fin(v * factor()); }
      else if (src[i] === '/') { i++; v = fin(v / factor()); }
      else if (src[i] === '%') { i++; v = fin(pyMod(v, factor())); }
      else return v;
    }
  }
  function expr() {
    let v = term();
    for (;;) {
      if (failed) return 0;
      ws();
      if (src[i] === '+') { i++; v = fin(v + term()); }
      else if (src[i] === '-') { i++; v = fin(v - term()); }
      else return v;
    }
  }

  const v = expr();
  ws();
  if (failed || i !== src.length) return null;
  return v;
}

// ---- generation ---------------------------------------------------------
// Literals always carry a decimal point, so every constant is a Python float
// and no case turns into exact integer arithmetic on one side only.
function literal() {
  const whole = below(100);
  const frac = String(below(100)).padStart(2, '0');
  return `${whole}.${frac}`;
}

const BINOPS = ['+', '-', '*', '/', '//', '%', '**'];

function operand(depth) {
  const r = below(10);
  if (depth > 0 && r < 3) return '(' + build(depth - 1) + ')';
  if (r < 5) return '-' + (depth > 0 && r === 3 ? '(' + build(depth - 1) + ')' : literal());
  return literal();
}

function build(depth) {
  let out = operand(depth);
  const terms = 1 + below(3);
  for (let k = 0; k < terms; k++) {
    const op = BINOPS[below(BINOPS.length)];
    // A float raised to a float power is a complex number in Python when the
    // base is negative, so exponents are kept to small whole numbers where the
    // two languages agree on what the answer even is.
    const rhs = op === '**' ? String(below(4)) + '.0' : operand(depth);
    out += ` ${op} ${rhs}`;
  }
  return out;
}

// ---- run ----------------------------------------------------------------
function main() {
  const root = process.argv[2];
  if (!root) { console.error('usage: mathfuzz.js <repo-root> [iterations]'); process.exit(2); }
  const iterations = Number(process.argv[3] || ITERATIONS_DEFAULT);
  const python = process.env.GROUNDCHECK_PYTHON || 'python3';

  const cases = [];
  let skipped = 0;
  for (let n = 0; n < iterations; n++) {
    const src = build(2);
    const v = evaluate(src);
    // Anything the second parser cannot turn into a finite number is dropped:
    // that is a division by zero, an overflow, or a negative base under a
    // fractional exponent, and Python answers those with an exception rather
    // than a value. The agreement being tested is over expressions that have
    // one.
    if (v === null || !Number.isFinite(v)) { skipped++; continue; }
    cases.push(src + '\t' + String(v));
  }
  if (cases.length === 0) { console.log('  generated no evaluable expressions'); process.exit(1); }

  // The real tool, asked whether its own answer is the answer computed here.
  const driver = [
    'import sys',
    'sys.path.insert(0, sys.argv[1])',
    'from src.groundcheck import verify',
    'for line in sys.stdin:',
    '    expr, claimed = line.rstrip("\\n").split("\\t")',
    '    v = verify.math_holds(expr, float(claimed))',
    '    sys.stdout.write(v.status + "\\t" + v.evidence + "\\n")',
  ].join('\n');

  const proc = spawnSync(python, ['-c', driver, root], {
    input: cases.join('\n') + '\n',
    encoding: 'utf8',
    maxBuffer: 1 << 28,
  });
  if (proc.status !== 0) {
    console.log('  check_math failed on the generated expressions:');
    console.log((proc.stderr || '').trim().split('\n').slice(-6).join('\n'));
    process.exit(1);
  }

  const replies = proc.stdout.split('\n').filter((s) => s.length > 0);
  if (replies.length !== cases.length) {
    console.log(`  sent ${cases.length} expressions, check_math answered ${replies.length}`);
    process.exit(1);
  }

  let bad = 0;
  let worst = 0;
  for (let n = 0; n < replies.length; n++) {
    const [src, claimed] = cases[n].split('\t');
    const [status, evidence] = replies[n].split('\t');
    const mine = Number(claimed);
    const theirs = Number(evidence);
    if (Number.isFinite(theirs)) {
      const d = Math.abs(theirs - mine) / Math.max(1.0, Math.abs(theirs));
      if (d > worst) worst = d;
    }
    if (status !== 'checked') {
      if (bad < 5) console.log(`  ${src}: node says ${mine}, check_math says ${evidence} (${status})`);
      bad++;
    }
  }
  if (bad > 0) {
    console.log(`Node and check_math disagree on ${bad} of ${replies.length} random expressions`);
    process.exit(1);
  }
  console.log(`Node agrees with check_math on all ${replies.length} random expressions ` +
    `(${skipped} of ${iterations} had no finite value and were dropped), ` +
    `worst value disagreement ${worst.toExponential(1)} relative`);
}

main();
