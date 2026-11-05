"use client";

// NAP-118: the administrator's news — any news item becomes a Telegram post in one click: a short explanation
// (written by the language model when it is connected, otherwise from a template), edited next to a preview that
// shows exactly what the channel will show, then sent now or at a set time. Sending happens once.
import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

type Post = {
  id: string; source_key: string; source_title: string; text: string; photo_url: string | null; link_url: string | null;
  language: string; generated_by: string; status: "draft" | "scheduled" | "sending" | "sent" | "failed";
  scheduled_at: string | null; sent_at: string | null; message_url: string | null; error_code: string | null;
  error_detail: string | null; message: string; limit: number;
};
type Status = { bot: boolean; channel: string | null; model: boolean; model_name: string | null; ready: boolean };

const KINDS = ["all", "announcement", "change", "app", "product"] as const;

function message(text: string, link: string | null) {
  const x = text.trim();
  return link && !x.includes(link) ? `${x}\n\n${link}` : x;
}

/** The post as the channel shows it: avatar, channel name, picture, text with live links, time. */
export function TelegramPreview({ channel, text, photo, link }: { channel: string | null; text: string; photo: string | null; link: string | null }) {
  const { t, locale } = useApp();
  const body = message(text, link);
  const parts = body.split(/(https:\/\/\S+)/g);
  return (
    <figure className="tg-preview" data-testid="tg-preview" aria-label={t("tg.preview_label")}>
      <figcaption className="tg-head">
        <span className="tg-avatar" aria-hidden="true" />
        <span><strong>{channel || t("tg.channel_unset")}</strong><span className="hint">{t("tg.channel_kind")}</span></span>
      </figcaption>
      <div className="tg-bubble">
        {photo && <img src={photo} alt="" className="tg-photo" />}
        <p className="tg-text">{parts.map((p, i) => p.startsWith("https://") ? <a key={i} href={p} target="_blank" rel="noreferrer">{p}</a> : <span key={i}>{p}</span>)}</p>
        <span className="tg-time">{new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(new Date())}</span>
      </div>
    </figure>
  );
}

function Editor({ post, status, onChange, onClose }: { post: Post; status: Status; onChange: (p: Post) => void; onClose: () => void }) {
  const { t, locale } = useApp();
  const act = useAction();
  const [text, setText] = useState(post.text);
  const [photo, setPhoto] = useState(post.photo_url ?? "");
  const [link, setLink] = useState(post.link_url ?? "");
  const [when, setWhen] = useState("");
  const [flash, setFlash] = useState<string | null>(null);
  const locked = post.status === "sent" || post.status === "sending";
  const ref = useRef<HTMLElement>(null);
  useEffect(() => { ref.current?.scrollIntoView({ block: "start", behavior: "smooth" }); ref.current?.querySelector("textarea")?.focus({ preventScroll: true }); }, []);
  const limit = photo ? 1024 : 4096;
  const length = message(text, link || null).length;
  const dirty = text !== post.text || (photo || null) !== post.photo_url || (link || null) !== post.link_url;
  const save = async (extra: Record<string, any> = {}) => {
    const x = await act.run(() => api<Post>(`/admin/telegram-posts/${post.id}`, { method: "PUT", body: { text, photo_url: photo, link_url: link, ...extra } }));
    if (x) onChange(x);
    return x;
  };
  const publish = async () => {
    if (dirty && !(await save())) return;
    const x = await act.run(() => api<Post>(`/admin/telegram-posts/${post.id}/publish`, { method: "POST" }));
    if (x) { onChange(x); setFlash(x.status === "sent" ? t("tg.done_sent") : x.status === "failed" ? null : t("tg.done_scheduled")); }
  };
  return (
    <section ref={ref} className="panel stack tg-editor" aria-labelledby="tg-ed-h" data-testid="tg-editor">
      <div className="spread"><h3 id="tg-ed-h">{t("tg.editor_title", { title: post.source_title })}</h3>
        <button type="button" className="btn ghost small" onClick={onClose}>{t("tg.close")}</button></div>
      <p className="hint">{t(`tg.by_${post.generated_by}`, { model: status.model_name ?? "" })}{post.error_code === "model_failed" && ` ${t("tg.model_fell_back")}`}</p>
      <div className="tg-split">
        <div className="stack">
          <label className="field">{t("tg.text")}
            <textarea rows={9} value={text} disabled={locked} onChange={(e) => setText(e.target.value)} aria-describedby="tg-count" />
            <span id="tg-count" className={`hint${length > limit ? " bad-text" : ""}`}>{t("tg.count", { n: length, max: limit })}{photo ? ` ${t("tg.caption_note")}` : ""}</span>
          </label>
          <label className="field">{t("tg.link")}<input type="url" inputMode="url" placeholder="https://" value={link} disabled={locked} onChange={(e) => setLink(e.target.value)} /></label>
          <label className="field">{t("tg.photo")}<input type="url" inputMode="url" placeholder="https://" value={photo} disabled={locked} onChange={(e) => setPhoto(e.target.value)} /><span className="hint">{t("tg.photo_hint")}</span></label>
          {!locked && (
            <div className="row">
              <button type="button" className="btn ghost" disabled={act.pending || !dirty} onClick={async () => { if (await save()) setFlash(t("tg.done_saved")); }}>{t("common.save")}</button>
              <button type="button" className="btn" disabled={act.pending || !status.ready || length > limit || !text.trim()} onClick={publish}>{t("tg.publish_now")}</button>
            </div>)}
          {!locked && (
            <div className="row">
              <label className="field" style={{ margin: 0 }}>{t("tg.schedule")}<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
              <button type="button" className="btn ghost" disabled={act.pending || !when || !status.ready}
                onClick={async () => { const x = await save({ scheduled_at: new Date(when).toISOString() }); if (x) setFlash(t("tg.done_scheduled_at", { at: date(x.scheduled_at!, locale, true) })); }}>{t("tg.schedule_btn")}</button>
              {post.status === "scheduled" && <button type="button" className="btn ghost small" disabled={act.pending}
                onClick={async () => { if (await save({ scheduled_at: null })) setFlash(t("tg.done_unscheduled")); }}>{t("tg.unschedule")}</button>}
            </div>)}
          {!status.ready && <p className="notice warn">{t("tg.not_ready")}</p>}
          <PostState post={post} />
          {flash && <p className="notice ok" role="status">{flash}</p>}
          <ErrorBox error={act.error} />
        </div>
        <TelegramPreview channel={status.channel} text={text} photo={photo || null} link={link || null} />
      </div>
    </section>
  );
}

function PostState({ post }: { post: Post }) {
  const { t, locale } = useApp();
  if (post.status === "sent") return <p className="notice ok" data-testid="tg-sent">{t("tg.sent_at", { at: date(post.sent_at!, locale, true) })}{post.message_url && <> · <a href={post.message_url} target="_blank" rel="noreferrer">{t("tg.open_post")}</a></>}</p>;
  if (post.status === "failed") return <p className="notice bad" role="alert">{t(`tg.err.${post.error_code ?? "telegram_failed"}`)}{post.error_detail ? ` (${post.error_detail})` : ""}</p>;
  if (post.status === "scheduled") return <p className="notice">{t("tg.scheduled_for", { at: date(post.scheduled_at!, locale, true) })}</p>;
  return null;
}

function Setup({ s }: { s: Status }) {
  const { t } = useApp();
  const row = (ok: boolean, key: string, extra?: string) => (
    <li className={ok ? "ok" : "warn"}><span aria-hidden="true">{ok ? "✓" : "!"}</span><span>{t(key, { value: extra ?? "" })}</span></li>);
  return (
    <details className="panel flat" open={!s.ready}>
      <summary><strong>{s.ready ? t("tg.setup_ready", { channel: s.channel ?? "" }) : t("tg.setup_missing")}</strong></summary>
      <ul className="checklist" style={{ marginTop: 10 }}>
        {row(s.bot, s.bot ? "tg.setup_bot_ok" : "tg.setup_bot")}
        {row(!!s.channel, s.channel ? "tg.setup_channel_ok" : "tg.setup_channel", s.channel ?? "")}
        {row(s.model, s.model ? "tg.setup_model_ok" : "tg.setup_model", s.model_name ?? "")}
      </ul>
      <p className="hint">{t("tg.setup_where")}</p>
    </details>
  );
}

export function NewsAdmin() {
  const { t, locale } = useApp();
  const [lang, setLang] = useState<"ru" | "en">(locale === "en" ? "en" : "ru");
  const { data, error, loading, reload, setData } = useLoad<any>(`/admin/news?lang=${lang}`, [lang]);
  const [kind, setKind] = useState<(typeof KINDS)[number]>("all");
  const [open, setOpen] = useState<Post | null>(null);
  const [form, setForm] = useState({ title: "", body: "", link: "" });
  const act = useAction();
  useEffect(() => { setOpen(null); }, [lang]);
  const items = useMemo(() => (data?.items ?? []).filter((x: any) => kind === "all" || x.kind === kind || (kind === "app" && x.kind === "app_update")), [data, kind]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const status: Status = data.status;
  const draft = async (key: string) => {
    const p = await act.run(() => api<Post>("/admin/telegram-posts", { body: { key, language: lang } }));
    if (p) { setOpen(p); reload(); }
  };
  const changed = (p: Post) => {
    setOpen(p);
    setData({ ...data, items: data.items.map((x: any) => x.post?.id === p.id || x.key === p.source_key ? { ...x, post: p } : x) });
  };
  return (
    <div className="stack" data-testid="news-admin">
      <Setup s={status} />
      {open && <Editor key={open.id} post={open} status={status} onChange={changed} onClose={() => setOpen(null)} />}
      <form className="panel flat stack" onSubmit={async (e) => {
        e.preventDefault();
        if (await act.run(() => api("/admin/news", { body: { title: form.title, body: form.body, link: form.link || null } }))) { setForm({ title: "", body: "", link: "" }); reload(); }
      }}>
        <h3>{t("tg.new_announcement")}</h3>
        <label className="field">{t("tg.ann_title")}<input required maxLength={200} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
        <label className="field">{t("tg.ann_body")}<textarea rows={3} maxLength={5000} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></label>
        <label className="field">{t("tg.link")}<input type="url" placeholder="https://" value={form.link} onChange={(e) => setForm({ ...form, link: e.target.value })} /></label>
        <div><button className="btn ghost" disabled={act.pending || !form.title.trim()}>{t("tg.add_announcement")}</button></div>
      </form>
      <div className="spread">
        <div className="seg" role="group" aria-label={t("tg.filter")}>{KINDS.map((k) => <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>{t(`tg.kind.${k}`)}</button>)}</div>
        <label className="row hint">{t("tg.post_language")}<select value={lang} onChange={(e) => setLang(e.target.value as "ru" | "en")}><option value="ru">RU</option><option value="en">EN</option></select></label>
      </div>
      {!items.length ? <Empty>{t("tg.empty")}</Empty> : (
        <ul className="news-list">{items.map((x: any) => (
          <li key={x.key} className="panel flat" data-testid="news-item">
            <div className="spread">
              <span><span className={`badge ${x.kind === "announcement" ? "r" : "gray"}`}>{t(`tg.kind.${x.kind === "app_update" ? "app" : x.kind}`)}</span> <strong>{x.title}</strong>
                {x.version && <span className="hint"> · v{x.version}</span>}</span>
              <span className="hint">{x.at ? date(x.at, locale, true) : ""}</span>
            </div>
            {x.body && <p className="hint news-body">{x.body.length > 220 ? `${x.body.slice(0, 220)}…` : x.body}</p>}
            <div className="row">
              {x.post && <span className={`badge ${x.post.status === "sent" ? "ok" : x.post.status === "failed" ? "bad" : x.post.status === "scheduled" ? "warn" : "gray"}`}>{t(`tg.state.${x.post.status}`)}</span>}
              {x.post && x.post.status !== "sent"
                ? <button type="button" className="btn small" onClick={() => setOpen(x.post)}>{t("tg.open_draft")}</button>
                : <button type="button" className="btn small" disabled={act.pending} onClick={() => draft(x.key)}>{x.post ? t("tg.post_again") : t("tg.make_post")}</button>}
              {x.post?.message_url && <a href={x.post.message_url} target="_blank" rel="noreferrer">{t("tg.open_post")}</a>}
            </div>
          </li>))}</ul>)}
      <ErrorBox error={act.error} />
    </div>
  );
}
