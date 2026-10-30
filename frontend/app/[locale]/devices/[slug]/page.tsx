import type { Metadata } from "next";
import { ProductDetail } from "@/components/ProductDetail";
import { isLocale, Locale } from "@/lib/i18n";
import { alternates, serverApi } from "@/lib/server";

type P = Promise<{ locale: string; slug: string }>;

export async function generateMetadata({ params }: { params: P }): Promise<Metadata> {
  const { locale, slug } = await params;
  const r = await serverApi<any>(`/hardware/products/${slug}`, locale);
  if (!r.ok) return {};
  const base = r.data.kind === "simple" ? "devices" : "kits";
  return { title: r.data.title, description: r.data.summary, alternates: alternates(locale, `/${base}/${slug}`) };
}

export default async function DevicePage({ params }: { params: P }) {
  const { locale, slug } = await params;
  return <ProductDetail locale={(isLocale(locale) ? locale : "ru") as Locale} slug={slug} />;
}
