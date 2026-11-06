'use strict';
(function (root) {
  const SECTION_NAMES = ['一眼看懂', '答辩思维逻辑图', '现场回答', '一句话收束', '依据与提醒'];
  let diagramSequence = 0, initialized = false;
  const diagramRequests = new Map();
  function strip(value) { return String(value || '').replace(/\*\*/g, '').trim(); }
  function isClarification(value) { return /^\s*(?:#{1,6}\s*)?(?:\*\*)?【需要确认】/.test(String(value || '')); }
  function hasClarification(value) { return parse(value).some(item => item.sections.some(section => section.name === '需要确认')); }
  function questionKeywords(question) {
    const words = String(question || '').replace(/为什么|怎么办|怎么|怎样|如何|分别|什么|哪些|是否|能否|以及|还有|请问|请|我们|这个|那个|的问题|的问题是|进行|需要|能够|到底|为什么要|和|与|的|是|了|吗|呢/g, '|').split(/[|\s，。？?；;：:、（）()]+/);
    return [...new Set(words.filter(word => word.length >= 2 && word.length <= 16))].sort((a, b) => b.length - a.length).slice(0, 24);
  }
  function inline(target, value, doc, terms = []) {
    const expression = terms.length ? new RegExp('(' + terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi') : null;
    String(value || '').split('**').forEach((part, index) => {
      if (!part) return;
      if (index % 2) { const strong = doc.createElement('strong'); strong.textContent = part; target.append(strong); return; }
      if (!expression) { target.append(doc.createTextNode(part)); return; }
      let at = 0;
      for (const match of part.matchAll(expression)) {
        if (match.index > at) target.append(doc.createTextNode(part.slice(at, match.index)));
        const strong = doc.createElement('strong'); strong.textContent = match[0]; target.append(strong);
        at = match.index + match[0].length;
      }
      if (at < part.length) target.append(doc.createTextNode(part.slice(at)));
    });
  }
  function parse(value, fallbackQuestion = '') {
    const questions = [];
    let current = null, section = null, fenced = false;
    for (const raw of String(value || '').replace(/\r\n?/g, '\n').split('\n')) {
      const line = raw.trim();
      const heading = line.replace(/^#{1,6}\s*/, '').replace(/^\*\*(.*?)\*\*$/, '$1');
      const question = !fenced && heading.match(/^问题\s*([\d一二三四五六七八九十]+)\s*[：:、.]\s*(.*)$/);
      if (question) {
        current = { title: strip(question[2]) || '正在概括问题…', number: question[1], explicit: true, sections: [] };
        questions.push(current); section = null; continue;
      }
      if (!current && !line) continue;
      if (!current) { current = { title: fallbackQuestion || '专家提问', number: '1', sections: [] }; questions.push(current); }
      const clarification = !fenced && heading.replace(/^\*\*(【需要确认】)\*\*/, '$1').match(/^(?:【需要确认】|\[需要确认\])\s*(.*)$/);
      if (clarification) { section = { name: '需要确认', lines: clarification[1] ? [clarification[1]] : [] }; current.sections.push(section); continue; }
      const sectionMatch = !fenced && heading.match(/^(?:【|\[)?(一眼看懂|答辩思维逻辑图|现场回答|一句话收束|依据与提醒)(?:[｜|丨·—-]不朗读)?(?:】|\])?\s*$/);
      if (sectionMatch) { section = { name: sectionMatch[1], lines: [] }; current.sections.push(section); continue; }
      if (!section) { section = { name: '', lines: [] }; current.sections.push(section); }
      section.lines.push(raw);
      if (/^```/.test(line)) fenced = !fenced;
    }
    return questions;
  }
  function conciseQuestions(value, question) {
    const found = parse(value, question).filter(item => item.explicit).map(item => item.title);
    if (found.length) return found;
    const parts = String(question || '').split(/[？?；;\n]+|(?:另外|此外|另一个问题是)/).map(part => part.trim()).filter(Boolean);
    return (parts.length ? parts : ['等待专家提问']).map(part => part.length > 100 ? part.slice(0, 99) + '…' : part).slice(0, 8);
  }
  function spokenText(value) {
    const questions = parse(value);
    const parts = questions.filter(item => !item.sections.some(section => section.name === '需要确认')).map(item => item.sections.filter(section => ['现场回答', '一句话收束'].includes(section.name)).map(section => strip(section.lines.join('\n').replace(/^#{1,6}\s*/gm, '').replace(/```[\s\S]*?(?:```|$)/g, ''))).filter(Boolean).join('\n\n')).filter(Boolean);
    if (!parts.length && questions.some(item => item.sections.some(section => section.name))) return '';
    return parts.length ? parts.join('\n\n') : strip(String(value || '').replace(/^#{1,6}\s*/gm, '').replace(/```[\s\S]*?(?:```|$)/g, ''));
  }
  function diagramData(value) {
    const fenced = String(value || '').match(/```(?:mermaid)?\s*\n([\s\S]*?)(?:```|$)/i);
    const source = (fenced ? fenced[1] : value).trim();
    const labels = [], edges = [];
    const nodePattern = /\b([A-Za-z][\w]*)\s*(?:\[|\()(?:\[|\()?\s*["']?([^\]\)\n]+?)["']?\s*(?:\]|\))(?:\]|\))?/g;
    for (const match of source.matchAll(nodePattern)) {
      const text = strip(match[2].replace(/["']$/, '').replace(/<br\s*\/?\s*>/gi, ' · '));
      if (text && !labels.some(item => item.id === match[1])) labels.push({ id: match[1], text });
    }
    const relationText = source.replace(nodePattern, '$1');
    for (const match of relationText.matchAll(/(?=(\b[A-Za-z][\w]*)\s*(?:-->|---|==>|-\.->)\s*(?:\|[^|]*\|\s*)?([A-Za-z][\w]*))/g)) {
      if (labels.some(item => item.id === match[1]) && labels.some(item => item.id === match[2]) && !edges.some(edge => edge[0] === match[1] && edge[1] === match[2])) edges.push([match[1], match[2]]);
    }
    // A line break is text formatting, never an HTML node. Only this exact tag
    // is normalized; other model HTML and all behavior/config commands are rejected.
    const inspected = source.replace(/<br\s*\/?\s*>/gi, '\n');
    const safe = /^(?:flowchart|graph)\s+(?:TD|TB|BT|LR|RL)\b/i.test(source) && source.length <= 6000 && labels.length >= 2 && labels.length <= 9
      && !/(?:%%|\bclick\b|https?:|javascript:|data:|<\/?[a-z]|\b(?:style|classDef|linkStyle|subgraph|end)\b)/i.test(inspected);
    const renderSource = source.replace(nodePattern, (whole, id, label) => whole.replace(label, wrapDiagramLabel(label)));
    return { source: renderSource, labels, edges, safe, closed: !fenced || /```\s*$/.test(String(value).trim()) };
  }
  function wrapDiagramLabel(value) {
    const lines = String(value).replace(/<br\s*\/?\s*>/gi, '\n').split('\n');
    const wrapped = [];
    for (const line of lines) {
      const tokens = strip(line).match(/\d+(?:\.\d+)?(?:%|％|万元|天|秒|℃|ppm|mg\/m3)?|[A-Za-z][A-Za-z0-9._/+-]*|[^\s]/g) || [];
      let row = '', width = 0;
      for (const token of tokens) {
        const size = [...token].reduce((sum, ch) => sum + (/[\x00-\x7f]/.test(ch) ? .6 : 1), 0);
        if (row && width + size > 14 && !/^[，。；：、）]/.test(token)) { wrapped.push(row); row = ''; width = 0; }
        row += token; width += size;
      }
      if (row) wrapped.push(row);
    }
    return wrapped.join('<br/>');
  }
  function diagramMinimumSize(viewBox) {
    const box = String(viewBox || '').trim().split(/[\s,]+/).map(Number);
    if (box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) return null;
    // Mermaid labels use 15px. Preserve at least 12px rather than shrinking
    // the whole diagram to fit a narrow answer column; its own box can scroll.
    return { width: Math.ceil(box[2] * .8), height: Math.ceil(box[3] * .8) };
  }
  function diagramFallback(target, value, doc, terms) {
    const info = diagramData(value);
    const tree = doc.createElement('div'); tree.className = 'logic-tree'; tree.setAttribute('aria-label', '答辩思维逻辑图');
    const rows = [], visited = new Set();
    function walk(id, prefix = '', branch = '') {
      const label = info.labels.find(item => item.id === id); if (!label) return;
      rows.push({ text: label.text, prefix: prefix + branch, root: !prefix && !branch });
      if (visited.has(id)) return; visited.add(id);
      const children = info.edges.filter(edge => edge[0] === id).map(edge => edge[1]);
      children.forEach((child, i) => walk(child, prefix + (branch ? (branch === '└─ ' ? '　 ' : '│ ') : ''), i === children.length - 1 ? '└─ ' : '├─ '));
    }
    for (const label of info.labels.filter(item => !info.edges.some(edge => edge[1] === item.id))) walk(label.id);
    for (const label of info.labels) if (!visited.has(label.id)) walk(label.id);
    if (!rows.length) String(value || '').split('\n').map(line => strip(line.replace(/^```.*$/, '').replace(/^(?:flowchart|graph)\s+\w+.*$/, ''))).filter(line => line && !/^(?:click|%%|style|classDef|linkStyle)\b/i.test(line)).slice(0, 7).forEach(text => rows.push({ text, prefix: '', root: false }));
    rows.forEach(item => { const row = doc.createElement('p'); row.className = item.root ? 'logic-root' : 'logic-branch'; row.append(doc.createTextNode(item.prefix)); inline(row, item.text, doc, terms); tree.append(row); });
    if (!rows.length) { const row = doc.createElement('p'); row.textContent = '正在组织回答要点…'; tree.append(row); }
    target.replaceChildren(tree);
  }
  async function renderDiagram(target, value, doc, terms, environment) {
    diagramFallback(target, value, doc, terms);
    const info = diagramData(value), mermaid = environment.mermaid;
    if (!info.safe || !info.closed || !mermaid || !environment.DOMParser) return;
    try {
      if (!initialized) {
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true, theme: 'base',
          flowchart: { htmlLabels: false, useMaxWidth: true, curve: 'basis', nodeSpacing: 18, rankSpacing: 28, padding: 10 },
          themeVariables: { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: '15px', primaryColor: '#edf4e9', primaryTextColor: '#294b3b', primaryBorderColor: '#96b59d', lineColor: '#90a493' } });
        initialized = true;
      }
      let rendering = diagramRequests.get(info.source);
      if (!rendering) {
        rendering = Promise.resolve(mermaid.render('defense-diagram-' + (++diagramSequence), info.source));
        diagramRequests.set(info.source, rendering);
        if (diagramRequests.size > 64) diagramRequests.delete(diagramRequests.keys().next().value);
      }
      const result = await rendering;
      if (target.isConnected === false) return;
      const parsed = new environment.DOMParser().parseFromString(result.svg, 'image/svg+xml');
      if (parsed.querySelector('parsererror') || parsed.documentElement.localName !== 'svg') return;
      // Only the renderer's SVG is mounted. Model HTML, events and external links never enter the page.
      parsed.querySelectorAll('script,foreignObject,a,image,iframe').forEach(item => item.remove());
      parsed.querySelectorAll('*').forEach(item => {
        for (const attribute of [...item.attributes]) {
          if (/^on/i.test(attribute.name) || (/href$/i.test(attribute.name) && !attribute.value.startsWith('#'))) item.removeAttribute(attribute.name);
        }
      });
      // Cached identical diagrams can occur in several turns. Namespace every
      // mounted SVG so markers and style selectors cannot target another turn.
      const prefix = 'defense-view-' + (++diagramSequence), identifiers = new Map();
      parsed.querySelectorAll('[id]').forEach((item, index) => identifiers.set(item.getAttribute('id'), prefix + '-' + index));
      const remap = value => {
        let result = value;
        for (const [from, to] of [...identifiers].sort((a, b) => b[0].length - a[0].length)) result = result.split('#' + from).join('#' + to);
        return result;
      };
      parsed.querySelectorAll('*').forEach(item => {
        const old = item.getAttribute('id'); if (old) item.setAttribute('id', identifiers.get(old));
        for (const attribute of [...item.attributes]) if (attribute.name !== 'id' && attribute.value.includes('#')) item.setAttribute(attribute.name, remap(attribute.value));
        if (item.localName === 'style') item.textContent = remap(item.textContent);
      });
      const svg = doc.importNode(parsed.documentElement, true); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', '答辩思维逻辑图');
      const minimum = diagramMinimumSize(svg.getAttribute('viewBox'));
      if (minimum) { svg.style.minWidth = minimum.width + 'px'; svg.style.minHeight = minimum.height + 'px'; }
      target.setAttribute('tabindex', '0');
      target.setAttribute('aria-label', '答辩思维逻辑图，窄窗口可在图内左右滚动查看完整内容');
      if (terms.length) {
        const expression = new RegExp('(' + terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
        svg.querySelectorAll('text tspan').forEach(line => {
          if (line.children.length) return;
          const text = line.textContent, pieces = []; let at = 0;
          for (const match of text.matchAll(expression)) {
            if (match.index > at) pieces.push(doc.createTextNode(text.slice(at, match.index)));
            const word = doc.createElementNS('http://www.w3.org/2000/svg', 'tspan'); word.setAttribute('fill', '#b23b35'); word.setAttribute('font-weight', '700'); word.textContent = match[0]; pieces.push(word); at = match.index + match[0].length;
          }
          if (pieces.length) { if (at < text.length) pieces.push(doc.createTextNode(text.slice(at))); line.replaceChildren(...pieces); }
        });
      }
      target.replaceChildren(svg);
    } catch (_) { /* Keep the compact tree already visible. */ }
  }
  function paragraphs(target, lines, doc, terms) {
    let pending = [], diagram = false;
    function flush() { if (!pending.length) return; const p = doc.createElement('p'); p.className = 'spoken-paragraph'; inline(p, pending.join('\n'), doc, terms); target.append(p); pending = []; }
    for (const raw of lines) {
      const line = raw.trim();
      if (/^```mermaid\b/i.test(line)) { flush(); diagram = true; continue; }
      if (diagram) { if (/^```/.test(line)) diagram = false; continue; }
      if (!line) { flush(); continue; }
      const heading = line.match(/^#{1,6}\s+(.*)$/);
      const point = /^(?:第[一二三四五六七八九十]+[，、：:]|[①②③④⑤⑥]|\d+[、.）)]|[-*]\s+)/.test(line);
      if (heading || point) { flush(); const p = doc.createElement(heading ? 'h4' : 'p'); p.className = heading ? 'spoken-heading' : 'spoken-point'; inline(p, heading ? heading[1] : line, doc, terms); target.append(p); }
      else pending.push(line);
    }
    flush();
  }
  function render(target, value, { document: doc = root.document, environment = root, question = '', id = 'current' } = {}) {
    const terms = questionKeywords(question), questions = parse(value, question), nodes = [];
    let number = 0;
    questions.forEach(item => {
      const preface = !item.explicit && questions.some(other => other.explicit);
      const index = number;
      const card = doc.createElement('section'); card.className = 'defense-question'; card.id = id + '-question-' + index;
      if (!preface) {
        number++;
        if (item.explicit || !item.sections.some(section => section.name === '需要确认')) { const heading = doc.createElement('h3'); heading.className = 'defense-question-title'; inline(heading, '问题' + number + '：' + item.title, doc, terms); card.append(heading); }
      }
      else { card.id = id + '-preface'; card.className = 'defense-preface'; }
      item.sections.forEach(section => {
        // Keep old raw history intact, but skip diagrams in the faster reading view.
        if (section.name === '答辩思维逻辑图') return;
        const box = doc.createElement('section'); box.className = 'defense-section ' + (section.name === '需要确认' ? 'defense-glance clarification-card' : section.name === '一眼看懂' ? 'defense-glance' : section.name === '依据与提醒' ? 'defense-notes' : section.name === '一句话收束' ? 'defense-closing' : '');
        if (section.name) { const title = doc.createElement('h4'); title.className = 'defense-section-title'; title.textContent = section.name + (section.name === '依据与提醒' ? '｜不朗读' : ''); box.append(title); }
        paragraphs(box, section.lines, doc, terms);
        card.append(box);
      });
      nodes.push(card);
    });
    target.replaceChildren(...nodes);
    return questions;
  }
  const api = { parse, render, inline, questionKeywords, conciseQuestions, spokenText, diagramData, diagramMinimumSize, isClarification, hasClarification, SECTION_NAMES };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DefenseRenderer = api;
})(typeof window !== 'undefined' ? window : globalThis);
