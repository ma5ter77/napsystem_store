"use client";

// NAP-084: the moderator edits any product card, publishes, takes off sale, deletes and restores it.
import { useParams } from "next/navigation";
import { Suspense } from "react";
import { ProductEditor } from "@/components/ProductEditor";
import { RequireAuth } from "@/components/ui";

function Page() {
  const { id } = useParams<{ id: string }>();
  return <ProductEditor mode="moderator" id={id} />;
}

export default function ModeratorProductPage() {
  return <div className="section"><RequireAuth role={["moderator", "admin"]}><Suspense><Page /></Suspense></RequireAuth></div>;
}
