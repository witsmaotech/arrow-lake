// Streaming OLAP reader: POST + Authorization header (EventSource can't do either).
// SSE events (see arrow_lake/api/routers/query.py:36 _stream_table):
//   {type:'schema', columns:[...], row_count}
//   {type:'batch',  rows:M, data:'<base64 Arrow IPC RecordBatch>'}
//   {type:'done',   total_rows}
// apache-arrow is imported dynamically from importmap (esm.sh) only when stream is used.

export async function streamQuery({ url, token, body, onSchema, onBatch, onDone, onError, signal }) {
  let arrow;
  try {
    arrow = await import("apache-arrow");
  } catch (e) {
    onError && onError(new Error("流式模式需要加载 apache-arrow 库(联网从 CDN 拉取)。请检查网络,或取消 stream 用非流式 JSON。"));
    return;
  }

  let resp;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    onError && onError(new Error(`网络错误: ${e.message}`));
    return;
  }
  if (!resp.ok || !resp.body) {
    let d = resp.statusText;
    try { const j = await resp.json(); d = j.detail || d; } catch (_) {}
    onError && onError(new Error(`${resp.status}: ${d}`));
    return;
  }

  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const evt = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const dataLine = evt.split("\n").find(l => l.startsWith("data:"));
        if (!dataLine) continue;
        let json;
        try { json = JSON.parse(dataLine.slice(5).trim()); } catch (_) { continue; }
        if (json.type === "schema") onSchema && onSchema(json);
        else if (json.type === "batch") onBatch && onBatch({ ...json, rows: decodeBatch(arrow, json.data) });
        else if (json.type === "done") onDone && onDone(json);
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") onError && onError(e);
  }
}

function decodeBatch(arrow, b64) {
  const u8 = base64ToUint8(b64);
  const rows = [];
  const reader = arrow.RecordBatchStreamReader.readAll(u8);
  for (const batch of reader) {
    const cols = batch.schema.fields.map(f => f.name);
    for (let i = 0; i < batch.numRows; i++) {
      const row = {};
      cols.forEach(c => {
        const col = batch.getChild(c);
        row[c] = col ? col.get(i) : null;
      });
      rows.push(row);
    }
  }
  return rows;
}

function base64ToUint8(b64) {
  const bin = atob(b64);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return u8;
}
