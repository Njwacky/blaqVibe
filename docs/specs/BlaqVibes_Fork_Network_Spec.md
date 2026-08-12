# Fork Network Graph — Spec (5 Whys)

**Why network not just forks list?** List shows 1 level, network shows tree: original → fork → fork-of-fork. At 100 forks, you need to see who remixed whom.

**Why backend not JS?** Fork tree is DB query `forked_from` FK, not client calc. Backend builds tree JSON, JS renders SVG, no secrets.

**Why not D3 heavy?** 10 forks → simple CSS tree is faster, no 100KB D3. D3 later if 100+.

**Why at scale?** Trending boost can use `forks.count` — network shows virality.

**Why crush silently?** Circular fork (A forks B, B forks A) → detect loop, break, log.
