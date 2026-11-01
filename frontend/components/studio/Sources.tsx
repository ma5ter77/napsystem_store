"use client";

// Immutable studio sources (spec §2.1, §2.10): upload with provenance, read-only metadata, never modified.
import { useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, useAction, useLoad } from "@/components/ui";
import { api, sha256File, uploadWithProgress } from "@/lib/api";

export type SourceAsset = {
  id: string; name: string; status: string; sha256: string; size_bytes: number; codec: string | null;
  channels: number | null; sample_rate: number | null; bit_depth: number | null; channel_layout: string | null;
  duration: number | null; lossy: boolean | null; rights: { source?: string; license?: string } | null;
};

export function sourceSummary(a: SourceAsset): string {
  const parts = [a.codec?.toUpperCase(), a.channels ? `${a.channels} ch` : null,
    a.sample_rate ? `${(a.sample_rate / 1000).toFixed(1)} kHz` : null, a.bit_depth ? `${a.bit_depth}-bit` : null,
    a.duration ? `${a.duration.toFixed(2)} s` : null];
  return parts.filter(Boolean).join(" · ");
}

function Upload({ onDone }: { onDone: () => void }) {
  const { t } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState({ source: "", license: "" });
  const [phase, setPhase] = useState<{ step: "hash" | "upload"; p: number } | null>(null);
  const act = useAction();
  return (
    <form className="panel stack" onSubmit={async (e) => {
      e.preventDefault();
      if (!file) return;
      const ok = await act.run(async () => {
        setPhase({ step: "hash", p: 0 });
        const sha256 = await sha256File(file, (p) => setPhase({ step: "hash", p }));
        const tk = await api("/audio/assets", { body: { filename: file.name, size_bytes: file.size, sha256, rights } });
        setPhase({ step: "upload", p: 0 });
        await uploadWithProgress(tk.upload_url, file, (p) => setPhase({ step: "upload", p }));
        return true;
      });
      setPhase(null);
      if (ok) { setFile(null); onDone(); }
    }}>
      <h3>{t("studio.upload_title")}</h3>
      <p className="hint">{t("studio.upload_hint")}</p>
      <div className="form wide">
        <label className="field">{t("studio.file")}<input type="file" required accept=".wav,.flac,.aif,.aiff,audio/wav,audio/flac,audio/aiff"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <label className="field">{t("studio.rights_source")}<input required maxLength={200} value={rights.source}
          onChange={(e) => setRights({ ...rights, source: e.target.value })} /><span className="hint">{t("studio.rights_source_hint")}</span></label>
        <label className="field">{t("studio.rights_license")}<input required maxLength={200} value={rights.license}
          onChange={(e) => setRights({ ...rights, license: e.target.value })} /><span className="hint">{t("studio.rights_license_hint")}</span></label>
      </div>
      {phase && <div className="stack" aria-live="polite">
        <span className="hint">{t(phase.step === "hash" ? "studio.hashing" : "studio.uploading")} {Math.round(phase.p * 100)}%</span>
        <div className="progress" role="progressbar" aria-valuenow={Math.round(phase.p * 100)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${phase.p * 100}%` }} /></div>
      </div>}
      <ErrorBox error={act.error} />
      <div><button className="btn" disabled={act.pending || !file}>{t("studio.upload")}</button></div>
    </form>
  );
}

/** Library list. With `onPick` it becomes a picker inside the editor. */
export function SourceLibrary({ onPick, compact = false }: { onPick?: (a: SourceAsset) => void; compact?: boolean }) {
  const { t } = useApp();
  const { data, error, loading, reload } = useLoad<{ items: SourceAsset[] }>("/audio/assets");
  const [showUpload, setShowUpload] = useState(false);
  return (
    <section className="stack" aria-labelledby="sources-h">
      <div className="spread">
        {compact ? <h3 id="sources-h">{t("studio.sources")}</h3> : <h2 id="sources-h">{t("studio.sources")}</h2>}
        <button className={`btn ${compact ? "small ghost" : "ghost"}`} onClick={() => setShowUpload(!showUpload)} aria-expanded={showUpload}>{t("studio.upload")}</button>
      </div>
      {!compact && <p className="hint">{t("studio.sources_lead")}</p>}
      {showUpload && <Upload onDone={() => { setShowUpload(false); reload(); }} />}
      {loading && !data ? <Loading lines={2} /> : error ? <ErrorBox error={error} onRetry={reload} /> :
        !data?.items.length ? <Empty>{t("studio.no_sources")}</Empty> : (
          <ul className={`studio-sources${compact ? " compact" : ""}`}>
            {data.items.map((a) => (
              <li key={a.id}>
                <div className="studio-source-main">
                  <strong title={a.name}>{a.name}</strong>
                  <span className="muted">{a.status === "uploaded" ? sourceSummary(a) : t("studio.source_processing")}</span>
                  {a.lossy && <span className="badge warn">{t("studio.lossy")}</span>}
                  {!compact && <span className="muted studio-hash" title={a.sha256}>SHA-256 {a.sha256.slice(0, 16)}…</span>}
                  {!compact && a.rights && <span className="muted">{a.rights.source} · {a.rights.license}</span>}
                </div>
                {onPick && <button className="btn small" disabled={a.status !== "uploaded"} onClick={() => onPick(a)}
                  aria-label={`${t("studio.place")}: ${a.name}`}>{t("studio.place")}</button>}
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}
