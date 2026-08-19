# Construction Search v2

This branch changes the reconstruction problem from a locally greedy propagation chain into a constrained search over candidate construction explanations.

The search keeps **observed geometry** and **construction provenance** separate. Raster evidence says that a line or focus probably exists; provenance says how an exact point or line could have been constructed. A node may retain several provenance candidates at once, such as direct continuation, intersection, midpoint/division, symmetry, algebraic seed, boundary construction, or a user-supplied core point.

The selected result is a construction DAG. The score is evaluated on the whole selected route and combines unexplained evidence, image residual, construction description cost, generation depth, and optional cAMV violations. Symmetry is deliberately not special-cased: a direct route wins when symmetry is merely a detour, while a symmetry construction may win if its extra step explains enough downstream structure to reduce the total description cost.

Large `a+b√2` coefficients are treated the same way. They are not forbidden, because some designs can genuinely require complex values, but their description complexity is higher. This prevents a tiny raster-error improvement from automatically beating a much simpler geometric explanation.

cAMV is a global ranking signal rather than a construction rule. A cAMV problem may come from a missing line, endpoint error, or another local defect, so it can lower the score of a candidate state but must not directly instruct the search to use symmetry or any other construction type.

Isolated take-lines are intended to be handled after the main construction graph stabilizes. A high-confidence residual line that does not feed later core construction may be explained locally (for example by fitted position, rational division, or a simple radical relation) and reported to the user without contaminating the main DAG.
