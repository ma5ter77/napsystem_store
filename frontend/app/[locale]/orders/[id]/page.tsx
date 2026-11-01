"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { AddressForm } from "@/components/AddressForm";
import { LegalText } from "@/components/LegalText";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, Price, RequireAuth, StatusBadge, TestModeBanner, useAction, useLoad } from "@/components/ui";
import { OrderTimeline } from "@/components/OrderTimeline";
import { Breadcrumbs } from "@/components/nav";
import { api } from "@/lib/api";
import { date, money } from "@/lib/i18n";

function Order() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useApp();
  const router = useRouter();
  const { data: o, error, loading, reload, setData } = useLoad<any>(`/hardware/orders/${id}`);
  const [rates, setRates] = useState<any[] | null>(null);
  const [editAddr, setEditAddr] = useState(false);
  const [ret, setRet] = useState<Record<string, number>>({});
  const [reason, setReason] = useState("");
  const [rtrack, setRtrack] = useState("");
  const act = useAction();
  if (loading && !o) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const unpaid = ["unpaid", "failed"].includes(o.payment_status) && ["awaiting_payment", "awaiting_quote"].includes(o.status);
  const cur = o.currency;
  const run = async (fn: () => Promise<any>) => { const r = await act.run(fn); if (r && r.id) setData(r); return r; };

  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") },
        { href: `/${locale}/orders`, label: t("nav.orders") }, { label: o.number }]} />
      <div className="spread">
        <h1>{t("orders.order", { number: o.number })}</h1>
        <div className="status-line">
          <StatusBadge ns="order_status" value={o.status} />
          <StatusBadge ns="payment_status" value={o.payment_status} />
          <StatusBadge ns="fulfillment_status" value={o.fulfillment_status} />
        </div>
      </div>
      <section className="panel"><OrderTimeline order={o} /></section>
      {o.is_demo && <TestModeBanner />}
      {o.exception_reason && <div className="notice bad">{t(`exceptions.${o.exception_reason}`)}</div>}
      <p className="muted">{t("orders.seller_from", { seller: o.seller.name, country: o.ship_from })}</p>

      <section className="panel stack">
        <h2>{t("orders.items")}</h2>
        <div className="scroll-x"><table>
          <thead><tr><th>{t("cart.item")}</th><th>{t("cart.qty")}</th><th>{t("cart.price")}</th><th>{t("orders.shipped")}</th></tr></thead>
          <tbody>{o.items.map((it: any) => (
            <tr key={it.id}><td>{it.title}<div className="hint">{it.sku}{it.made_to_order ? ` · ${t("hw.made_to_order_short")}` : ""}</div></td>
              <td>{it.quantity}</td><td><Price minor={it.unit_price_minor} currency={cur} /></td><td>{it.shipped_qty}/{it.quantity}</td></tr>))}
          </tbody>
        </table></div>
        <dl className="kv">
          <dt>{t("orders.goods")}</dt><dd>{money(o.goods_minor, cur, locale)}</dd>
          <dt>{t("orders.shipping")}</dt><dd>{o.shipping_minor === null ? t("orders.not_calculated") : money(o.shipping_minor, cur, locale)}</dd>
          {o.shipping_rate?.insurance_minor !== undefined && o.shipping_rate?.insurance_minor !== null && <><dt>{t("orders.insurance")}</dt><dd>{money(o.shipping_rate.insurance_minor, cur, locale)}</dd></>}
          <dt>{t("orders.fees")}</dt><dd>{money(o.fees_minor, cur, locale)}</dd>
          <dt>{t("orders.duties")}</dt><dd>{t("orders.duties_unknown", { payer: t(`payer.${o.terms.duties_payer}`) })}</dd>
          <dt><strong>{t("orders.total")}</strong></dt><dd><strong>{o.total_minor === null ? t("orders.not_calculated") : money(o.total_minor, cur, locale)}</strong></dd>
          {o.refunded_minor > 0 && <><dt>{t("orders.refunded")}</dt><dd>{money(o.refunded_minor, cur, locale)}</dd></>}
        </dl>
      </section>

      <section className="panel stack">
        <h2>{t("orders.address")}</h2>
        {o.address && !editAddr ? (
          <div className="spread">
            <address style={{ fontStyle: "normal" }}>
              {[o.address.fields.name, o.address.fields.line1, o.address.fields.line2, o.address.fields.city, o.address.fields.region,
                o.address.fields.postal_code, new Intl.DisplayNames([locale], { type: "region" }).of(o.address.country)].filter(Boolean).join(", ")}
            </address>
            {unpaid && <button className="btn ghost small" onClick={() => setEditAddr(true)}>{t("common.change")}</button>}
          </div>
        ) : unpaid ? <AddressForm orderId={o.id} initial={o.address} onSaved={(r) => { setData(r); setEditAddr(false); setRates(null); }} /> : null}
      </section>

      {unpaid && o.address && (
        <section className="panel stack">
          <h2>{t("orders.delivery")}</h2>
          {o.status === "awaiting_quote" ? (
            <div className="stack">
              <p>{t("orders.quote_needed")}</p>
              {!o.freight_quote || o.freight_quote.status === "expired" ? (
                <button className="btn" disabled={act.pending} onClick={() => run(() => api(`/hardware/orders/${o.id}/quote-request`, { method: "POST" }))}>{t("orders.request_quote")}</button>
              ) : o.freight_quote.status === "requested" ? <div className="notice">{t("orders.quote_waiting")}</div> : null}
              {o.freight_quote?.status === "quoted" && (
                <div className="panel flat stack">
                  <div>{t("orders.quote_offer", { amount: money(o.freight_quote.amount_minor, o.freight_quote.currency, locale),
                    min: o.freight_quote.eta[0], max: o.freight_quote.eta[1], until: date(o.freight_quote.valid_until, locale, true) })}</div>
                  {o.freight_quote.note && <div className="hint">{o.freight_quote.note}</div>}
                  <button className="btn" onClick={() => run(() => api(`/hardware/orders/${o.id}/quote-accept`, { body: { quote_id: o.freight_quote.id } }))}>{t("orders.accept_quote")}</button>
                </div>
              )}
            </div>
          ) : (
            <div className="stack">
              {o.shipping_rate && <div className="notice">{t("orders.selected_rate", { carrier: o.shipping_rate.carrier ?? "—",
                amount: money(o.shipping_rate.amount_minor, cur, locale), min: o.shipping_rate.eta_days[0], max: o.shipping_rate.eta_days[1] })}</div>}
              <button className="btn ghost" disabled={act.pending} onClick={async () => {
                const r = await act.run(() => api(`/hardware/orders/${o.id}/rates`, { method: "POST" }));
                if (r) setRates(r.items); else reload();
              }}>{o.shipping_rate ? t("orders.recalculate") : t("orders.calculate")}</button>
              {rates && (
                <fieldset className="stack" style={{ border: 0, padding: 0 }}>
                  <legend className="sr-only">{t("orders.delivery")}</legend>
                  {rates.map((r) => (
                    <label key={r.id} className="check panel flat">
                      <input type="radio" name="rate" checked={o.shipping_rate?.rate_id === r.id}
                             onChange={() => run(() => api(`/hardware/orders/${o.id}/shipping`, { body: { rate_id: r.id } }))} />
                      <span><strong>{r.carrier}</strong> — {money(r.amount_minor, r.currency, locale)}
                        <span className="hint"> · {t("orders.eta", { min: r.eta_days[0], max: r.eta_days[1] })} · {t("orders.packages", { count: r.packages })} · {t("orders.rate_valid", { time: date(r.expires_at, locale, true) })}{r.test_mode ? ` · ${t("common.test")}` : ""}</span></span>
                    </label>
                  ))}
                </fieldset>
              )}
            </div>
          )}
          {o.status === "awaiting_payment" && o.shipping_rate && (
            <div className="stack">
              <p className="hint">{t("orders.eta_note")}</p>
              <p className="hint"><LegalText text={t("legal.order_note")} locale={locale} labels={{ terms: t("legal.terms") }} /> {t("vat.note")}</p>
              <button className="btn" disabled={act.pending} onClick={async () => {
                const r = await act.run(() => api(`/hardware/orders/${o.id}/pay`, { method: "POST" }));
                if (r?.checkout_url) router.push(`/${locale}${r.checkout_url}`);
                else reload();
              }}>{t("orders.pay", { amount: money(o.total_minor, cur, locale) })}</button>
            </div>
          )}
          <button className="btn ghost small" onClick={() => run(() => api(`/hardware/orders/${o.id}/cancel`, { method: "POST" }))}>{t("orders.cancel")}</button>
        </section>
      )}

      {o.parcels.length > 0 && (
        <section className="panel stack">
          <h2>{t("orders.parcels")}</h2>
          {o.parcels.map((p: any) => (
            <div key={p.id} className="panel flat stack">
              <div className="spread">
                <strong>{t("orders.parcel", { n: p.number })}</strong>
                <span><StatusBadge ns="parcel_status" value={p.status} /> <span className="hint">{t("orders.source")}: {t(`sources.${p.status_source ?? "supplier"}`)}</span></span>
              </div>
              <div className="hint">{p.carrier ?? "—"} · {p.tracking_number ?? "—"} · {p.items.map((x: any) => `${o.items.find((i: any) => i.id === x.order_item_id)?.sku} × ${x.quantity}`).join(", ")}</div>
              {p.status === "label_created" && <div className="hint">{t("orders.label_not_shipped")}</div>}
              <ol className="steps">{p.events.map((e: any, i: number) => (
                <li key={i}><StatusBadge ns="parcel_status" value={e.status} /> {date(e.occurred_at, locale, true)} — <span className="hint">{e.description} ({t(`sources.${e.source}`)})</span></li>))}</ol>
              <a href={`/api/v1/hardware/orders/${o.id}/documents/packing-list?parcel=${p.id}&format=pdf`} target="_blank" rel="noreferrer">{t("orders.packing_list")}</a>
            </div>
          ))}
        </section>
      )}

      {o.items.some((it: any) => it.shipped_qty > it.returned_qty) && (
        <section className="panel stack">
          <h2>{t("returns.title")}</h2>
          {o.returns.map((r: any) => (
            <div key={r.id} className="panel flat stack">
              <div><StatusBadge ns="return_status" value={r.status} /> {r.reason}</div>
              {r.decision_note && <div className="hint">{r.decision_note}</div>}
              {r.status === "approved" && (
                <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api(`/hardware/orders/${o.id}/returns/${r.id}/ship`, { body: { tracking_number: rtrack } })); }}>
                  <input placeholder={t("returns.tracking")} value={rtrack} onChange={(e) => setRtrack(e.target.value)} required style={{ maxWidth: 260 }} />
                  <button className="btn small">{t("returns.shipped")}</button>
                </form>
              )}
            </div>
          ))}
          <form className="stack" onSubmit={(e) => {
            e.preventDefault();
            const items = Object.entries(ret).filter(([, q]) => q > 0).map(([order_item_id, quantity]) => ({ order_item_id, quantity }));
            run(() => api(`/hardware/orders/${o.id}/returns`, { body: { items, reason } }));
          }}>
            <p className="hint">{t("returns.separate")}</p>
            {o.items.filter((it: any) => it.shipped_qty > it.returned_qty).map((it: any) => (
              <label key={it.id} className="field">{it.title} ({t("returns.max", { count: it.shipped_qty - it.returned_qty })})
                <input type="number" min={0} max={it.shipped_qty - it.returned_qty} value={ret[it.id] ?? 0} onChange={(e) => setRet({ ...ret, [it.id]: Number(e.target.value) })} style={{ maxWidth: 120 }} /></label>
            ))}
            <label className="field">{t("returns.reason")}<textarea value={reason} onChange={(e) => setReason(e.target.value)} required /></label>
            <div><button className="btn ghost">{t("returns.request")}</button></div>
          </form>
        </section>
      )}
      <ErrorBox error={act.error} />
    </div>
  );
}

export default function OrderPage() {
  return <div className="section"><RequireAuth><Order /></RequireAuth></div>;
}
