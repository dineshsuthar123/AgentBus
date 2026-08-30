# The Solution

AgentBus is a safety-oriented local execution runtime for autonomous software
engineering. AgentBus Studio is its visual control plane.

The runtime separates planning, implementation, verification, and final review.
Managed tools derive capabilities independently from model output, deterministic
policy evaluates every invocation, and risky operations stop at exact
revision-bound approvals. SQLite-backed task and attempt state survives process
interruption, while retry evidence carries bounded failure observations into the
next attempt.

Studio makes that system visible as an execution graph:

1. launch a task against a validated Git root;
2. inspect the persisted plan and task done criteria;
3. watch repository and managed-tool activity;
4. approve or reject the exact paused invocation;
5. inspect verifier and reviewer outcomes;
6. review changed files and a bounded Git diff;
7. verify the sealed trace and run an offline replay.

Studio does not infer success or invent activity. Every state comes from the
authenticated AgentBus control protocol.
