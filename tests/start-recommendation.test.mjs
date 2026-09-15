import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assessStartReport, createTrialCache, evaluateStartCandidates, shortlistStarts } from '../web/start-recommendation.mjs';

const report = (overrides = {}) => ({
  enabled: true, checks_passed: true, cp_available: true, cp: '2 0 0 1 1',
  required_observations: 57, unexplained_observations: 0,
  cp_output_contract: { draft_internal_segment_count: 170, draft_observed_endpoint_fallback_count: 0, blockers: [] },
  ...overrides,
});
const choices = Array.from({ length: 10 }, (_, i) => ({ kind: 'boundary_relation', id: String(i) }));
const unresolved = () => report({
  checks_passed: false, unexplained_observations: 5,
  cp_output_contract: {
    draft_internal_segment_count: 180, draft_observed_endpoint_fallback_count: 15,
    blockers: [{ code: 'unresolved_finite_segment_endpoints', count: 15 }],
  },
});

test('fewer complete lines beat a larger incorrect CP; stop at first verified start', async () => {
  let calls = 0;
  const best = await evaluateStartCandidates(choices, async () => ++calls === 1 ? unresolved() : report(),
    () => true, () => {}, {});
  assert.equal(calls, 2);
  assert.equal(best.choice.id, '1');
  assert.equal(best.quality.status, 'verified');
});

test('at most three trials; unverified results stay explicitly provisional', async () => {
  let calls = 0;
  const best = await evaluateStartCandidates(choices, async () => { calls++; return unresolved(); },
    () => true, () => {}, {});
  assert.equal(calls, 3);
  assert.equal(best.quality.status, 'provisional');
});

test('manual selection cancels subsequent trials and ignores the pending recommendation', async () => {
  let active = true;
  let calls = 0;
  const best = await evaluateStartCandidates(choices, async () => {
    calls++; active = false; return report();
  }, () => active, () => {}, {});
  assert.equal(calls, 1);
  assert.equal(best, null);
});

test('clicking the chosen or in-flight start reuses the same trial', async () => {
  let calls = 0;
  const cache = createTrialCache(async () => { calls++; return report(); });
  const steps = [choices[0]];
  const pending = cache.run(steps);
  assert.equal(cache.get(structuredClone(steps)), pending);
  assert.equal(cache.run(steps), pending);
  await pending;
  await cache.run(steps);
  assert.equal(calls, 1);
  const otherImage = createTrialCache(async () => { calls++; return report(); });
  await otherImage.run(steps);
  assert.equal(calls, 2);
});

test('failed trial may be retried and does not poison manual selection', async () => {
  let calls = 0;
  const cache = createTrialCache(async () => {
    if (++calls === 1) throw new Error('temporary failure');
    return report();
  });
  await assert.rejects(cache.run([choices[0]]));
  await cache.run([choices[0]]);
  assert.equal(calls, 2);
});

test('missing checks, empty output, or unreported coverage cannot pass as verified', () => {
  assert.equal(assessStartReport(report({ cp: '' }), {}), null);
  assert.equal(assessStartReport(report({ required_observations: undefined }), {}), null);
  assert.equal(assessStartReport(report({ checks_passed: false }), {}).status, 'provisional');
  assert.equal(assessStartReport(unresolved(), {}).stop, false);
});

test('unknown source MV can yield geometry-only recommendation, never full verification', () => {
  const mv = report({ checks_passed: false,
    cp_output_contract: {
      draft_internal_segment_count: 18, draft_observed_endpoint_fallback_count: 0,
      blockers: [{ code: 'camv_foldability_violations', count: 5, violations: [{ rule: 'maekawa' }] }],
    },
  });
  assert.equal(assessStartReport(mv, {}).status, 'provisional');
  assert.equal(assessStartReport(mv, { stats: { raw_mv_default_mountain_segment_count: 18 } }).status, 'geometry_only');
  mv.cp_output_contract.blockers[0].violations[0].rule = 'number_of_folds';
  assert.equal(assessStartReport(mv, { stats: { raw_mv_default_mountain_segment_count: 18 } }).status, 'provisional');
});

test('shortlist avoids repeated sides, selects an actual non-corner point, and supports interior starts', () => {
  const root = { stats: { analysis_size_used: 101 }, shadow_search: {
    boundary_relation_candidates: ['top', 'top', 'left', 'left'].map((side, i) => ({
      id: String(i), side, points: [{ point_px: [0, 0] }, { observed_point_px: [50, 0] }],
    })),
    topology_point_start_candidates: [{ id: 'inside', selectable: true, observed_point_px: [50, 50] }],
  } };
  const original = structuredClone(root);
  const list = shortlistStarts(root);
  assert.deepEqual(list.map(c => c.id), ['0', '2', 'inside']);
  assert.deepEqual(list[0].point, [50, 0]);
  assert.deepEqual(root, original);
});
