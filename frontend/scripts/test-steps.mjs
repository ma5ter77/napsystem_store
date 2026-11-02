// Unit tests of the status timelines (NAP-092): every state maps to steps and a "what's next" text key.
import assert from "node:assert/strict";
import test from "node:test";
import { conversionSteps, refundSteps, versionSteps } from "../lib/steps.ts";

const states = (s) => s.steps.map((x) => x.state).join(",");

test("conversion: running stages, completed, failure at the stage it stopped", () => {
  assert.equal(states(conversionSteps({ state: "queued" })), "current,upcoming,upcoming,upcoming,upcoming");
  assert.equal(states(conversionSteps({ state: "rendering" })), "done,done,current,upcoming,upcoming");
  assert.equal(conversionSteps({ state: "rendering" }).next, "running");
  assert.equal(states(conversionSteps({ state: "completed" })), "done,done,done,done,done");
  const f = conversionSteps({ state: "failed", failed_stage: "quality_check" });
  assert.equal(states(f), "done,done,done,problem,upcoming");
  assert.equal(f.next, "failed");
  // unknown stage (failed before the stage was recorded) or cancelled: no false "problem here" marker
  assert.ok(!states(conversionSteps({ state: "failed", failed_stage: null })).includes("problem"));
  assert.equal(conversionSteps({ state: "canceled" }).next, "canceled");
});

test("version: draft → moderation → approved → published, rejection and withdrawal", () => {
  assert.equal(states(versionSteps({ state: "draft" })), "current,upcoming,upcoming,upcoming");
  assert.equal(versionSteps({ state: "submitted" }, true).next, "submitted_auto");
  assert.equal(versionSteps({ state: "submitted" }, false).next, "submitted");
  assert.equal(states(versionSteps({ state: "rejected" })), "done,problem,upcoming,upcoming");
  assert.equal(states(versionSteps({ state: "published" })), "done,done,done,done");
  assert.equal(versionSteps({ state: "archived" }).next, "archived");
});

test("refund: under review → approved → money back, decline and provider failure", () => {
  assert.equal(states(refundSteps({ status: "requested" })), "done,current,upcoming");
  assert.equal(states(refundSteps({ status: "pending" })), "done,done,current");
  assert.equal(states(refundSteps({ status: "succeeded" })), "done,done,done");
  assert.equal(states(refundSteps({ status: "rejected" })), "done,problem,upcoming");
  assert.equal(states(refundSteps({ status: "failed" })), "done,done,problem");
});
