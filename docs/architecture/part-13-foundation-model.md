# Part 13 — XYRIS Foundation Model Strategy

## 13.1 Staged Evolution

### Stage 0: External Model + Runtime (CURRENT)
- Objective: Use Gemini Live API as the brain, XYRIS runtime handles everything else
- Model size: N/A (external)
- Training: None
- Compute: API calls only
- Risk: Low (model is replaceable)
- Exit criteria: Runtime architecture is stable and complete

### Stage 1: XYRIS Prompting
- Objective: Optimize prompts for structured output, tool use, reasoning
- Model size: N/A (external)
- Training: Prompt engineering only
- Compute: Minimal
- Risk: Low
- Exit criteria: Measurable improvement in tool selection accuracy and planning

### Stage 2: Small Model Experiments
- Objective: Train small (1-3B) models for specific sub-tasks
- Model size: 1-3B parameters
- Training: Instruction tuning on XYRIS-specific data
- Compute: Single GPU (feasible now)
- Risk: Medium
- Exit criteria: Small model outperforms general model on specific XYRIS tasks

### Stage 3: Instruction Tuning
- Objective: Fine-tune a base model on XYRIS instruction data
- Model size: 3-7B parameters
- Training: LoRA/QLoRA on curated dataset
- Compute: Single GPU, 1-2 days (feasible now)
- Risk: Medium
- Exit criteria: Measurable improvement on XYRIS benchmark

### Stage 4: Tool-Use Training
- Objective: Train model to reliably select and invoke tools
- Model size: 3-7B parameters
- Training: Tool-use trajectory data
- Compute: Single GPU, 2-3 days (feasible later)
- Risk: Medium
- Exit criteria: >95% tool selection accuracy on test set

### Stage 5: Planning Training
- Objective: Train model to generate task graphs and decompose goals
- Model size: 7-13B parameters
- Training: Planning trajectory data
- Compute: Multi-GPU, 3-5 days (feasible later)
- Risk: High
- Exit criteria: >80% plan completion rate without replanning

### Stage 6: Agent Trajectory Training
- Objective: Train model on complete agent trajectories
- Model size: 7-13B parameters
- Training: Full trajectory data with outcomes
- Compute: Multi-GPU, 1 week (research scale)
- Risk: High
- Exit criteria: >70% autonomous task completion rate

### Stage 7: Multimodal Training
- Objective: Add vision, audio, and screen understanding
- Model size: 13-30B parameters
- Training: Multimodal trajectory data
- Compute: Multi-node, 2+ weeks (research scale)
- Risk: Very high
- Exit criteria: Competitive with frontier models on XYRIS-specific tasks

### Stage 8: XYRIS Foundation Model
- Objective: Full native intelligence
- Model size: 30B+ parameters
- Compute: Cluster scale (frontier scale)
- Risk: Very high
- Exit criteria: XYRIS can operate without external model dependency

## 13.2 Feasibility Assessment

| Stage | Compute | Timeline | Cost |
|-------|---------|----------|------|
| 0 | API only | Done | ~$50/mo |
| 1 | Prompt testing | 1-2 weeks | ~$100 |
| 2 | 1x GPU (RTX 4090) | 1 month | ~$2K |
| 3 | 1x GPU (RTX 4090) | 1-2 months | ~$3K |
| 4 | 1-2x GPU | 2-3 months | ~$5K |
| 5 | 2-4x GPU | 3-6 months | ~$15K |
| 6 | 4-8x GPU | 6-12 months | ~$50K |
| 7 | Multi-node | 12-24 months | ~$200K+ |
| 8 | Cluster | 24+ months | ~$1M+ |

## 13.3 Key Principle

Stages 0-3 are feasible for a solo developer with modest compute.
Stages 4-5 are feasible with investment.
Stages 6-8 require research-scale resources.

The architecture must work at every stage without requiring the next.
