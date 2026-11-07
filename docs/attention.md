# Attention in Neurotree

What "Attention Is All You Need" (Vaswani et al., 2017, arXiv:1706.03762) changed in Neurotree, what it did not,
and the measurements behind each decision. Code: `neurotree/keys.py` (encode once), `neurotree/attend.py`
(attend many times), `neurotree/bench.py` (the measurements).

## The idea, plainly

A Transformer lets every query look at every position at once, weigh them with a softmax, and mix what several
"heads" saw. Neurotree had a fixed ranking: the same importance for a file whatever you were asking about. Now a
question attends to all ~52,000 nodes (projects, files, functions, claude-mem memories) and gets back the few that
hold most of the attention, each with the reason why. That is what `/neurotree:scan` uses.

## What carried over

| In the paper | In Neurotree |
|---|---|
| Scaled dot-product attention, softmax over all positions | Each head scores every node and turns the scores into a distribution with a softmax. The paper's 1/√d keeps the softmax out of saturation; here a temperature does that job (cosines are already normalized). |
| Multi-head attention: several heads look at different representation subspaces, their outputs are mixed | Two heads: **words** (BM25 over each node's key: name split into words, path words, the first line the file says about itself, its functions, its memory titles) and **meaning** (cosine of static code embeddings, potion-code-16M, 256 dims). Mixed with equal weights. |
| Positional encoding | Two biases added to the logits, like a position bias: the **project you are in**, and **what you work on** at four time scales (attention half-lives of 1, 7, 30 and 120 days, a geometric series like the paper's wavelengths). A bias reorders what matched; it never brings in a node nothing matched. |
| Self-attention, residual connections | One round of attention along **import** edges: 15 % of each top file's attention flows to the modules it imports or is imported by, 85 % stays (the residual). Attention is conserved, so the result is still a distribution. |
| Restricted self-attention for long sequences (§4: attend to a neighborhood of size r) | Heads keep their top 400 nodes; the graph round only looks at the neighbors of the top 40 files. A query takes ~40 ms instead of 52,000² pairs. |
| Encoder–decoder: encode the source once, every decoding step attends to it | Keys are built once per index run (an SQLite FTS5 table and an embedding matrix inside graph.db); a question only reads them. |
| Attention visualizations | Every result says why: which head matched, which function or memory carried it, which import pulled it in, how often Claude touched it. |
| Layer normalization | Already there: each signal is a percentile within its level. Kept. |

## What was left out, and why

| In the paper | Why it would make Neurotree worse |
|---|---|
| Learned projections W_Q, W_K, W_V, W_O; training | The only labels are ~116 past questions. Tuning just 7 settings on the older 74 made the newer 42 *worse* (nDCG@10 0.129 → 0.062). Training a model on one person's Mac would overfit the same way, cost real energy, and lose the explanations. The weights stay fixed and readable. |
| A stack of 6 layers | 2 or 3 graph rounds measured no better than 1 (nDCG@10 0.148 → 0.151 / 0.155, within noise, P@3 slightly down): after a few rounds everything looks related to everything (over-smoothing). One round ships. |
| Dense attention over all pairs | 52,000² pairs to answer one question: energy for nothing. |
| A large embedding model | A 100M+ parameter encoder would need the GPU on every reindex and gigabytes of disk. The static model encodes all 52k nodes in ~1.2 s on the CPU, is 32 MB, and was already on this Mac (semble uses it). |
| "Worked on together" links in the graph round | Files touched in the same Claude session: measured slightly worse for ranking. They stay in `neurotree node` as information. |
| Multi-scale time in the tree's own importance (the 3D layout) | Predicting next week's attention: files nDCG@50 0.054 vs 0.057, projects nDCG@10 0.348 vs 0.326 (±0.13) — no gain, so `scoring.py` keeps its single 30-day half-life. Multi-scale time is only used inside a question's prior. |
| Dropout, label smoothing, warmup, beam search, byte-pair encoding | Tools for training and generating. Nothing here is trained or generated. |
| Periodic (sinusoidal) time features | Time of day or weekday patterns would mostly add noise to "what matters for this question". |

## How it was measured

`python -m neurotree bench` replays your own past. Every prompt in a main Claude Code session that led to edits
is a question (the prompt is read in memory, never stored or printed); its answer is the files edited before the
next prompt that already existed. Subagent prompts count too, with file paths cut out (otherwise the answer is in
the question), since they are written the way Claude will ask Neurotree. Every setting is replayed **as of the
moment the question was asked**: it sees neither the session's own touches nor memory written later.

116 questions (63 of your prompts, 53 agent prompts) from 93 sessions; Claude Code keeps about a month of
sessions, so that is what exists. With this little data only big differences are real; the paired bootstrap
intervals below say which.

| Setting (each row adds one idea) | P@1 | P@3 | R@10 | nDCG@10 |
|---|---|---|---|---|
| Neurotree before: importance ranking, no question | 0.12 | 0.20 | 0.00 | 0.00 |
| before + the project you are in | 0.26 | 0.32 | 0.08 | 0.05 |
| 1 head: words (BM25) | 0.27 | 0.41 | 0.16 | 0.10 |
| 1 head: meaning (embeddings) | 0.20 | 0.34 | 0.13 | 0.10 |
| 2 heads, equal weights | 0.32 | 0.45 | 0.19 | 0.12 |
| + prior, 4 time scales | 0.32 | 0.47 | 0.17 | 0.12 |
| + project you are in | 0.37 | 0.49 | 0.20 | 0.14 |
| + 1 graph round over imports (**shipped**) | **0.37** | **0.49** | **0.22** | **0.15** |

P@1 / P@3: the right project is first / in the top 3. R@10: share of the answer files in the top 10.
nDCG@10: ranking quality of files (1 = perfect). ±0.05 for nDCG@10 at 95 %.

Shipped vs. the best single head, paired bootstrap, 95 % interval:
- nDCG@10 +0.049 [+0.008, +0.094] vs words only, +0.052 [+0.025, +0.080] vs meaning only
- right project first (P@1) +0.10 [+0.03, +0.17] vs words only
- the two biases on top of the two heads: nDCG@10 +0.027 [+0.001, +0.054]
- the graph round alone: +0.006 [−0.001, +0.016], the time prior alone: +0.006 [−0.019, +0.028] — not proven
  either way; both are kept because they do not hurt, cost a few ms, and give the "imported by" and "touched N
  times" reasons.

Absolute numbers are modest because the task is hard: a prompt says what you want, not which of 32,000 files will
change, and most misses are the right project with a neighboring file. That is why the plugin shows projects and
files, and Claude reads around what it points to.

## Improved by heartgold (November 2026)

[heartgold](https://github.com/Malek1414/heartgold) ran on this repo in its metric mode, which raises a measured score
and keeps only what holds up on unseen data. The inputs were frozen: the index as of 5 Nov, and 110 questions from
sessions before it. They were split the way bench splits them: whole sessions, the older 60 % to tune on (70
questions) and the newer 40 % held out (40 questions). Agents only ever saw scores on the tune split
(`python -m neurotree.evaljson --split tune`). The held-out split prints only for heartgold's own check
(`HEARTGOLD_HOLDOUT=1`). Any change that made held-out worse was dropped.

| | tune P@1 | tune nDCG@10 | held-out P@1 | held-out nDCG@10 |
|---|---|---|---|---|
| before | 0.400 | 0.158 | 0.550 | 0.169 |
| **after (kept: reciprocal rank fusion)** | **0.457** | **0.189** | **0.600** | **0.195** |

Held-out change, paired bootstrap, 95 %, n = 40: P@1 +0.050 [+0.000, +0.125], nDCG@10 +0.026 [−0.030, +0.080].
That is a likely gain, not a proven one; the set is small.

- **What changed.** The two heads are combined by reciprocal rank fusion (each head ranks its own candidates,
  score = Σ 1/(k + rank)) instead of softmax-averaging raw scores. The bias is applied after fusion. Several
  constants were retuned along with it.
- **What was tried and dropped.** Eight more ideas over two rounds: a saturating mass rollup, query-shape gating,
  Rocchio and RM3 query expansion, tuning k, max-dominant aggregation, a calibrated score blend, and a direct
  project-scoring stage. The project-scoring stage raised tune P@1 to 0.486 but lowered held-out P@1 to 0.575, so it
  was rejected as overfit. That is the same lesson as the tuning note above.
- **Cost.** Queries take longer: median 26 → 44 ms, p90 42 → 77 ms, measured on 60 questions, cold memo. The
  heartgold runs cost $12.34 in agent time.

## Also fixed along the way

- **Moved projects lost their history.** Projects that moved (e.g. `~/Desktop/simulacrum` → `~/Desktop/Projects/...`)
  dropped every Claude touch from before the move. Paths are now relocated to the project of the same name;
  agent worktrees (`<repo>/.claude/worktrees/<name>/x`) count for `<repo>/x`. Same 1,047 session logs, old vs new
  code: 19,345 → 21,722 session events (+12 %), 8,929 → 9,864 memory events (+10 %), 1,789 → 2,200 memory facts.
- **Copies of a file in sibling repos** (same relative path, same first line) collapse into one result that says
  where the other copies are.
