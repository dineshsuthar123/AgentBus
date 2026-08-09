# Architecture and decisions

AgentBus keeps architecture decisions as ADRs and detailed subsystem guides.
They remain authoritative for design invariants; the user guides provide the
short operational path.

## Runtime

- [Durable task execution](../adr/0001-durable-task-execution.md)
- [Provider routing](../adr/0002-model-provider-routing.md)
- [Parallel worktree execution](../adr/0004-worktree-parallel-execution.md)
- [Local control plane and VS Code](../adr/0007-local-control-plane-and-vscode.md)
- [Cooperative cancellation](../adr/0008-cooperative-cancellation-and-real-local-execution.md)
- [Capability tool runtime](../adr/0009-capability-tool-runtime.md)

## Evidence and intelligence

- [Evaluation and regression](../adr/0005-evaluation-and-regression-harness.md)
- [Replay and provenance](../adr/0010-deterministic-replay-and-provenance.md)
- [Repository intelligence](../adr/0011-repository-intelligence-engine.md)

## Release history

- [v0.1 release surface](../adr/0006-v0.1-release-and-real-repo-validation.md)
- [Control protocol](../protocol-v1.md)
- [Pre-v1 compatibility policy](../reference/compatibility.md)
