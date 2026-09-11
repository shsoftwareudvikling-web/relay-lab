import { useEffect, useMemo, useState } from "react";

type Status = "queued" | "processing" | "retrying" | "delivered" | "dead_letter";
type Receiver = "healthy" | "transient_failure" | "invalid_signature";
type Attempt = { attempt_no: number; worker: string; started_at: number; finished_at: number; outcome: string; http_status: number; detail: string };
type Delivery = { id: string; idempotencyKey: string; receiver: Receiver; payload: Record<string, unknown>; payloadText: string; payloadHash: string; signature: string; signatureValid: boolean; status: Status; attemptsCount: number; maxAttempts: number; nextAttemptAt: number; createdAt: number; updatedAt: number; leaseOwner: string | null; leaseExpiresAt: number | null; attempts: Attempt[] };
type Snapshot = { clock: number; clockMode: "simulated"; backoffSeconds: number[]; deliverySemantics: string; counts: Record<Status, number>; deliveries: Delivery[] };
type Filter = "all" | Status;

const labels: Record<Status, string> = { queued: "Queued", processing: "Processing", retrying: "Retrying", delivered: "Delivered", dead_letter: "Dead letter" };
const receivers: { value: Receiver; label: string; note: string }[] = [
  { value: "healthy", label: "Healthy", note: "Accepts a valid signature" },
  { value: "transient_failure", label: "Transient fault", note: "Returns 503 once" },
  { value: "invalid_signature", label: "Tamper probe", note: "Modifies payload before verify" },
];

async function api<T>(path: string, body?: object, key?: string): Promise<T> {
  const response = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) }, body: JSON.stringify(body) } : undefined);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? data.error ?? "Request failed");
  return data;
}

const time = (value: number) => new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" }).format(new Date(value * 1000));
const compact = (value: string, size = 12) => `${value.slice(0, size)}…${value.slice(-6)}`;

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [receiver, setReceiver] = useState<Receiver>("healthy");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    const data = await api<Snapshot>("/api/snapshot");
    setSnapshot(data);
    setSelectedId(current => data.deliveries.some(item => item.id === current) ? current : (data.deliveries[0]?.id ?? ""));
  };
  useEffect(() => { refresh().catch(err => setError(err.message)); }, []);

  const act = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label); setError(""); setNotice("");
    try { await action(); await refresh(); setNotice(label); }
    catch (err) { setError(err instanceof Error ? err.message : "Request failed"); }
    finally { setBusy(""); }
  };

  const visible = useMemo(() => snapshot?.deliveries.filter(item => filter === "all" || item.status === filter) ?? [], [snapshot, filter]);
  const selected = snapshot?.deliveries.find(item => item.id === selectedId) ?? null;
  const createProbe = () => {
    if (!snapshot) return Promise.resolve();
    const key = `probe-${Math.floor(snapshot.clock)}-${receiver}-${snapshot.deliveries.length}`;
    return api("/api/deliveries", { receiver, payload: { event: "lab.probe", source: "operator", sequence: snapshot.deliveries.length + 1 } }, key);
  };

  if (!snapshot) return <main className="boot"><p className="eyebrow">RELAYLAB / OUTBOX_01</p><h1>Opening the delivery ledger…</h1>{error && <p role="alert">{error}</p>}</main>;

  return <div className="shell">
    <header className="masthead">
      <a className="brand" href="#top" aria-label="RelayLab home"><span>RL</span><b>RelayLab</b></a>
      <div className="clock"><span className="live-dot" aria-hidden="true" /><div><small>SIMULATED UTC</small><strong>{time(snapshot.clock)}</strong></div></div>
      <p className="semantics">Delivery contract <strong>at least once</strong><br />Receiver writes one idempotent receipt.</p>
    </header>

    <main id="top">
      <section className="intro" aria-labelledby="title">
        <div><p className="eyebrow">WEBHOOK DELIVERY LABORATORY · LOCAL ONLY</p><h1 id="title">Observe the failure.<br /><em>Prove the recovery.</em></h1></div>
        <p className="lede">A working SQLite outbox. Create a controlled fault, inspect its signed payload, advance deterministic time, and watch the retry policy resolve—or exhaust—the delivery.</p>
      </section>

      <section className="metrics" aria-label="Queue totals">
        {(["queued", "retrying", "delivered", "dead_letter"] as Status[]).map((status, index) => <div className="metric" key={status}><span>0{index + 1}</span><strong>{snapshot.counts[status]}</strong><small>{labels[status]}</small></div>)}
      </section>

      <section className="control-strip" aria-label="Lab controls">
        <label><span>FAULT PROFILE</span><select value={receiver} onChange={event => setReceiver(event.target.value as Receiver)}>{receivers.map(item => <option value={item.value} key={item.value}>{item.label} — {item.note}</option>)}</select></label>
        <button className="primary" disabled={!!busy} onClick={() => act("Probe queued", createProbe)}>Queue probe <span>↗</span></button>
        <button disabled={!!busy} onClick={() => act("Worker ran one claim", () => api("/api/worker/run", { worker: "console-worker" }))}>Run worker <span>→</span></button>
        <button disabled={!!busy} onClick={() => act("Clock advanced 10 seconds", () => api("/api/clock/advance", { seconds: 10 }))}>Advance +10s</button>
      </section>
      <div className="feedback" aria-live="polite">{busy ? `Working: ${busy}…` : error ? <span className="error">{error}</span> : notice || "Ready for an operator action."}</div>

      <section className="workspace">
        <div className="ledger">
          <div className="section-head"><div><p className="eyebrow">01 / DELIVERY LEDGER</p><h2>Queue state</h2></div><div className="filters" aria-label="Filter deliveries">{(["all", "queued", "retrying", "delivered", "dead_letter"] as Filter[]).map(item => <button className={filter === item ? "active" : ""} aria-pressed={filter === item} onClick={() => setFilter(item)} key={item}>{item === "all" ? "All" : labels[item]}</button>)}</div></div>
          <div className="table-wrap"><table><thead><tr><th>Delivery / event</th><th>Receiver</th><th>State</th><th>Attempt</th><th>Due UTC</th></tr></thead><tbody>{visible.map(item => <tr key={item.id} className={selectedId === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><td><button className="row-pick" onClick={() => setSelectedId(item.id)}><strong>{item.payload.event as string}</strong><small>{item.id}</small></button></td><td><span className="receiver-name">{item.receiver.replaceAll("_", " ")}</span></td><td><span className={`status ${item.status}`}>{labels[item.status]}</span></td><td>{item.attemptsCount} / {item.maxAttempts}</td><td>{time(item.nextAttemptAt)}</td></tr>)}</tbody></table>{!visible.length && <p className="empty">No deliveries match this filter.</p>}</div>
        </div>

        <aside className="inspector" aria-label="Selected delivery inspector">
          {selected ? <><div className="section-head"><div><p className="eyebrow">02 / ENVELOPE</p><h2>Signed evidence</h2></div><span className={`verify ${selected.signatureValid ? "valid" : "invalid"}`}>{selected.signatureValid ? "✓ VALID HMAC" : "× INVALID HMAC"}</span></div>
            <dl className="facts"><div><dt>Delivery ID</dt><dd>{selected.id}</dd></div><div><dt>Idempotency key</dt><dd>{selected.idempotencyKey}</dd></div><div><dt>SHA-256 payload</dt><dd title={selected.payloadHash}>{compact(selected.payloadHash)}</dd></div><div><dt>HMAC signature</dt><dd title={selected.signature}>{compact(selected.signature)}</dd></div></dl>
            <div className="payload"><div><span>CANONICAL JSON</span><button onClick={() => navigator.clipboard.writeText(selected.payloadText)}>Copy</button></div><pre>{JSON.stringify(selected.payload, null, 2)}</pre></div>
            <button className="replay" disabled={!!busy || !["delivered", "dead_letter"].includes(selected.status)} onClick={() => act("Delivery replay queued", () => api(`/api/deliveries/${selected.id}/replay`, {}))}>Replay selected <span>↻</span></button>
          </> : <p>Select a delivery to inspect its envelope.</p>}
        </aside>
      </section>

      {selected && <section className="attempt-panel">
        <div className="section-head"><div><p className="eyebrow">03 / ATTEMPT HISTORY</p><h2>Immutable delivery record</h2></div><p>Newest attempt first · total {selected.attempts.length}</p></div>
        <ol className="timeline">{selected.attempts.length ? selected.attempts.map(attempt => <li key={attempt.attempt_no}><div className={`node ${attempt.http_status < 300 ? "ok" : "fail"}`}>{String(attempt.attempt_no).padStart(2, "0")}</div><div className="attempt-main"><div><strong>{attempt.outcome.replaceAll("_", " ")}</strong><span>HTTP {attempt.http_status}</span></div><p>{attempt.detail}</p><small>{time(attempt.finished_at)} UTC · {attempt.worker}</small></div></li>) : <li className="no-attempts">No attempt yet. Run the worker to claim the next due delivery.</li>}</ol>
      </section>}
    </main>
    <footer><span>RELAYLAB / LOCAL CONTROL PLANE</span><p>SQLite persistence · transactional lease · HMAC-SHA256 · deterministic clock</p><span>PORTS 5179 → 8179</span></footer>
  </div>;
}
