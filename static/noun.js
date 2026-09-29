// The word the pages use for a board: "project" unless this hub's setting
// (projectNoun in /api/settings, chosen at install and in Settings) says
// "initiative", "epic"… One word per hub, so no screen mixes two names. Only
// what a person reads uses it: routes, JSON keys, file names and what agents
// say among themselves keep "project".
//
//   noun('one'|'many', 'lower'|'title')   the word: project, Projects…
//   nounText('New {project}')             a text with the word in it:
//        {project} {Project} {projects} {Projects}, and {a project} {A project}
//        for the article that goes with it (an initiative, an epic).
//   applyNouns(root)                      fills what the markup marks:
//        data-noun="text with {project}", and data-noun-title,
//        data-noun-aria-label, data-noun-placeholder for those attributes.
//
// The word is kept in this browser too, so a page paints with it before the
// hub answers; a change reaches the pages on their next load. Runs in Node
// too (the tests), where it is the default word unless set.
(function (root) {
'use strict';

const DEFAULT = { one: 'Project', many: 'Projects' };
const KEY = 'cd-noun';

function clean(v) {
  if (!v || typeof v !== 'object') return null;
  const out = {};
  for (const n of ['one', 'many']) {
    const w = typeof v[n] === 'string' ? v[n].split(/\s+/).filter(Boolean).join(' ') : '';
    if (!w || w.length > 40 || /[<>{}&"']/.test(w)) return null;
    out[n] = w;
  }
  return out;
}

let word = DEFAULT;
try { word = clean(JSON.parse(root.localStorage.getItem(KEY) || 'null')) || DEFAULT; } catch (e) {}

// Lower case but for a word that starts with two capitals (OKR, OKRs); title case is the
// first letter up, the rest as it was typed.
function noun(n, c) {
  const w = word[n === 'many' ? 'many' : 'one'];
  if (c === 'title') return w.charAt(0).toUpperCase() + w.slice(1);
  return w.split(' ').map(x => (x.length > 1 && x.slice(0, 2) === x.slice(0, 2).toUpperCase() && x.slice(0, 2) !== x.slice(0, 2).toLowerCase()) ? x : x.toLowerCase()).join(' ');
}
function article(w) { return /^[aeiou]/i.test(w) ? 'an' : 'a'; }
function nounText(s) {
  return String(s).replace(/\{(a |A )?([Pp])roject(s?)\}/g, (m, a, p, s) => {
    const w = noun(s ? 'many' : 'one', p === 'P' && !a ? 'title' : 'lower');
    if (!a) return w;
    const art = article(w);
    return (a === 'A ' ? art.charAt(0).toUpperCase() + art.slice(1) : art) + ' ' + w;
  });
}

const ATTRS = ['title', 'aria-label', 'placeholder'];
function applyNouns(el) {
  const doc = root.document;
  if (!doc) return;
  el = el || doc;
  if (el.querySelectorAll) {
    el.querySelectorAll('[data-noun]').forEach(x => { x.textContent = nounText(x.dataset.noun); });
    for (const a of ATTRS) el.querySelectorAll(`[data-noun-${a}]`).forEach(x => x.setAttribute(a, nounText(x.getAttribute(`data-noun-${a}`))));
  }
  const t = doc.querySelector && doc.querySelector('title[data-noun]');
  if (t) doc.title = nounText(t.dataset.noun);
}
// The hub's word, kept here for the next load. Returns whether it changed.
function setNoun(v) {
  const w = clean(v) || DEFAULT;
  const changed = w.one !== word.one || w.many !== word.many;
  word = w;
  try { root.localStorage.setItem(KEY, JSON.stringify(w)); } catch (e) {}
  return changed;
}

const api = { noun, nounText, applyNouns, setNoun, NOUN_DEFAULT: DEFAULT, cleanNoun: clean };
if (typeof module === 'object' && module.exports) module.exports = api;
Object.assign(root, api);
if (root !== globalThis) Object.assign(globalThis, api);
})(typeof window !== 'undefined' ? window : globalThis);
