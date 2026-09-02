# Meaningful AI Features

AgentBus uses model providers for bounded software-engineering roles rather than
placing an ungoverned chatbot in front of a shell.

- The planner produces a validated task graph with dependencies, expected
  outputs, capabilities, and done criteria.
- The coder chooses structured actions, but model output never grants tool
  permission.
- The verifier runs repository checks and can reject a candidate.
- Task review evaluates only the current task evidence; mandatory final review
  evaluates the completed run before optional publication.
- RetryEvidence feeds bounded verifier or reviewer diagnostics into a later
  immutable attempt.
- Repository intelligence supplies local symbols, dependencies, impact, and
  test evidence without uploading source.
- Deterministic, Ollama, and explicitly consented Azure routes share the same
  safety and durability boundary.

The payment demo uses the deterministic provider so reviewers can inspect the
complete workflow with no model credentials or paid API calls.
