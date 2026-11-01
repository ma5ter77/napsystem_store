// Runs from the default branch, never from a pull request checkout.
module.exports = async ({github, context, core}) => {
  const repo = context.repo;
  const prs = await github.paginate(github.rest.pulls.list, {...repo, state:'open', base:'master', per_page:100});
  for (const item of prs) {
    const {data: pr} = await github.rest.pulls.get({...repo, pull_number:item.number});
    if (pr.user.login !== 'ma5ter77' || pr.draft || pr.head.repo?.full_name !== 'BrothersB/nap-store') continue;
    const {data: base} = await github.rest.repos.getBranch({...repo, branch:'master'});
    const {data: cmp} = await github.rest.repos.compareCommitsWithBasehead({...repo, basehead:`${base.commit.sha}...${pr.head.sha}`});
    if (cmp.merge_base_commit.sha !== base.commit.sha) { core.info(`PR ${pr.number}: update branch from master first`); continue; }
    const files = await github.paginate(github.rest.pulls.listFiles, {...repo,pull_number:pr.number,per_page:100});
    const required = ['ci.yml'];
    if (files.some(f => [f.filename, f.previous_filename || ''].some(name => /^(ops\/cohost\/|docker-compose.yml$|backend\/Dockerfile$|frontend\/Dockerfile$|backend\/migrations\/|\.github\/workflows\/release-acceptance.yml$)/.test(name)))) required.push('release-acceptance.yml');
    let passed = true;
    for (const workflow_id of required) {
      const {data} = await github.rest.actions.listWorkflowRuns({...repo,workflow_id,event:'pull_request',head_sha:pr.head.sha,per_page:100});
      const runs = data.workflow_runs.filter(r => r.pull_requests.some(p => p.number === pr.number)).sort((a,b) => b.id-a.id);
      if (!runs.length || runs[0].status !== 'completed' || runs[0].conclusion !== 'success') passed = false;
    }
    if (!passed) { core.info(`PR ${pr.number}: waiting for exact-head checks`); continue; }
    const {data: fresh} = await github.rest.pulls.get({...repo,pull_number:pr.number});
    const {data: lastBase} = await github.rest.repos.getBranch({...repo,branch:'master'});
    if (fresh.head.sha !== pr.head.sha || fresh.draft || fresh.state !== 'open' || lastBase.commit.sha !== base.commit.sha) continue;
    const {data: merged} = await github.rest.pulls.merge({...repo,pull_number:pr.number,sha:pr.head.sha,merge_method:'merge'});
    if (merged.merged) {
      core.info(`Merged PR ${pr.number}: ${merged.sha}`);
      // GITHUB_TOKEN merges do not trigger push workflows. Dispatch CI explicitly.
      await github.rest.actions.createWorkflowDispatch({...repo,workflow_id:'ci.yml',ref:'master'});
      break; // Remaining PRs must include the new master before acceptance.
    }
  }
};
