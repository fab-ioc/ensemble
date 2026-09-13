// The code highlighter of the Ensemble pages. fileview.html and index.html
// both load it (<script src="/static/hl.js">), so a file in the Workspace and
// a diff in the Changes tab are coloured by the same code. It defines one
// global, HL; the colours are each page's --code-* tokens and .tk-* rules.
// ---- Code highlighting: begin ---------------------------------------------
// A highlighter of this page's own, so a file is coloured wherever the hub is
// read from, CDN or not. Each language is a list of regular expressions tried
// left to right; a match is a token, and what matches nothing is plain text.
// It is not a parser and need not be one: it has to make a file easy to read,
// on a 500 KB file in well under a second. Tokens are [class, text] pairs whose
// texts add up to the file exactly, so the comment layer's offsets, a copy and
// a selection all see the file as written.
const HL = (() => {
  const EOF = String.raw`(?![\s\S])`;
  const DQ = String.raw`"(?:[^"\\\n]|\\.)*"?`;
  const SQ = String.raw`'(?:[^'\\\n]|\\.)*'?`;
  const NUM = String.raw`\b(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?[a-zA-Z]*)\b`;
  const C_LINE = String.raw`\/\/.*`, C_BLOCK = String.raw`\/\*[\s\S]*?(?:\*\/|${EOF})`;
  const W = s => new Set(s.split(/\s+/));
  const LANGS = {};
  const lastSig = s => { for (let i = s.length - 1; i >= 0; i--) { const c = s[i]; if (c !== ' ' && c !== '\t' && c !== '\n' && c !== '\r') return c; } return ''; };
  const nextCh = (t, i) => { while (t[i] === ' ' || t[i] === '\t') i++; return t[i]; };

  // An identifier: a keyword, a literal, the name a def or a class introduces,
  // a builtin, a CONSTANT, a Type, or a call. A name after a dot is a property.
  function word(d) {
    return (s, st, end, start) => {
      const k = d.ci ? s.toLowerCase() : s, prev = st.prev;
      st.prev = k;
      st.sig = 'a';
      if (st.text[start - 1] === '.') return d.calls !== false && nextCh(st.text, end) === '(' ? 'fn' : '';
      if (d.kw.has(k)) { if (d.sigAfter && d.sigAfter.has(k)) st.sig = '('; return 'kw'; }
      if (d.lit && d.lit.has(k)) return 'num';
      if (d.defFn && d.defFn.has(prev)) return 'fn';
      if (d.defTy && d.defTy.has(prev)) return 'ty';
      if (d.bi && d.bi.has(k)) return 'bi';
      if (d.caps !== false && /^[A-Z]/.test(s)) return s.length > 1 && /^[A-Z][A-Z0-9_]*$/.test(s) ? 'num' : 'ty';
      if (d.calls !== false && nextCh(st.text, end) === '(') return 'fn';
      return '';
    };
  }
  function scan(text, lang, out) {
    const d = LANGS[lang];
    if (!d) { out.push('', text); return; }
    if (d.embed) { d.embed(text, out); return; }
    if (!d.re) d.re = new RegExp(d.rules.map(r => '(' + r[0] + ')').join('|'), 'gm');
    const re = d.re, rules = d.rules;
    const st = { text, prev: '', sig: '(', depth: 0, inTag: false };
    let pos = 0, m;
    const gap = g => { out.push('', g); const c = lastSig(g); if (c) { st.sig = c; st.prev = ''; } };
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      let s = m[0];
      const start = m.index;
      if (!s) { re.lastIndex++; continue; }
      let i = 1;
      while (m[i] === undefined) i++;
      const rule = rules[i - 1];
      if (start > pos) gap(text.slice(pos, start));
      if (!rule[2]) st.prev = '';
      let cls = rule[1];
      if (typeof cls === 'function') {
        cls = cls(s, st, start + s.length, start);
        // [class, length]: the rule took less than it matched ("/" that is a
        // division, not a regex); scanning resumes right after what it took.
        if (Array.isArray(cls)) { s = s.slice(0, cls[1]); cls = cls[0]; re.lastIndex = start + s.length; }
      }
      if (!rule[2] && cls !== 'com') st.sig = cls ? 'a' : lastSig(s);
      out.push(cls || '', s);
      pos = start + s.length;
    }
    if (pos < text.length) gap(text.slice(pos));
  }
  const lang = (names, d) => { names.forEach(n => { LANGS[n] = d; }); return d; };

  const py = lang(['python'], {
    kw: W('and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case'),
    lit: W('True False None'),
    bi: W('self cls print len range str int float bool bytes dict list set frozenset tuple type isinstance issubclass super open enumerate zip map filter sorted reversed min max sum abs any all getattr setattr hasattr iter next repr round object'),
    defFn: W('def'), defTy: W('class'),
  });
  py.rules = [
    [String.raw`#.*`, 'com'],
    [String.raw`[rRbBuUfF]{0,2}"""[\s\S]*?(?:"""|${EOF})`, 'str'],
    [String.raw`[rRbBuUfF]{0,2}'''[\s\S]*?(?:'''|${EOF})`, 'str'],
    [String.raw`[rRbBuUfF]{0,2}` + DQ, 'str'],
    [String.raw`[rRbBuUfF]{0,2}` + SQ, 'str'],
    [String.raw`^[ \t]*@[A-Za-z_][\w.]*`, 'meta'],
    [NUM, 'num'],
    [String.raw`[A-Za-z_]\w*`, word(py), true],
  ];

  const JS_KW = 'break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new of return super switch this throw try typeof var void while with yield async await static get set from as';
  const jsRules = d => [
    [String.raw`^#!.*`, 'meta'],
    [C_LINE, 'com'], [C_BLOCK, 'com'],
    [String.raw`\x60(?:[^\x60\\]|\\[\s\S])*(?:\x60|${EOF})`, 'str'],
    [DQ, 'str'], [SQ, 'str'],
    // A regex literal, where an expression may start; otherwise "/" divides.
    [String.raw`\/(?![*/])(?:[^/\\\n\[]|\\.|\[(?:[^\]\\\n]|\\.)*\])+\/[dgimsuyv]*`,
      (s, st) => /^$|[(,=:[!&|?{};+\-*%<>~^]$/.test(st.sig) ? 'str' : [null, 1]],
    [String.raw`@[A-Za-z_]\w*`, 'meta'],
    [NUM, 'num'],
    [String.raw`[A-Za-z_$][\w$]*`, word(d), true],
  ];
  const JS_LIT = 'true false null undefined NaN Infinity';
  const JS_BI = 'console window document globalThis Math JSON Object Array String Number Boolean Symbol Promise Map Set WeakMap Date RegExp Error fetch require module exports process';
  const JS_SIG = W('return typeof instanceof in of new delete void throw case do else yield await');
  const js = lang(['javascript'], { kw: W(JS_KW), lit: W(JS_LIT), bi: W(JS_BI), defFn: W('function'), defTy: W('class extends new'), sigAfter: JS_SIG });
  js.rules = jsRules(js);
  const ts = lang(['typescript'], {
    kw: W(JS_KW + ' interface type enum implements declare readonly private public protected abstract namespace keyof infer is satisfies override'),
    lit: W(JS_LIT), bi: W(JS_BI + ' string number boolean any unknown never object bigint'),
    defFn: W('function'), defTy: W('class extends new interface type enum implements'), sigAfter: JS_SIG,
  });
  ts.rules = jsRules(ts);

  const java = lang(['java'], {
    kw: W('abstract assert break case catch class continue default do else enum extends final finally for if implements import instanceof interface native new package private protected public return static strictfp super switch synchronized this throw throws transient try volatile while var record sealed permits yield boolean byte char double float int long short void'),
    lit: W('true false null'), defTy: W('class interface enum record extends implements new'),
  });
  const clikeRules = d => [
    [String.raw`^[ \t]*#[ \t]*[A-Za-z]\w*.*|#!?\[[^\]\n]*\]`, 'meta'],
    [C_LINE, 'com'], [C_BLOCK, 'com'],
    [String.raw`"""[\s\S]*?(?:"""|${EOF})`, 'str'],
    [String.raw`\x60[^\x60]*(?:\x60|${EOF})`, 'str'],
    [DQ, 'str'], [String.raw`'(?:[^'\\\n]|\\.){1,8}'`, 'str'],
    [String.raw`@[A-Za-z_]\w*`, 'meta'],
    [NUM, 'num'],
    [String.raw`[A-Za-z_$][\w$]*`, word(d), true],
  ];
  java.rules = clikeRules(java);
  const clike = lang(['clike'], {
    kw: W('auto break case char const continue default do double else enum extern float for goto if inline int long register return short signed sizeof static struct switch typedef union unsigned void volatile while bool class namespace template typename using virtual public private protected internal new delete this throw try catch finally operator override final package import type var func go chan select defer map range interface fn let mut impl trait pub use mod crate self match loop where as async await dyn move ref unsafe fun val when object companion data sealed is in out init guard extension protocol string byte rune uint uint8 int32 int64 float64 usize isize f64 i32 u8'),
    lit: W('true false null nil None NULL nullptr'), defFn: W('func fn fun def'), defTy: W('class struct enum interface trait impl type record'),
  });
  clike.rules = clikeRules(clike);

  lang(['json'], { rules: [
    [C_LINE, 'com'], [C_BLOCK, 'com'],
    [String.raw`"(?:[^"\\\n]|\\.)*"(?=[ \t]*:)`, 'attr'],
    [DQ, 'str'],
    [String.raw`-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b`, 'num'],
    [String.raw`\b(?:true|false|null)\b`, 'num'],
  ] });

  const cssRules = scss => [
    [C_BLOCK, 'com'],
    ...(scss ? [[String.raw`(?<![:\w])\/\/.*`, 'com']] : []),
    [DQ, 'str'], [SQ, 'str'],
    [String.raw`@[\w-]+`, 'kw'],
    [String.raw`--[\w-]+`, 'attr'],
    [String.raw`[{}]`, (s, st) => { st.depth = Math.max(0, st.depth + (s === '{' ? 1 : -1)); return ''; }],
    [String.raw`#[\w-]+`, (s, st) => st.depth > 0 ? (/^#[\da-fA-F]{3,8}$/.test(s) ? 'num' : '') : 'ty'],
    [String.raw`-?(?:\d+\.?\d*|\.\d+)(?:%|[a-zA-Z]+)?`, (s, st, end, start) => /[\w-]/.test(st.text[start - 1] || '') ? [null, s.length] : 'num'],
    [String.raw`\.[A-Za-z_-][\w-]*`, (s, st) => st.depth > 0 ? '' : 'ty'],
    [String.raw`::?[A-Za-z-]+`, (s, st) => st.depth > 0 ? [null, 1] : 'kw'],
    [String.raw`!important\b`, 'kw'],
    [String.raw`[A-Za-z-][\w-]*(?=\()`, 'fn'],
    [String.raw`[A-Za-z-][\w-]*(?=[ \t]*:(?!:))`, (s, st) => st.depth > 0 ? 'attr' : 'tag'],
    [String.raw`[A-Za-z][\w-]*`, (s, st) => st.depth > 0 ? '' : 'tag'],
  ];
  lang(['css'], { rules: cssRules(false) });
  lang(['scss'], { rules: cssRules(true) });

  lang(['xml'], { rules: [
    [String.raw`<!--[\s\S]*?(?:-->|${EOF})`, 'com'],
    [String.raw`<!\[CDATA\[[\s\S]*?(?:\]\]>|${EOF})`, 'str'],
    [String.raw`<[!?][^>]*>?`, 'meta'],
    [String.raw`<\/?[A-Za-z][\w:.-]*`, (s, st) => { st.inTag = true; return 'tag'; }],
    [String.raw`\/?>`, (s, st) => st.inTag ? (st.inTag = false, 'tag') : [null, s.length]],
    [String.raw`[A-Za-z_:@][\w:.@-]*`, (s, st) => st.inTag ? 'attr' : ''],
    [String.raw`"[^"]*"?|'[^']*'?`, (s, st) => st.inTag ? 'str' : [null, 1]],
    [String.raw`&(?:#\d+|#x[\da-fA-F]+|[A-Za-z]\w*);`, 'num'],
  ] });
  // A page: markup, with its scripts and styles in their own languages.
  lang(['html'], { embed(text, out) {
    const open = /<(script|style)\b[^>]*>/gi;
    let pos = 0, m;
    while ((m = open.exec(text))) {
      const bodyStart = m.index + m[0].length;
      const close = new RegExp('</' + m[1] + '\\s*>', 'ig');
      close.lastIndex = bodyStart;
      const c = close.exec(text), bodyEnd = c ? c.index : text.length;
      scan(text.slice(pos, bodyStart), 'xml', out);
      const type = ((m[0].match(/\btype\s*=\s*["']?([^"'\s>]+)/i) || [])[1] || '').toLowerCase();
      const sub = m[1].toLowerCase() === 'style' ? 'css'
        : /json/.test(type) ? 'json' : (!type || /module|javascript|ecmascript/.test(type)) ? 'javascript' : '';
      if (bodyEnd > bodyStart) { if (sub) scan(text.slice(bodyStart, bodyEnd), sub, out); else out.push('', text.slice(bodyStart, bodyEnd)); }
      pos = bodyEnd;
      open.lastIndex = bodyEnd;
    }
    if (pos < text.length) scan(text.slice(pos), 'xml', out);
  } });

  // Blanks around a key or a value are counted to at most 64. An open-ended run
  // in a lookbehind or lookahead is walked again from every position of a long
  // run of blanks, which is quadratic: one line of 50,000 spaces took seconds.
  const B0 = String.raw`[ \t]{0,64}`, B1 = String.raw`[ \t]{1,64}`;
  const AFTER_VALUE = String.raw`(?<=:${B1}|^${B0}-${B1}|[\[,]${B0})`;
  lang(['yaml'], { rules: [
    [String.raw`(?:^|(?<=[ \t]))#.*`, 'com'],
    [String.raw`^(?:---|\.\.\.)(?=[ \t]|$)`, 'meta'],
    [String.raw`^[ \t]*-(?=[ \t]|$)`, 'kw'],
    [String.raw`(?<=^${B0}(?:-${B1})?)(?:"(?:[^"\\\n]|\\.)*"|'[^'\n]*'|[^\s#'"{}\[\],&*!|>%@\x60-][^#\n]*?)(?=${B0}:(?:[ \t]|$))`, 'attr'],
    [DQ, 'str'], [SQ, 'str'],
    [String.raw`(?<=^|[\s\[,])[&*][A-Za-z_][\w-]*`, 'meta'],
    [String.raw`(?<=^|[\s\[,])!{1,2}[\w/.-]*`, 'meta'],
    [AFTER_VALUE + String.raw`(?:true|false|yes|no|on|off|null|True|False|Null|~)(?=${B0}(?:[,\]#]|$))`, 'num'],
    [AFTER_VALUE + String.raw`[-+]?(?:0x[\da-fA-F]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)(?=${B0}(?:[,\]#]|$))`, 'num'],
    [String.raw`(?<=:${B1})[|>][-+]?\d*(?=${B0}(?:#.*)?$)`, 'kw'],
  ] });
  lang(['toml', 'ini'], { rules: [
    [String.raw`(?:^|(?<=[ \t]))[#;].*`, 'com'],
    [String.raw`^[ \t]*\[\[?[^\]\n]*\]\]?`, 'ty'],
    [String.raw`(?<=^${B0})[^\s=#;\[][^=\n]*?(?=${B0}=)`, 'attr'],
    [String.raw`"""[\s\S]*?(?:"""|${EOF})`, 'str'], [String.raw`'''[\s\S]*?(?:'''|${EOF})`, 'str'],
    [DQ, 'str'], [String.raw`'[^'\n]*'?`, 'str'],
    [String.raw`\b\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)?)?\b`, 'num'],
    [String.raw`(?<=[=\[,]${B0})(?:true|false|on|off|yes|no)\b`, 'num'],
    [String.raw`[-+]?\b(?:0x[\da-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)\b`, 'num'],
  ] });

  const sh = lang(['bash'], {
    kw: W('if then else elif fi for while until do done case esac in function return exit local export readonly declare typeset set unset shift break continue source alias trap eval exec select time'),
    bi: W('echo printf cd pwd read test cat grep sed awk git mkdir rm cp mv ls ln chmod chown sudo curl wget tar ssh launchctl brew npm npx node py python python3 pip kill sleep touch find xargs head tail sort uniq wc tr cut env which true false'),
    caps: false, calls: false,
  });
  sh.rules = [
    [String.raw`^#!.*`, 'meta'],
    [String.raw`(?:^|(?<=[\s;|&(]))#.*`, 'com'],
    [String.raw`\$\{[^}\n]*\}|\$[A-Za-z_]\w*|\$[@#?$!*\d-]`, 'attr'],
    [DQ, 'str'],
    [String.raw`'[^']*(?:'|${EOF})`, 'str'],
    [String.raw`[A-Za-z_]\w*(?==)`, 'attr'],
    [String.raw`(?<![\w-])--?[A-Za-z][\w-]*`, 'meta'],
    [String.raw`\b\d+\b`, 'num'],
    [String.raw`[A-Za-z_][\w.-]*`, word(sh), true],
  ];
  const ps = lang(['powershell'], {
    ci: true, caps: false,
    kw: W('begin break catch class continue data default do dynamicparam else elseif end enum exit filter finally for foreach from function if in param process return switch throw trap try until using var while workflow'),
    defFn: W('function filter'),
  });
  ps.rules = [
    [String.raw`<#[\s\S]*?(?:#>|${EOF})`, 'com'],
    [String.raw`#.*`, 'com'],
    [String.raw`@"[\s\S]*?(?:^"@|${EOF})`, 'str'], [String.raw`@'[\s\S]*?(?:^'@|${EOF})`, 'str'],
    [String.raw`"(?:[^"\x60\n]|\x60.|"")*"?`, 'str'], [String.raw`'(?:[^'\n]|'')*'?`, 'str'],
    [String.raw`\$(?:true|false|null|True|False|Null|TRUE|FALSE|NULL)\b`, 'num'],
    [String.raw`\$(?:\{[^}\n]*\}|[\w:?]+)`, 'attr'],
    [String.raw`\[[A-Za-z][\w.]*(?:\[\])?\]`, 'ty'],
    [String.raw`(?<![\w-])-[A-Za-z]\w*`, 'meta'],
    [NUM, 'num'],
    [String.raw`[A-Za-z_]\w*-[A-Za-z]\w*`, 'fn'],
    [String.raw`[A-Za-z_]\w*`, word(ps), true],
  ];
  const sql = lang(['sql'], {
    ci: true, caps: false,
    kw: W('select from where insert into values update set delete create table index join left right inner outer full cross on group by order having limit offset as and or not null is in exists union all distinct primary key foreign references default drop alter add column view begin commit rollback transaction case when then else end like between asc desc with returning if replace trigger unique check constraint integer int text varchar real blob boolean date timestamp'),
    lit: W('true false'),
  });
  sql.rules = [
    [String.raw`--.*`, 'com'], [C_BLOCK, 'com'],
    [String.raw`'(?:[^']|'')*'?`, 'str'], [String.raw`"[^"\n]*"`, 'attr'],
    [NUM, 'num'],
    [String.raw`[A-Za-z_]\w*`, word(sql), true],
  ];

  lang(['md-inline'], { rules: [
    [String.raw`<!--[\s\S]*?(?:-->|${EOF})`, 'com'],
    [String.raw`^[ \t]{0,3}#{1,6}(?:[ \t].*)?$`, 'hd'],
    [String.raw`^[ \t]*(?:[-*_][ \t]*){3,}$`, 'meta'],
    [String.raw`^[ \t]*(?:[-*+]|\d{1,9}[.)])(?=[ \t])`, 'kw'],
    [String.raw`^[ \t]*(?:>[ \t]?)+`, 'com'],
    [String.raw`\x60+[^\x60\n]+\x60+`, 'str'],
    [String.raw`!?\[[^\[\]\n]*\]\([^)\n]*\)`, 'fn'],
    [String.raw`\*\*[^*\n]+\*\*|__[^_\n]+__`, 'strong'],
    [String.raw`(?<![\w*])\*[^*\s](?:[^*\n]*[^*\s])?\*(?![\w*])|(?<![\w_])_[^_\s](?:[^_\n]*[^_\s])?_(?![\w_])`, 'em'],
    [String.raw`\|`, 'meta'],
  ] });
  // Markdown source: its front matter and fenced blocks in their own languages.
  lang(['markdown'], { embed(text, out) {
    let from = 0;
    const fm = text.match(/^---[ \t]*\n[\s\S]*?\n---[ \t]*(?:\n|$)/);
    if (fm) { scan(fm[0], 'yaml', out); from = fm[0].length; }
    const lines = text.slice(from).split('\n');
    const nl = k => (k < lines.length - 1 ? '\n' : '');
    let md = '';
    for (let i = 0; i < lines.length; i++) {
      const m = lines[i].match(/^[ \t]*(\x60{3,}|~{3,})[ \t]*([^\s\x60]*)/);
      if (!m) { md += lines[i] + nl(i); continue; }
      if (md) { scan(md, 'md-inline', out); md = ''; }
      out.push('meta', lines[i] + nl(i));
      const close = new RegExp('^[ \\t]*' + (m[1][0] === '~' ? '~' : '\\x60') + '{' + m[1].length + ',}[ \\t]*$');
      let j = i + 1, body = '';
      while (j < lines.length && !close.test(lines[j])) { body += lines[j] + nl(j); j++; }
      const sub = langOfFence(m[2]);
      if (body) { if (sub) scan(body, sub, out); else out.push('', body); }
      if (j < lines.length) out.push('meta', lines[j] + nl(j));
      i = j;
    }
    if (md) scan(md, 'md-inline', out);
  } });

  lang(['diff'], { rules: [
    [String.raw`^(?:diff |index |--- |\+\+\+ |new file mode|deleted file mode|similarity index|rename (?:from|to) |old mode|new mode|Binary files).*|^---$`, 'dh'],
    [String.raw`^(?:commit [0-9a-f]{7,}|Author:|Date:|Merge:).*`, 'dh'],
    [String.raw`^@@.*`, 'hunk'],
    [String.raw`^\+.*`, 'ins'],
    [String.raw`^-.*`, 'del'],
  ] });
  lang(['log'], { rules: [
    [String.raw`^\[?\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d)?(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?\]?|^\[?\d\d:\d\d:\d\d(?:[.,]\d+)?\]?`, 'ts'],
    [String.raw`\b(?:ERROR|ERR|FATAL|CRITICAL|CRIT|PANIC|FAIL(?:ED|URE)?|Traceback)\b|\b[A-Z]\w*(?:Error|Exception)\b`, 'err'],
    [String.raw`\bWARN(?:ING)?\b`, 'warn'],
    [String.raw`\b(?:INFO|NOTICE)\b`, 'lv'],
    [String.raw`\b(?:DEBUG|TRACE|VERBOSE)\b`, 'com'],
    [String.raw`^[ \t]+File "[^"\n]*", line \d+.*`, 'fn'],
    [String.raw`\b(?:https?|file):\/\/[^\s"'<>]+`, 'fn'],
    [String.raw`"(?:[^"\\\n]|\\.)*"`, 'str'],
  ] });

  const EXT = {
    py: 'python', pyw: 'python', pyi: 'python',
    js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
    ts: 'typescript', tsx: 'typescript', mts: 'typescript', cts: 'typescript',
    json: 'json', jsonc: 'json', map: 'json', jsonl: 'json', ndjson: 'json', ipynb: 'json', webmanifest: 'json',
    html: 'html', htm: 'html', xhtml: 'html', vue: 'html', svelte: 'html',
    xml: 'xml', svg: 'xml', plist: 'xml', xsd: 'xml', xsl: 'xml', csproj: 'xml', iml: 'xml',
    css: 'css', scss: 'scss', sass: 'scss', less: 'scss',
    yml: 'yaml', yaml: 'yaml',
    toml: 'toml', ini: 'ini', cfg: 'ini', conf: 'ini', env: 'ini', properties: 'ini', editorconfig: 'ini',
    gitignore: 'ini', gitattributes: 'ini', dockerignore: 'ini', npmrc: 'ini',
    md: 'markdown', markdown: 'markdown', mdx: 'markdown',
    sh: 'bash', bash: 'bash', zsh: 'bash', ksh: 'bash', command: 'bash',
    ps1: 'powershell', psm1: 'powershell', psd1: 'powershell',
    java: 'java',
    c: 'clike', h: 'clike', cc: 'clike', cpp: 'clike', hpp: 'clike', cs: 'clike', go: 'clike', rs: 'clike',
    kt: 'clike', kts: 'clike', scala: 'clike', groovy: 'clike', gradle: 'clike', swift: 'clike', dart: 'clike', php: 'clike',
    sql: 'sql', diff: 'diff', patch: 'diff', log: 'log', out: 'log',
  };
  const NAMES = { dockerfile: 'bash', makefile: 'bash', 'requirements.txt': 'ini', license: '', '.env': 'ini' };
  const FENCE = { py: 'python', python3: 'python', node: 'javascript', shell: 'bash', console: 'bash', shellsession: 'bash',
    pwsh: 'powershell', ps: 'powershell', rust: 'clike', golang: 'clike', kotlin: 'clike', csharp: 'clike', 'c++': 'clike', jsonc: 'json' };
  const LABEL = { python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript', json: 'JSON', html: 'HTML', xml: 'XML',
    css: 'CSS', scss: 'SCSS', yaml: 'YAML', toml: 'TOML', ini: 'Settings', markdown: 'Markdown', bash: 'Shell',
    powershell: 'PowerShell', java: 'Java', sql: 'SQL', diff: 'Diff', log: 'Log' };
  function langOfFence(info) {
    const k = (info || '').toLowerCase();
    return FENCE[k] || (LANGS[k] && !LANGS[k].internal ? k : '') || EXT[k] || '';
  }
  LANGS['md-inline'].internal = true;
  // The language of a file: by its name, its extension, or a #! first line.
  function langOf(name, text) {
    const base = (name || '').toLowerCase(), ext = (base.match(/\.([\w-]+)$/) || [, ''])[1];
    if (base in NAMES) return NAMES[base];
    if (EXT[ext]) return EXT[ext];
    const sb = !ext && (text || '').match(/^#![ \t]*(\S+)(?:[ \t]+(\S+))?/);
    if (!sb) return '';
    const prog = /(?:^|\/)env$/.test(sb[1]) ? (sb[2] || '') : sb[1].split('/').pop();
    return /^python/.test(prog) ? 'python' : /^node/.test(prog) ? 'javascript' : /^pwsh/.test(prog) ? 'powershell' : /sh$/.test(prog) ? 'bash' : '';
  }
  const label = (lng, ext) => LABEL[lng] || (lng === 'clike' ? ext.toUpperCase() : 'Text');

  const escH = s => s.replace(/[&<>]/g, c => (c === '&' ? '&amp;' : c === '<' ? '&lt;' : '&gt;'));
  const tokens = (text, lng) => { const out = []; scan(text, lng, out); return out; };
  // Highlighted text with no line structure: a code block inside a document.
  function html(text, lng) {
    if (!LANGS[lng]) return escH(text);
    const t = tokens(text, lng);
    let h = '';
    for (let i = 0; i < t.length; i += 2) h += t[i] ? `<span class="tk-${t[i]}">${escH(t[i + 1])}</span>` : escH(t[i + 1]);
    return h;
  }
  // Each line of a text as highlighted HTML, without its newline, and the
  // class a diff line takes: an insertion, a deletion or a hunk header. A text
  // ending in a newline has no empty line after it.
  const ROW_CLS = { ins: 1, del: 1, hunk: 1 };
  function lineParts(text, lng) {
    const t = tokens(text, lng), html = [], cls = [];
    let cur = '', rowCls = '', atStart = true;
    const emit = (c, piece) => {
      if (!piece) return;
      if (atStart && ROW_CLS[c]) rowCls = c;
      atStart = false;
      cur += c ? `<span class="tk-${c}">${escH(piece)}</span>` : escH(piece);
    };
    const flush = () => { html.push(cur); cls.push(rowCls); cur = ''; rowCls = ''; atStart = true; };
    for (let i = 0; i < t.length; i += 2) {
      const c = t[i], s = t[i + 1];
      let a = 0, b;
      while ((b = s.indexOf('\n', a)) !== -1) { emit(c, s.slice(a, b)); flush(); a = b + 1; }
      emit(c, s.slice(a));
    }
    if (cur || !html.length) flush();
    return { html, cls };
  }
  // The lines of a text, highlighted: a diff shows its lines one by one.
  const lines = (text, lng) => lineParts(text, lng).html;
  // A file: one row per line, each carrying its number. A diff row whose
  // line is an insertion, a deletion or a hunk header takes that as its class.
  const CHUNK = 500;
  function rows(text, lng) {
    const p = lineParts(text, lng), parts = [], n = p.html.length, endNl = text.endsWith('\n');
    for (let i = 0; i < n; i++) {
      const nl = i < n - 1 || endNl;
      parts.push(`<div class="ln-row${p.cls[i] ? ' r-' + p.cls[i] : ''}" data-n="${i + 1}">${p.html[i]}${nl ? '\n' : ''}</div>`);
    }
    // Long files come in chunks the browser may skip laying out until they
    // scroll near, so a 10,000-line file opens as fast as a short one.
    if (parts.length <= CHUNK * 2) return { html: parts.join(''), lines: parts.length };
    let h = '';
    for (let i = 0; i < parts.length; i += CHUNK) h += '<div class="ln-chunk">' + parts.slice(i, i + CHUNK).join('') + '</div>';
    return { html: h, lines: parts.length };
  }
  return { rows, lines, html, tokens, langOf, langOfFence, label, has: l => !!LANGS[l] };
})();
// ---- Code highlighting: end -----------------------------------------------
