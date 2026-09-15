// Start selection is advisory. It never changes the current CP or user choice.
export const MAX_START_TRIALS = 3;

function pointOnRelation(relation, maximum) {
  return (relation.points || []).find(point => {
    const xy = point.observed_point_px || point.point_px;
    if (!Array.isArray(xy) || xy.length !== 2 || !xy.every(Number.isFinite)) return false;
    // Prefer a real non-corner contact so the highlighted point is meaningful.
    return !xy.every(v => v <= 1 || v >= maximum - 1);
  });
}

export function shortlistStarts(root) {
  const shadow = root?.shadow_search || {};
  const maximum = Number(shadow.raw_crease_topology?.maximum_coordinate_px
    ?? root?.stats?.analysis_size_used - 1);
  const boundary = (shadow.boundary_relation_candidates || []).flatMap(relation => {
    const point = pointOnRelation(relation, maximum);
    return point && relation.id ? [{
      kind: 'boundary_relation', id: String(relation.id),
      side: relation.side, point: point.observed_point_px || point.point_px,
    }] : [];
  });
  const internal = (shadow.topology_point_start_candidates || [])
    .filter(point => point.selectable && point.id && Array.isArray(point.observed_point_px))
    .map(point => ({
      kind: 'topology_point', id: String(point.id),
      side: 'interior', point: point.observed_point_px,
    }));
  const seenSides = new Set();
  const diverse = [...boundary, ...internal].filter(choice => {
    if (seenSides.has(choice.side)) return false;
    seenSides.add(choice.side);
    return true;
  });
  return [...diverse, ...boundary, ...internal]
    .filter((choice, index, all) => all.findIndex(c => c.kind === choice.kind && c.id === choice.id) === index)
    .slice(0, MAX_START_TRIALS);
}

export function startTrialKey(steps) {
  return JSON.stringify(steps.map(({ kind, id }) => ({ kind, id: String(id) })));
}

export function createTrialCache(evaluate) {
  const promises = new Map();
  return {
    get(steps) { return promises.get(startTrialKey(steps)); },
    run(steps) {
      const key = startTrialKey(steps);
      if (!promises.has(key)) {
        const promise = Promise.resolve().then(() => evaluate(steps));
        promises.set(key, promise);
        promise.catch(() => { if (promises.get(key) === promise) promises.delete(key); });
      }
      return promises.get(key);
    },
  };
}

export function assessStartReport(report, root) {
  const contract = report?.cp_output_contract;
  const segments = Number(contract?.draft_internal_segment_count);
  if (!report?.enabled || !report.cp_available || !report.cp?.trim()
      || !Number.isFinite(segments) || segments <= 0 || !contract) return null;
  const unresolved = Number(report.unexplained_observations);
  const fallback = Number(contract.draft_observed_endpoint_fallback_count);
  const required = Number(report.required_observations);
  if (![unresolved, fallback, required].every(Number.isFinite) || required <= 0) return null;
  const blockers = Array.isArray(contract.blockers) ? contract.blockers : null;
  if (!blockers) return null;
  const violations = blockers.filter(b => b.code === 'camv_foldability_violations')
    .flatMap(b => b.violations || []);
  const geometryBlockers = blockers.filter(b => b.code !== 'camv_foldability_violations');
  const mvOnly = violations.length > 0 && violations.every(v => v.rule === 'maekawa')
    && Number(root?.stats?.raw_mv_default_mountain_segment_count) > 0;
  const verified = report.checks_passed === true && blockers.length === 0
    && unresolved === 0 && fallback === 0;
  const geometryOnly = !verified && mvOnly && geometryBlockers.length === 0
    && unresolved === 0 && fallback === 0;
  const residuals = (report.geometry_graph?.entities || [])
    .map(e => e.exact_geometry?.observed_residual_px).filter(Number.isFinite);
  const residual = residuals.length
    ? residuals.reduce((a, b) => a + b, 0) / residuals.length : Infinity;
  const status = verified ? 'verified' : geometryOnly ? 'geometry_only' : 'provisional';
  return {
    status, segments, unresolved, fallback,
    blockers: blockers.reduce((n, b) => n + Number(b.count || 1), 0),
    stop: verified || geometryOnly,
    score: [
      verified ? 0 : geometryOnly ? 1 : 2,
      unresolved / required, fallback,
      geometryBlockers.reduce((n, b) => n + Number(b.count || 1), 0),
      violations.length, residual,
    ],
  };
}

export function betterStart(candidate, previous) {
  if (!previous) return true;
  for (let i = 0; i < candidate.quality.score.length; i += 1) {
    if (candidate.quality.score[i] !== previous.quality.score[i])
      return candidate.quality.score[i] < previous.quality.score[i];
  }
  return false;
}

export async function evaluateStartCandidates(choices, evaluate, active, onTrial, root) {
  let best = null;
  for (const [index, choice] of choices.slice(0, MAX_START_TRIALS).entries()) {
    if (!active()) break;
    onTrial(index + 1);
    try {
      const report = await evaluate([{ kind: choice.kind, id: choice.id }]);
      if (!active()) break;
      const quality = assessStartReport(report, root);
      if (!quality) continue;
      const candidate = { choice, quality };
      if (betterStart(candidate, best)) best = candidate;
      if (quality.stop) break;
    } catch (_) {
      // One failed trial must not disable manual selection or discard a prior result.
    }
  }
  return best;
}
