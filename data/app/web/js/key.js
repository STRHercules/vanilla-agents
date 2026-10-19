// The per-launch key (D-381). The launcher opens this window at /#k=<key>; the key lives in the address fragment, which the browser never sends to the server.
// It is kept for this window only (memory, and sessionStorage so a reload still works) and sent as a header on every call that changes something.
let key = '';
try {
  const m = /[#&]k=([\w-]+)/.exec(location.hash);
  if (m) { key = m[1]; try { sessionStorage.setItem('fk', key); } catch (e) { /* no storage: the key stays in memory */ } history.replaceState(null, '', location.pathname + location.search); }
  else { try { key = sessionStorage.getItem('fk') || ''; } catch (e) { key = ''; } }
} catch (e) { key = ''; }

export const postHeaders = () => ({ 'Content-Type': 'application/json', 'X-Vanilla-Key': key });
// V-040: the Phone section builds the phone link from it (shown only in this window, on request)
export const windowKey = () => key;
