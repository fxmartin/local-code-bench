# Findings: DFlash (dense + speculative) vs TurboQuant (sparse MoE)

Date: 2026-06-22. Hardware: MacBook Pro M3 Max, 48 GB unified memory.

## Question

The reference articles argue that for local agentic coding, which is prefill bound
on long prompts, a sparse Mixture-of-Experts model should beat a dense model with
speculative decoding, because the MoE activates only a fraction of its parameters
per token. This note records a direct head-to-head on the project's two local
baselines to test that claim on this specific machine:

- `local-dflash-qwen`: dense `mlx-community/Qwen3.6-27B-4bit` served by DFlash with
  speculative decoding.
- `local-turboquant-qwen-moe`: sparse `manjunathshiva/Qwen3.6-35B-A3B-tq3-g32`
  (about 35B total, roughly 3B active per token) served by TurboQuant.

## Method

Runs were produced by `scripts/run-local-sweeps.sh`, which brings up one local
server at a time, warms it, sweeps it, tears it down, then does the other. This
sequential isolation is mandatory: on 48 GB the two models cannot co-reside, and
running both drives the machine into swap, which corrupts prefill timing (see the
memory-pressure note in `EVALUATION-METHODOLOGY.md`). Each model was swept across a
2k/8k/16k/24k context ladder with `--power` recording GPU and CPU power via
`powermetrics`. The result below reproduced across three runs within noise.

## Speed: Prefill And Time-To-First-Token

| Context tokens | DFlash TTFT | DFlash prefill tok/s | TurboQuant TTFT | TurboQuant prefill tok/s |
|---|---:|---:|---:|---:|
| 8000 | 8.7 s | 1227 | 22.7 s | 472 |
| 16000 | 8.5 s | 2512 | 33.8 s | 632 |
| 24000 | 9.8 s | 3256 | 66.8 s | 479 |

DFlash wins long-context prefill decisively. Its time-to-first-token stays almost
flat as context grows to 24k, while TurboQuant's climbs steeply. At 24k, DFlash is
roughly 5 to 6 times faster on both TTFT (9.8 s vs 66.8 s) and prefill rate (3256
vs 479 tok/s). Decode rates converge at long context (about 180 tok/s for both), so
the entire gap is in prefill, which is precisely the axis the articles predicted
the MoE would win.

## Energy: The Twist

| Metric (full sweep) | DFlash | TurboQuant |
|---|---:|---:|
| Average GPU power | 45.4 W | 38.6 W |
| Average combined power | 49.2 W | 42.2 W |
| Peak GPU power | 51.0 W | 56.4 W |
| Wall time | 45.6 s | 133.5 s |
| Total energy | 2244 J | 5639 J |

TurboQuant genuinely draws less instantaneous power, about 42 W combined versus 49 W,
so it runs cooler and quieter. That qualitative impression held up. But it is a
false economy. Because DFlash completes the same sweep in roughly a third of the
time, it consumes about 2.5 times less total energy (2244 J vs 5639 J). Expressed as
prompt tokens prefilled per joule, DFlash lands near 28 and TurboQuant near 12, so
DFlash is about 2.4 times more energy efficient for this workload. Lower watts over
a much longer runtime loses to higher watts over a short one.

## Verdict

On this 48 GB M3 Max, for this workload:

- Speed: DFlash wins long-context prefill by roughly 5 to 6 times.
- Energy: DFlash wins by roughly 2.5 times, despite running hotter.
- TurboQuant's only genuine advantage is lower instantaneous power and thermals,
  which matters for sustained background use but not for getting work done quickly
  or efficiently.

This is a clean counter-result to the reference series on this hardware: dense 27B
with speculative decoding beats the sparse MoE on both speed and energy, and the
MoE's cooler-running impression is real but misleading once runtime is accounted
for.

## Caveats

- One clean, isolated run per model is summarized here, but the speed result
  reproduced across three runs and the energy result across the two power-enabled
  runs. Given temperature-0 nondeterminism in local serving, treat small gaps with
  caution; the gaps here are large.
- The 2k-context cell for DFlash is excluded. It reproducibly returned empty content
  (the model produced only reasoning, which the harness does not score), so TTFT and
  prefill are unmeasurable there. This is a known sweep-metric gap to address, not a
  model failure.
- Results are specific to these two server builds and quantizations on 48 GB. A
  different MoE quant, a larger memory budget, or a different serving stack could
  shift the balance.
- Power is whole-run average from `powermetrics`; per-task attribution was not
  isolated.

## Reproduce

```bash
export DFLASH_COMMAND='dflash serve --model mlx-community/Qwen3.6-27B-4bit --port 8000'
export TURBOQUANT_COMMAND='turboquant-serve --model manjunathshiva/Qwen3.6-35B-A3B-tq3-g32 --prompt-concurrency 1 --port 8002'
POWER=1 ./scripts/run-local-sweeps.sh
```
