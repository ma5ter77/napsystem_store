// NAP-044: any unknown address under /ru or /en shows the localized "not found" page with the site layout.
import { notFound } from "next/navigation";

export default function Unknown() {
  notFound();
}
