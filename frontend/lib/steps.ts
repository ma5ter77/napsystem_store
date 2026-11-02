// NAP-092: statuses as a sequence of steps a person understands, plus what happens next.
// Pure functions (unit-tested in scripts/test-steps.mjs); <StatusTimeline> renders them.
export type StepState = "done" | "current" | "upcoming" | "problem";
export type Step = { key: string; state: StepState };
export type Steps = { steps: Step[]; next: string };

function line(keys: string[], current: number, problem = false): Step[] {
  return keys.map((key, i) => ({ key, state: i < current ? "done" : i === current ? (problem ? "problem" : "current") : "upcoming" }));
}

/** Audio conversion of an author's upload: file check → decoding → rendering → quality control → package. */
export const CONVERSION_STAGES = ["validating", "decoding", "rendering", "quality_check", "packaging"] as const;

export function conversionSteps(job: { state: string; failed_stage?: string | null }): Steps {
  const keys = [...CONVERSION_STAGES];
  if (job.state === "completed") return { steps: line(keys, keys.length), next: "completed" };
  if (job.state === "queued") return { steps: line(keys, 0), next: "queued" };
  if (job.state === "failed" || job.state === "canceled") {
    // mark the stage it stopped at only when the server knows it (jobs failed before NAP-092 do not say)
    const at = keys.indexOf((job.failed_stage ?? "") as (typeof keys)[number]);
    if (job.state === "failed" && at >= 0) return { steps: line(keys, at, true), next: "failed" };
    return { steps: keys.map((key) => ({ key, state: "upcoming" as const })), next: job.state };
  }
  const i = keys.indexOf(job.state as (typeof keys)[number]);
  return { steps: line(keys, i < 0 ? 0 : i), next: "running" };
}

/** An app version from the author's side: preparation → moderation → approved → in the store. */
export function versionSteps(v: { state: string }, autoRelease = false): Steps {
  const keys = ["draft", "submitted", "approved", "published"];
  switch (v.state) {
    case "draft": return { steps: line(keys, 0), next: "draft" };
    case "submitted": return { steps: line(keys, 1), next: autoRelease ? "submitted_auto" : "submitted" };
    case "approved": return { steps: line(keys, 2), next: "approved" };
    case "published": return { steps: line(keys, keys.length), next: "published" };
    case "rejected": return { steps: line(keys, 1, true), next: "rejected" };
    case "withdrawn": return { steps: line(keys, 0, true), next: "withdrawn" };
    default: return { steps: line(keys, keys.length), next: "archived" };   // archived: a newer version replaced it
  }
}

/** A buyer's refund request: sent → decision → money back. */
export function refundSteps(r: { status: string }): Steps {
  const keys = ["requested", "approved", "refunded"];
  switch (r.status) {
    case "requested": return { steps: line(keys, 1), next: "requested" };
    case "pending": return { steps: line(keys, 2), next: "pending" };
    case "succeeded": return { steps: line(keys, keys.length), next: "succeeded" };
    case "failed": return { steps: line(keys, 2, true), next: "failed" };
    case "rejected": return { steps: line(keys, 1, true), next: "rejected" };
    default: return { steps: line(keys, 0), next: "requested" };
  }
}
