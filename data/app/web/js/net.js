// Talks to the local server: one snapshot at start, then Server-Sent Events. The browser never polls.
import { postHeaders } from './key.js';
async function getJson(url, opts) {
  const r = await fetch(url, opts);
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || 'request failed');
  return d;
}

export function connect({ onState, onEvents, onOnline }) {
  let es = null;
  async function start() {
    if (es) es.close();
    const s = await getJson('/api/state');
    onState(s);
    es = new EventSource('/api/events?cursor=' + s.cursor);
    es.onopen = () => onOnline(true);
    es.onerror = () => onOnline(false);   // the browser reconnects by itself and resumes from the last id
    es.addEventListener('update', (e) => {
      const d = JSON.parse(e.data);
      onState(d.state);
      onEvents(d.events);
    });
    es.addEventListener('reset', () => start());
  }
  return { start, post: (path, body) => getJson(path, { method: 'POST', headers: postHeaders(), body: JSON.stringify(body) }), projects: () => getJson('/api/projects'), get: (path) => getJson(path),
    openProject: (path) => getJson('/api/project', { method: 'POST', headers: postHeaders(), body: JSON.stringify({ path }) }) };
}
