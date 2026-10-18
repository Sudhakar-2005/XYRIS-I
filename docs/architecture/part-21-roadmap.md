# Part 21 — Implementation Roadmap

## 21.1 Phase 1: Runtime Foundation (Months 1-3)

### Week 1-2: Event Bus + Tool Registry
- Implement async event bus
- Refactor tool registry to be central
- Add schema validation
- Add event types and priorities

### Week 3-4: Planner
- Implement Task and TaskGraph data structures
- Implement goal decomposition
- Implement dependency tracking
- Implement dynamic replanning

### Week 5-6: Verifier
- Implement verification engine
- Add verification strategies per tool type
- Implement false-success prevention
- Add post-action observation

### Week 7-8: Memory 2.0
- Implement working memory (ephemeral)
- Implement episodic memory with trajectories
- Implement procedural memory
- Add relevance scoring
- Add contradiction detection

### Week 9-10: Reasoning Artifacts
- Implement structured reasoning output
- Add confidence assessment
- Add self-checking
- Add reasoning trace storage

### Week 11-12: Integration
- Wire all components together
- Run full test suite
- Fix bugs
- Performance optimization

## 21.2 Phase 2: Data Collection (Months 3-6)

### Month 3-4: Trajectory Recording
- Record all user interactions
- Record all tool executions
- Record all reasoning chains
- Record all verification results

### Month 5-6: Dataset Building
- Implement PII detection
- Implement quality scoring
- Build initial training dataset
- Start small model experiments

## 21.3 Phase 3: Training (Months 6-12)

### Month 6-9: Base Training
- Fine-tune base model on XYRIS data
- LoRA/QLoRA for efficiency
- Single GPU training

### Month 10-12: Evaluation
- Run XYRIS Intelligence Benchmark
- A/B test against Gemini
- Iterate on data and hyperparameters

## 21.4 Phase 4: Gradual Replacement (Months 12-18)

### Month 12-14: Tool Selection
- Replace Gemini tool selection with fine-tuned model
- Measure accuracy improvement
- Keep Gemini as fallback

### Month 15-16: Planning
- Replace Gemini planning with fine-tuned model
- Measure plan quality improvement
- Keep Gemini as fallback

### Month 17-18: Full Reasoning
- Replace Gemini reasoning with fine-tuned model
- Measure reasoning quality improvement
- Keep Gemini as fallback

## 21.5 Phase 5: Full XYRIS (Months 18-24)

### Month 18-20: Local Inference
- Optimize model for local inference
- Implement model caching
- Reduce latency

### Month 21-24: Polish
- Fine-tune on edge cases
- Improve error recovery
- Optimize performance
- Full production readiness

## 21.6 Success Criteria

| Phase | Success Metric |
|-------|---------------|
| Phase 1 | All runtime components working, 100% backward compatible |
| Phase 2 | 100K+ training examples collected |
| Phase 3 | Fine-tuned model outperforms Gemini on XIB-Lite |
| Phase 4 | Fine-tuned model handles 50%+ of tasks without Gemini |
| Phase 5 | Fine-tuned model handles 90%+ of tasks locally |
