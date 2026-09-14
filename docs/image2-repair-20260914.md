# Second-photo repair checkpoint

Input: `575892bb4169954d5d4f10789c54d9c4.jpg`.
Baseline: `2d0a09e`. No change to the uploaded images or existing backups.

## Causes and changes

1. With the top-edge relation selected, the upper diamond strip had one remaining
   degree of freedom. Legal directions alone do not require the two diamonds to
   have equal heights. The three intermediate horizontal strokes therefore could
   not become exact through ordinary intersection propagation.
2. The already-proved strip height is `(sqrt(2)-1)/2` of the paper side.
   The complete source-observed run supplies quarters of that interval, not
   independently fitted coordinates. Both opposite paper edges must contain the
   same lines in the same order, with every member present and within the stricter
   spacing tolerance. The operation retains its bounds, parents, indices, and
   supporting observations in the geometry/proof reports.
3. A 7.66-analysis-pixel blue detector fragment was wholly inside a longer blue
   vertical stroke but misclassified as a separate 112.5-degree line. It caused a
   dangling segment and pixel-endpoint fallback. Such embedded short echoes are
   now recorded separately, not constructed. Distinct-color strokes, branches
   leaving the stroke width, and strokes outside its finite interval are retained.

## Actual export verification

- Top-edge relation `boundary-d4373910e8ef`: 57 independent crease lines,
  170 internal CP segments, no unresolved crease, no pixel endpoint fallback,
  no non-22.5-degree segment, and no built-in CP-contract/cAMV blocker.
- Independently checked the eight red diamond edges, three complete horizontal
  stripes, their exact coordinates, and their red/blue assignments.
- First-photo and trex CP geometry and colors unchanged to six decimal places.
- The first tested left-edge relation yields zero angle/MV errors but still has
  one boundary-contact blocker. The first right-edge relation still yields
  incorrect output despite also reporting 57 exact lines. Line count is not a
  valid start-point ranking criterion on its own.

Follow-up: recommend a start using the resulting CP checks and image agreement;
do not automatically skip manual selection merely because all lines are marked
exact. Browser UI, preview deployment, and GitHub publication were not changed
by this checkpoint.
