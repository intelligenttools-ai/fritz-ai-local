// Fritz Local Brain — shared SSE live-update helper (#198, extracted for #220).
//
// AUTH: EventSource cannot send an Authorization header, and the long-lived
// Bearer token must NOT go in the URL (it would leak into access logs). So we
// POST the Bearer token to /v1/usage/stream-ticket (Bearer-protected) and get a
// short-lived, single-purpose ticket; that ticket — NOT the Bearer — is what
// rides in the stream URL. SSE is best-effort: the #195 polling timer keeps
// running as the backstop, so if SSE fails the page still refreshes.
//
// Each page defines window.loadAll(); a `changed` frame debounces into a single
// loadAll(). Pages with no live data set window.usesSSE = false and skip this.

let _evtSource    = null;   // active EventSource (null when off / fell back)
let _sseDebounce  = null;   // debounce handle for changed-event refreshes
let _sseRetried   = false;  // one reconnect attempt only — don't spam reconnects

/** Debounced refresh: many `changed` events collapse into a single loadAll(). */
function sseRefreshDebounced() {
  if (_sseDebounce) clearTimeout(_sseDebounce);
  _sseDebounce = setTimeout(() => {
    _sseDebounce = null;
    if (!_inFlight) loadAll();  // _inFlight guard avoids double-loads vs polling
  }, 500);
}

/** Close and forget any open EventSource (cleanup / before fallback). */
function closeSSE() {
  if (_sseDebounce) { clearTimeout(_sseDebounce); _sseDebounce = null; }
  if (_evtSource) {
    try { _evtSource.close(); } catch (_) {}
    _evtSource = null;
  }
}

/** Exchange the Bearer token for a stream ticket, then open the EventSource. */
async function setupSSE() {
  const token = getToken();
  if (!token) return;
  closeSSE();
  let ticket;
  try {
    const resp = await fetch("/v1/usage/stream-ticket", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) return; // 401/503 etc — polling remains the backstop
    ticket = (await resp.json()).ticket;
  } catch (e) {
    console.error("stream-ticket error:", e);
    return; // fall back to polling
  }
  if (!ticket) return;

  const src = new EventSource("/v1/usage/stream?ticket=" + encodeURIComponent(ticket));
  _evtSource = src;

  src.addEventListener("changed", () => sseRefreshDebounced());

  src.addEventListener("error", () => {
    // Connection dropped — close and fall back to the polling backstop.
    // Attempt exactly one reconnect (fresh ticket) with a short backoff.
    closeSSE();
    if (!_sseRetried) {
      _sseRetried = true;
      setTimeout(() => { if (getToken()) setupSSE(); }, 3000);
    }
  });

  src.addEventListener("open", () => { _sseRetried = false; });
}

// Cleanup so connections don't leak when the page goes away.
window.addEventListener("beforeunload", closeSSE);
