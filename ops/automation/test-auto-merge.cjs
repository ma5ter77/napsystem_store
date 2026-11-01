const assert = require('node:assert/strict');
const run = require('./auto-merge.cjs');
async function scenario(change={}) {
  let merged=0, dispatched=0;
  const pr={number:1,user:{login:'ma5ter77'},draft:false,state:'open',head:{sha:'head',repo:{full_name:'BrothersB/nap-store'}},...change.pr};
  const github={paginate:async(fn,args)=>fn(args),rest:{
    pulls:{list:async()=>[pr], get:async()=>({data:pr}),listFiles:async()=>[{filename:change.file||'frontend/app/page.tsx'}],merge:async args=>{assert.equal(args.sha,'head'); merged++;return {data:{merged:true,sha:'merged'}};}},
    repos:{getBranch:async()=>({data:{commit:{sha:'base'}}}),compareCommitsWithBasehead:async()=>({data:{merge_base_commit:{sha:change.stale?'old':'base'}}})},
    actions:{listWorkflowRuns:async args=>({data:{workflow_runs:change.missing?[]:[{id:2,status:'completed',conclusion:args.workflow_id==='release-acceptance.yml'?'failure':args.workflow_id==='vps-acceptance.yml'?(change.vps||'failure'):(change.result||'success'),pull_requests:[{number:change.wrongPR?2:1}]}]}}),createWorkflowDispatch:async args=>{assert.equal(args.ref,'master');dispatched++;}}
  }};
  await run({github,context:{repo:{owner:'BrothersB',repo:'nap-store'}},core:{info:()=>{}}});
  return {merged,dispatched};
}
(async()=>{
  assert.deepEqual(await scenario(),{merged:1,dispatched:1});
  assert.deepEqual(await scenario({file:'ops/vps/napdeploy.py',vps:'success'}),{merged:1,dispatched:1});
  for(const c of [{pr:{user:{login:'ma5tre77'}}},{pr:{draft:true}},{pr:{head:{sha:'head',repo:{full_name:'other/fork'}}}},{stale:true},{missing:true},{result:'failure'},{result:'cancelled'},{wrongPR:true},{file:'ops/cohost/release_control.py'},{file:'ops/vps/napdeploy.py'},{file:'.github/workflows/deliver-vps.yml'}]) {
    assert.deepEqual(await scenario(c),{merged:0,dispatched:0});
  }
  console.log('13 auto-merge cases PASS');
})().catch(e=>{console.error(e);process.exit(1)});
