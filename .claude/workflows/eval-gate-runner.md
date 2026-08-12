# eval-gate-runner

**Workflow**: 运行 Eval 门禁套件
**Trigger**: `/workflow eval-gate-runner`

## Steps

1. **Run offline gates** — 执行三个离线门禁
   - `python eval/agent_jd2_gate.py run`
   - `python eval/multimodal_embedding_gate.py run`
   - `python eval/harness_capability_gate.py run`
2. **Run eval pipeline** — `python eval/run.py run-eval --sample-limit 10`
3. **Gate check** — `python eval/run.py gate --threshold 5`
4. **Report** — 汇总各 gate pass/fail 结果

## Output

```
Offline gates:
  agent_jd2:              ✅ passed (score: 0.92)
  multimodal_embedding:   ✅ passed (score: 0.88)
  harness_capability:     ✅ passed (score: 0.95)
Eval pipeline:           ✅ passed (gate delta: 2.1% < threshold 5%)
```
