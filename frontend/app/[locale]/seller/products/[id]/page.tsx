"use client";

// NAP-022: supplier product editor (shared with the moderator, see components/ProductEditor.tsx)
import { useParams } from "next/navigation";
import { Suspense } from "react";
import { ProductEditor } from "@/components/ProductEditor";
import { RequireAuth } from "@/components/ui";

function Page() {
  const { id } = useParams<{ id: string }>();
  return <ProductEditor mode="seller" id={id} />;
}

export default function ProductEditorPage() {
  return <div className="section"><RequireAuth role="seller"><Suspense><Page /></Suspense></RequireAuth></div>;
}
