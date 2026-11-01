// NAP-092: one hardware order as a sequence of steps a buyer understands, plus the next thing that happens.
export type StepState = "done" | "current" | "upcoming" | "problem";
export type Step = { key: string; state: StepState };

const PAID = ["paid", "partially_refunded", "refunded"];

export function orderSteps(o: { status: string; payment_status: string; fulfillment_status: string }): { steps: Step[]; next: string } {
  const paid = PAID.includes(o.payment_status);
  const f = o.fulfillment_status;
  const shipped = ["shipped", "delivered"].includes(f);
  const order = ["placed", "paid", "packing", "shipped", "delivered"];
  let current = 1;
  if (paid) current = 2;
  if (f === "partially_shipped" || shipped) current = 3;
  if (shipped) current = 4;
  if (f === "delivered") current = 5;
  const problem = o.status === "cancelled" || f === "exception" || o.payment_status === "failed";
  const steps = order.map((key, i): Step => ({
    key,
    state: i < current ? "done" : i === current ? (problem ? "problem" : "current") : "upcoming",
  }));
  let next = "paying";
  if (o.status === "cancelled") next = "cancelled";
  else if (o.status === "closed") next = "closed";
  else if (o.payment_status === "failed") next = "payment_failed";
  else if (o.status === "awaiting_quote") next = "quote";
  else if (!paid) next = "paying";
  else if (f === "exception") next = "exception";
  else if (f === "delivered") next = "delivered";
  else if (f === "shipped") next = "shipped";
  else if (f === "partially_shipped") next = "partially_shipped";
  else next = "packing";
  return { steps, next };
}
