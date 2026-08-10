# Practical workflows

Run these examples from the exact root of a disposable or clean Git repository.
Replace paths and prompts with repository-specific values. Start with the
deterministic provider when learning the flow.

## Fix a failing test

```console
agentbus run --workspace . --workflow multi --durable "Fix the failing calculator test without changing unrelated behavior"
```

## Add a REST endpoint

```console
agentbus context-plan "Add a health endpoint with tests" --role planner --workspace .
agentbus run --workspace . --workflow multi --durable "Add a health endpoint with focused tests"
```

## Debug a Spring Boot repository

```console
agentbus index build --workspace . --json
agentbus search RestController --workspace . --evidence
agentbus impact src/main/java/com/example/ApiController.java --workspace .
```

## Refactor dependent symbols

```console
agentbus dependents calculate_total --workspace .
agentbus tests-for calculate_total --workspace .
agentbus context-plan "Rename calculate_total safely" --role coder --workspace .
```

## Inspect change impact

```console
agentbus impact src/payments/service.py src/payments/models.py --workspace .
```

## Replay a failed run

```console
agentbus show-run <run-id>
agentbus replay <run-id> --mode offline --from pre-verifier --json
```

## Approve a risky operation

```console
agentbus show-run <run-id>
agentbus approve <run-id>:<task-id> --reason "Reviewed exact path and operation"
agentbus resume <run-id>
```

## Connect a local MCP server

Configure an exact capability map, then validate without a public server:

```console
agentbus config validate --workspace . --json
agentbus doctor --workspace . --verbose --json
```

## Use Ollama locally

Install Ollama and obtain the model yourself, then:

```powershell
$env:AGENTBUS_PROVIDER = "ollama"
$env:AGENTBUS_MODEL = "qwen2.5-coder:7b"
agentbus providers check ollama
agentbus run --workspace . --workflow multi --durable "Add focused tests"
```

AgentBus does not download the model and provider traffic follows the configured
Ollama URL.
