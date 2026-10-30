import type { Metadata } from "next";
import { ProductDetail } from "@/components/ProductDetail";
import { isLocale, Locale } from "@/lib/i18n";
import { alternates, serverApi } from "@/lib/server";

type P = Promise<{ locale: string; slug: string }>;

export async function generateMetadata({ params }: { params: P }): Promise<Metadata> {
  const { locale, slug } = await params;
  const r = await serverApi<any>(`/hardware/products/${slug}`, locale);
  if (!r.ok) return {};
  return { title: r.data.title, description: r.data.summary, alternates: alternates(locale, `/kits/${slug}`) };
}

export default async function KitPage({ params, searchParams }: { params: P; searchParams: Promise<{ app?: string }> }) {
  const { locale, slug } = await params;
  const { app } = await searchParams;
  return <ProductDetail locale={(isLocale(locale) ? locale : "ru") as Locale} slug={slug} app={app} />;
}
