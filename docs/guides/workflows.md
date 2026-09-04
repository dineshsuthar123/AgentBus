# Practical workflows

Run these examples from the exact root of a disposable or clean Git repository.
Replace paths and prompts with repository-specific values. Start with the
deterministic provider when learning the flow.

## Fix a failing test

```console
syndra run --workspace . --workflow multi --durable "Fix the failing calculator test without changing unrelated behavior"
```

## Add a REST endpoint

```console
syndra context-plan "Add a health endpoint with tests" --role planner --workspace .
syndra run --workspace . --workflow multi --durable "Add a health endpoint with focused tests"
```

## Debug a Spring Boot repository

```console
syndra index build --workspace . --json
syndra search RestController --workspace . --evidence
syndra impact src/main/java/com/example/ApiController.java --workspace .
```

## Refactor dependent symbols

```console
syndra dependents calculate_total --workspace .
syndra tests-for calculate_total --workspace .
syndra context-plan "Rename calculate_total safely" --role coder --workspace .
```

## Inspect change impact

```console
syndra impact src/payments/service.py src/payments/models.py --workspace .
```

## Replay a failed run

```console
syndra show-run <run-id>
syndra replay <run-id> --mode offline --from pre-verifier --json
```

## Approve a risky operation

```console
syndra show-run <run-id>
syndra approve <run-id>:<task-id> --reason "Reviewed exact path and operation"
syndra resume <run-id>
```

## Connect a local MCP server

Configure an exact capability map, then validate without a public server:

```console
syndra config validate --workspace . --json
syndra doctor --workspace . --verbose --json
```

## Use Ollama locally

Install Ollama and obtain the model yourself, then:

```powershell
$env:SYNDRA_PROVIDER = "ollama"
$env:SYNDRA_MODEL = "qwen2.5-coder:7b"
syndra providers check ollama
syndra run --workspace . --workflow multi --durable "Add focused tests"
```

Syndra does not download the model and provider traffic follows the configured
Ollama URL.
