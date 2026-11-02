"use client";

import { Breadcrumbs, PageHead } from "@/components/nav";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api, uid } from "@/lib/api";
import { date, money } from "@/lib/i18n";

function SellerOrder() {
  const { id } = useParams<{ id: string }>();
  const { t, locale, me } = useApp();
  const { data: o, error, loading, reload, setData } = useLoad<any>(`/seller/orders/${id}`);
  const [pack, setPack] = useState<Record<string, number>>({});
  const [weight, setWeight] = useState(1000);
  const [dims, setDims] = useState("300,200,150");
  const [manual, setManual] = useState({ carrier: "", tracking_number: "" });
  const [quote, setQuote] = useState({ amount: 450, eta_min_days: 10, eta_max_days: 20, valid_hours: 72, note: "" });
  const [refund, setRefund] = useState({ goods: 0, shipping: 0 });
  const [results, setResults] = useState<Record<string, any>>({});
  const act = useAction();
  if (loading && !o) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const isSeller = me?.roles.includes("seller");
  const run = async (path: string, body?: any, headers?: Record<string, string>) => {
    const r = await act.run(() => api(`/seller/orders/${o.id}${path}`, { method: "POST", body, headers }));
    if (r?.id) setData(r);
  };
  const remaining = (it: any) => it.quantity - it.packed_qty;
  const a = o.address?.fields ?? {};
  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { href: `/${locale}/seller`, label: t("seller.title") }, { label: o.number }]} />
      <div className="spread"><h1>{o.number}</h1>
        <div className="status-line"><StatusBadge ns="order_status" value={o.status} /><StatusBadge ns="payment_status" value={o.payment_status} /><StatusBadge ns="fulfillment_status" value={o.fulfillment_status} /></div></div>
      {o.is_demo && <div className="notice test">{t("common.test_mode")}</div>}
      <div className="two">
        <section className="panel stack">
          <h2>{t("seller.ship_to")}</h2>
          <address style={{ fontStyle: "normal" }}>{[a.name, a.name_latin && `(${a.name_latin})`, a.company, a.line1, a.line1_latin && `(${a.line1_latin})`, a.line2, a.city, a.region, a.postal_code, o.address?.country, a.phone].filter(Boolean).join(", ")}</address>
          <p className="hint">{t("seller.locale_note", { lang: o.locale.toUpperCase() })}</p>
          <a href={`/api/v1/hardware/orders/${o.id}/documents/packing-list?format=pdf`} target="_blank" rel="noreferrer">{t("orders.packing_list")}</a>
        </section>
        <section className="panel stack">
          <h2>{t("orders.items")}</h2>
          <table><thead><tr><th>SKU</th><th>{t("cart.qty")}</th><th>{t("seller.packed")}</th><th>{t("orders.shipped")}</th></tr></thead>
            <tbody>{o.items.map((it: any) => <tr key={it.id}><td>{it.sku}<div className="hint">{it.title}</div></td><td>{it.quantity}</td><td>{it.packed_qty}</td><td>{it.shipped_qty}</td></tr>)}</tbody></table>
        </section>
      </div>

      {o.status === "awaiting_quote" && isSeller && o.freight_quote?.status === "requested" && (
        <form className="panel stack" onSubmit={(e) => { e.preventDefault(); run(`/quotes/${o.freight_quote.id}`, { amount_minor: Math.round(quote.amount * 100), currency: o.currency, eta_min_days: quote.eta_min_days, eta_max_days: quote.eta_max_days, valid_hours: quote.valid_hours, note: quote.note }); }}>
          <h2>{t("seller.quote")}</h2>
          <div className="form wide">
            <label className="field">{t("account.amount")} ({o.currency})<input type="number" step="0.01" value={quote.amount} onChange={(e) => setQuote({ ...quote, amount: Number(e.target.value) })} /></label>
            <label className="field">{t("seller.eta_min")}<input type="number" value={quote.eta_min_days} onChange={(e) => setQuote({ ...quote, eta_min_days: Number(e.target.value) })} /></label>
            <label className="field">{t("seller.eta_max")}<input type="number" value={quote.eta_max_days} onChange={(e) => setQuote({ ...quote, eta_max_days: Number(e.target.value) })} /></label>
            <label className="field">{t("seller.valid_hours")}<input type="number" value={quote.valid_hours} onChange={(e) => setQuote({ ...quote, valid_hours: Number(e.target.value) })} /></label>
          </div>
          <label className="field">{t("seller.note")}<textarea value={quote.note} onChange={(e) => setQuote({ ...quote, note: e.target.value })} /></label>
          <div><button className="btn">{t("seller.send_quote")}</button></div>
        </form>
      )}

      {o.status === "confirmed" && o.payment_status !== "unpaid" && (
        <>
          {o.fulfillment_status === "unfulfilled" && <div className="row"><button className="btn" onClick={() => run("/acknowledge")}>{t("seller.acknowledge")}</button></div>}
          {o.items.some((it: any) => remaining(it) > 0) && (
            <form className="panel stack" onSubmit={(e) => {
              e.preventDefault();
              const items = Object.entries(pack).filter(([, q]) => q > 0).map(([order_item_id, quantity]) => ({ order_item_id, quantity }));
              run("/parcels", { items, weight_g: weight, dims_mm: dims.split(",").map((x) => Number(x.trim())) });
              setPack({});
            }}>
              <h2>{t("seller.new_parcel")}</h2>
              {o.items.filter((it: any) => remaining(it) > 0).map((it: any) => (
                <label key={it.id} className="field">{it.sku} ({t("seller.left_to_pack", { count: remaining(it) })})
                  <input type="number" min={0} max={remaining(it)} value={pack[it.id] ?? 0} onChange={(e) => setPack({ ...pack, [it.id]: Number(e.target.value) })} style={{ maxWidth: 120 }} /></label>))}
              <div className="form wide">
                <label className="field">{t("seller.weight_g")}<input type="number" value={weight} onChange={(e) => setWeight(Number(e.target.value))} /></label>
                <label className="field">{t("seller.dims")}<input value={dims} onChange={(e) => setDims(e.target.value)} /></label>
              </div>
              <div><button className="btn">{t("seller.create_parcel")}</button></div>
            </form>
          )}
          {o.parcels.map((p: any) => (
            <section key={p.id} className="panel stack">
              <div className="spread"><h3>{t("orders.parcel", { n: p.number })} · {p.weight_g} g · {p.dims_mm.join("×")} mm</h3>
                <span><StatusBadge ns="parcel_status" value={p.status} /> <span className="hint">{t(`sources.${p.status_source ?? "supplier"}`)}</span></span></div>
              <div className="hint">{p.items.map((x: any) => `${o.items.find((i: any) => i.id === x.order_item_id)?.sku} × ${x.quantity}`).join(", ")} · {p.carrier ?? "—"} {p.tracking_number ?? ""}</div>
              {p.status === "packing" && (
                <div className="stack">
                  <div className="row"><button className="btn" onClick={() => run(`/parcels/${p.id}/label`)}>{t("seller.buy_label")}</button><span className="hint">{t("seller.label_note")}</span></div>
                  <form className="row" onSubmit={(e) => { e.preventDefault(); run(`/parcels/${p.id}/manual-shipment`, manual); }}>
                    <input placeholder={t("seller.carrier")} value={manual.carrier} onChange={(e) => setManual({ ...manual, carrier: e.target.value })} required style={{ maxWidth: 200 }} />
                    <input placeholder={t("seller.tracking")} value={manual.tracking_number} onChange={(e) => setManual({ ...manual, tracking_number: e.target.value })} required style={{ maxWidth: 200 }} />
                    <button className="btn ghost">{t("seller.manual")}</button>
                  </form>
                </div>
              )}
              {p.status === "label_created" && <div className="row"><button className="btn" onClick={() => run(`/parcels/${p.id}/handover`)}>{t("seller.handover")}</button><span className="hint">{t("seller.handover_note")}</span></div>}
              {p.documents.filter((d: any) => d.kind === "label").map((d: any) => (
                <button key={d.asset_id} className="btn ghost small" onClick={async () => { const r = await act.run(() => api(`/hardware/orders/${o.id}/documents/${d.asset_id}`)); if (r) window.open(r.url, "_blank"); }}>{t("seller.open_label")}</button>))}
              {!["packing", "label_created"].includes(p.status) && (
                <div className="row">
                  {["in_transit", "customs", "out_for_delivery", "delivered", "exception"].map((s) => (
                    <button key={s} className="btn ghost small" onClick={() => run(`/parcels/${p.id}/events`, { status: s, description: "manual" })}>{t(`parcel_status.${s}`)}</button>))}
                  {p.adapter === "mock" && <button className="btn ghost small" onClick={async () => { await act.run(() => api(`/test/parcels/${p.id}/tracking`, { body: { status: "in_transit" } })); reload(); }}>{t("seller.simulate")}</button>}
                </div>
              )}
              <ol className="steps hint">{p.events.map((e: any, i: number) => <li key={i}>{t(`parcel_status.${e.status}`)} — {date(e.occurred_at, locale, true)} ({t(`sources.${e.source}`)})</li>)}</ol>
            </section>
          ))}
        </>
      )}

      {o.returns.length > 0 && (
        <section className="panel stack">
          <h2>{t("returns.title")}</h2>
          {o.returns.map((r: any) => (
            <div key={r.id} className="panel flat stack">
              <div><StatusBadge ns="return_status" value={r.status} /> {r.reason} {r.return_tracking && <span className="hint">· {r.return_tracking}</span>}</div>
              <div className="row">
                {r.status === "requested" && isSeller && <><button className="btn small" onClick={() => run(`/returns/${r.id}`, { action: "approve", note: "OK" })}>{t("returns.approve")}</button>
                  <button className="btn ghost small" onClick={() => run(`/returns/${r.id}`, { action: "reject", note: "—" })}>{t("returns.reject")}</button></>}
                {r.status === "in_transit" && <button className="btn small" onClick={() => run(`/returns/${r.id}`, { action: "receive" })}>{t("returns.receive")}</button>}
                {r.status === "received" && (
                  <div className="stack">
                    {r.items.map((x: any) => (
                      <div key={x.order_item_id} className="row"><span>{o.items.find((i: any) => i.id === x.order_item_id)?.sku} × {x.quantity}</span>
                        <label className="check"><input type="checkbox" checked={results[x.order_item_id]?.restock !== 0} onChange={(e) => setResults({ ...results, [x.order_item_id]: { accepted: x.quantity, restock: e.target.checked ? x.quantity : 0 } })} /> {t("returns.restock")}</label></div>))}
                    <button className="btn small" onClick={() => run(`/returns/${r.id}`, { action: "inspect", results })}>{t("returns.inspect")}</button>
                  </div>
                )}
                {r.status === "inspected" && isSeller && <button className="btn ghost small" onClick={() => run(`/returns/${r.id}`, { action: "close" })}>{t("returns.close")}</button>}
              </div>
            </div>
          ))}
        </section>
      )}

      {isSeller && ["paid", "partially_refunded"].includes(o.payment_status) && (
        <form className="panel stack" onSubmit={(e) => { e.preventDefault(); run("/refunds", { goods_minor: Math.round(refund.goods * 100), shipping_minor: Math.round(refund.shipping * 100), fees_minor: 0, reason: "seller" }, { "Idempotency-Key": uid() }); }}>
          <h2>{t("seller.refund")}</h2>
          <p className="hint">{t("seller.refund_hint", { paid: money(o.total_minor, o.currency, locale), refunded: money(o.refunded_minor, o.currency, locale) })}</p>
          <div className="form wide">
            <label className="field">{t("orders.goods")}<input type="number" step="0.01" value={refund.goods} onChange={(e) => setRefund({ ...refund, goods: Number(e.target.value) })} /></label>
            <label className="field">{t("orders.shipping")}<input type="number" step="0.01" value={refund.shipping} onChange={(e) => setRefund({ ...refund, shipping: Number(e.target.value) })} /></label>
          </div>
          <div><button className="btn ghost">{t("seller.do_refund")}</button></div>
        </form>
      )}
      <ErrorBox error={act.error} />
    </div>
  );
}

export default function SellerOrderPage() {
  return <div className="section"><RequireAuth role={["seller", "fulfillment"]}><SellerOrder /></RequireAuth></div>;
}
