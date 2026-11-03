// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only
// Minimal DOM for running the built page's script under Node.
// Three details matter, each of which has previously made a stub artefact look
// like a page bug: tagName is uppercase as in a real DOM; textContent walks
// child text nodes rather than returning only what was assigned; and setting
// innerHTML to "" actually drops the children.
let idSeq = 0;

class Node_ {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this._text = "";
    this._seq = idSeq++;
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach(x => this.classList._s.add(x)),
      remove: (...c) => c.forEach(x => this.classList._s.delete(x)),
      toggle: (c, on) => (on === undefined
        ? (this.classList._s.has(c) ? this.classList._s.delete(c)
                                    : this.classList._s.add(c))
        : (on ? this.classList._s.add(c) : this.classList._s.delete(c))),
      contains: c => this.classList._s.has(c),
    };
    this.listeners = {};
  }
  get className() { return [...this.classList._s].join(" "); }
  set className(v) {
    this.classList._s = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach(c => this.appendChild(c)); }
  removeChild(c) {
    this.children = this.children.filter(x => x !== c);
    c.parentNode = null;
    return c;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k.startsWith("data-")) {
      const key = k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      this.dataset[key] = String(v);
    }
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  removeEventListener(ev, fn) {
    this.listeners[ev] = (this.listeners[ev] || []).filter(f => f !== fn);
  }
  dispatch(ev, arg) { (this.listeners[ev] || []).forEach(f => f.call(this, arg)); }
  click() { this.dispatch("click", {preventDefault() {}, stopPropagation() {}}); }
  get textContent() {
    if (this.tagName === "#TEXT") return this._text;
    return this._text + this.children.map(c => c.textContent).join("");
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get innerHTML() { return this._html || ""; }
  set innerHTML(v) { this._html = String(v); if (v === "") this.children = []; }
  // Enough of getBoundingClientRect for layout maths that only reads width.
  getBoundingClientRect() { return {width: 900, height: 300, left: 0, top: 0}; }
  closest(sel) {
    let n = this;
    while (n) { if (match(n, sel)) return n; n = n.parentNode; }
    return null;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = n => {
      for (const c of n.children) { if (match(c, sel)) out.push(c); walk(c); }
    };
    walk(this);
    return out;
  }
}

function match(n, sel) {
  // Supports the shapes the page uses: ".cls", "tag", "[a=b]", ".cls[a=b]".
  const m = /^(?:([a-zA-Z#]+))?(?:\.([\w-]+))?(?:\[([\w-]+)=["']?([^\]"']*)["']?\])?$/.exec(sel.trim());
  if (!m) return false;
  const [, tag, cls, attr, val] = m;
  if (tag && n.tagName !== tag.toUpperCase()) return false;
  if (cls && !n.classList.contains(cls)) return false;
  if (attr && n.getAttribute(attr) !== val) return false;
  return !!(tag || cls || attr);
}

class TextNode extends Node_ {
  constructor(t) { super("#text"); this._text = String(t); }
}

const byId = new Map();
const document = {
  documentElement: new Node_("html"),
  body: new Node_("body"),
  createElement: t => new Node_(t),
  createElementNS: (_ns, t) => new Node_(t),
  createTextNode: t => new TextNode(t),
  getElementById: id => byId.get(id) || null,
  querySelector: sel => document.body.querySelector(sel),
  querySelectorAll: sel => document.body.querySelectorAll(sel),
  addEventListener: () => {},
};

/** Register the ids the page looks up, and the tabs it queries for. */
function register(el, id) { el.attrs.id = id; byId.set(id, el); return el; }

const location = {pathname: "/page.html", hash: "", href: "file:///page.html"};
const history = {replaceState(_s, _t, url) {
  location.hash = String(url || "").startsWith("#") ? String(url) : "";
}};
const windowListeners = {};
const window_ = {
  addEventListener: (ev, fn) => ((windowListeners[ev] ||= []).push(fn)),
  matchMedia: () => ({matches: false, addEventListener() {}}),
  devicePixelRatio: 1,
};
const getComputedStyle = () => ({getPropertyValue: () => ""});

module.exports = {
  document, location, history, window_, windowListeners, getComputedStyle,
  register, Node_, byId,
};
