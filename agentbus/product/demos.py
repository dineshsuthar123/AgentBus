from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEMO_LANGUAGES = ("python", "java", "typescript", "go", "payment")
_MARKER = ".agentbus-demo.json"


@dataclass(frozen=True)
class DemoDefinition:
    language: str
    title: str
    task: str
    files: dict[str, str]
    test_command: tuple[str, ...]
    required_executable: str


@dataclass(frozen=True)
class DemoResult:
    language: str
    workspace: Path
    created_files: tuple[str, ...]
    test_command: tuple[str, ...]
    test_executed: bool = False
    test_exit_code: int | None = None
    expected_initial_failure: bool = True
    git_initialized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "workspace": str(self.workspace),
            "created_files": list(self.created_files),
            "test_command": list(self.test_command),
            "test_executed": self.test_executed,
            "test_exit_code": self.test_exit_code,
            "expected_initial_failure": self.expected_initial_failure,
            "git_initialized": self.git_initialized,
            "ready": self.test_exit_code not in {0} if self.test_executed else True,
            "network_used": False,
        }


def demo_definitions() -> dict[str, DemoDefinition]:
    return {
        "python": DemoDefinition(
            language="python",
            title="Python calculator",
            task="Fix add() so the bundled unittest passes without changing the test.",
            files={
                "calculator.py": "def add(left: int, right: int) -> int:\n    return left - right\n",
                "test_calculator.py": (
                    "import unittest\n\nfrom calculator import add\n\n\n"
                    "class CalculatorTest(unittest.TestCase):\n"
                    "    def test_adds_two_numbers(self):\n"
                    "        self.assertEqual(add(2, 3), 5)\n\n\n"
                    "if __name__ == '__main__':\n    unittest.main()\n"
                ),
            },
            test_command=(sys.executable, "-m", "unittest", "-q"),
            required_executable=sys.executable,
        ),
        "java": DemoDefinition(
            language="java",
            title="Spring Boot order total",
            task="Fix OrderService.total so the JUnit test passes.",
            files={
                "pom.xml": (
                    "<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
                    "  <modelVersion>4.0.0</modelVersion>\n"
                    "  <groupId>dev.agentbus.demo</groupId><artifactId>orders</artifactId><version>1</version>\n"
                    "  <properties><maven.compiler.release>17</maven.compiler.release>"
                    "<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>"
                    "<junit.version>5.11.4</junit.version></properties>\n"
                    "  <dependencies><dependency><groupId>org.junit.jupiter</groupId>"
                    "<artifactId>junit-jupiter</artifactId><version>${junit.version}</version>"
                    "<scope>test</scope></dependency></dependencies>\n"
                    "  <build><plugins><plugin><groupId>org.apache.maven.plugins</groupId>"
                    "<artifactId>maven-surefire-plugin</artifactId><version>3.5.2</version>"
                    "</plugin></plugins></build>\n</project>\n"
                ),
                "src/main/java/demo/OrderService.java": (
                    "package demo;\npublic final class OrderService {\n"
                    "  public int total(int subtotal, int shipping) { return subtotal - shipping; }\n}\n"
                ),
                "src/test/java/demo/OrderServiceTest.java": (
                    "package demo;\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n"
                    "import org.junit.jupiter.api.Test;\nclass OrderServiceTest {\n"
                    "  @Test void totalsOrder() { assertEquals(12, new OrderService().total(10, 2)); }\n}\n"
                ),
            },
            test_command=("mvn", "-q", "-o", "test"),
            required_executable="mvn",
        ),
        "payment": DemoDefinition(
            language="payment",
            title="Concurrent payment confirmation",
            task=(
                "Make payment confirmation idempotent under concurrent retries, "
                "preserve the public API, and prove the behavior with repository tests."
            ),
            files={
                ".gitignore": ".agentbus/\ntarget/\n",
                ".mvn/maven.config": "-q\n-o\n",
                "pom.xml": (
                    "<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
                    "  <modelVersion>4.0.0</modelVersion>\n"
                    "  <groupId>dev.agentbus.demo</groupId>\n"
                    "  <artifactId>payment-safety</artifactId>\n"
                    "  <version>1.0.0</version>\n"
                    "  <properties>\n"
                    "    <maven.compiler.release>17</maven.compiler.release>\n"
                    "    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>\n"
                    "    <junit.version>5.11.4</junit.version>\n"
                    "  </properties>\n"
                    "  <dependencies>\n"
                    "    <dependency>\n"
                    "      <groupId>org.junit.jupiter</groupId>\n"
                    "      <artifactId>junit-jupiter</artifactId>\n"
                    "      <version>${junit.version}</version>\n"
                    "      <scope>test</scope>\n"
                    "    </dependency>\n"
                    "  </dependencies>\n"
                    "  <build><plugins><plugin>\n"
                    "    <groupId>org.apache.maven.plugins</groupId>\n"
                    "    <artifactId>maven-surefire-plugin</artifactId>\n"
                    "    <version>3.5.2</version>\n"
                    "  </plugin></plugins></build>\n"
                    "</project>\n"
                ),
                "src/main/java/com/agentbus/demo/PaymentService.java": (
                    "package com.agentbus.demo;\n\n"
                    "import java.util.Objects;\n"
                    "import java.util.Set;\n"
                    "import java.util.concurrent.ConcurrentHashMap;\n\n"
                    "public final class PaymentService {\n"
                    "    private final Set<String> confirmedPaymentIds = "
                    "ConcurrentHashMap.newKeySet();\n\n"
                    "    public int confirm(String paymentId) {\n"
                    "        Objects.requireNonNull(paymentId, \"paymentId\");\n"
                    "        confirmedPaymentIds.add(paymentId);\n"
                    "        return 1;\n"
                    "    }\n"
                    "}\n"
                ),
                "src/test/java/com/agentbus/demo/PaymentServiceTest.java": (
                    "package com.agentbus.demo;\n\n"
                    "import static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
                    "import java.util.ArrayList;\n"
                    "import java.util.concurrent.CountDownLatch;\n"
                    "import java.util.concurrent.Executors;\n"
                    "import java.util.concurrent.Future;\n"
                    "import org.junit.jupiter.api.Test;\n\n"
                    "class PaymentServiceTest {\n"
                    "    @Test\n"
                    "    void confirmsOnlyOnceAcrossSequentialRetries() {\n"
                    "        var service = new PaymentService();\n"
                    "        assertEquals(1, service.confirm(\"pay_demo_001\"));\n"
                    "        assertEquals(0, service.confirm(\"pay_demo_001\"));\n"
                    "    }\n\n"
                    "    @Test\n"
                    "    void confirmsOnlyOnceAcrossConcurrentRetries() throws Exception {\n"
                    "        var service = new PaymentService();\n"
                    "        var start = new CountDownLatch(1);\n"
                    "        var pool = Executors.newFixedThreadPool(8);\n"
                    "        try {\n"
                    "            var attempts = new ArrayList<Future<Integer>>();\n"
                    "            for (int index = 0; index < 32; index++) {\n"
                    "                attempts.add(pool.submit(() -> {\n"
                    "                    start.await();\n"
                    "                    return service.confirm(\"pay_demo_002\");\n"
                    "                }));\n"
                    "            }\n"
                    "            start.countDown();\n"
                    "            int accepted = 0;\n"
                    "            for (var attempt : attempts) {\n"
                    "                accepted += attempt.get();\n"
                    "            }\n"
                    "            assertEquals(1, accepted);\n"
                    "        } finally {\n"
                    "            pool.shutdownNow();\n"
                    "        }\n"
                    "    }\n"
                    "}\n"
                ),
            },
            test_command=("mvn", "-q", "-o", "test"),
            required_executable="mvn",
        ),
        "typescript": DemoDefinition(
            language="typescript",
            title="TypeScript price helper",
            task="Fix addTax so the Node test passes.",
            files={
                "package.json": json.dumps(
                    {
                        "name": "agentbus-typescript-demo",
                        "private": True,
                        "type": "module",
                        "scripts": {"test": "node --test test/*.test.js"},
                    },
                    indent=2,
                ) + "\n",
                "src/price.js": "export function addTax(price, tax) { return price - tax; }\n",
                "test/price.test.js": (
                    "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
                    "import { addTax } from '../src/price.js';\n"
                    "test('adds tax', () => assert.equal(addTax(10, 2), 12));\n"
                ),
            },
            test_command=("node", "--test", "test/price.test.js"),
            required_executable="node",
        ),
        "go": DemoDefinition(
            language="go",
            title="Go counter",
            task="Fix Add so the Go test passes.",
            files={
                "go.mod": "module example.invalid/agentbus-demo\n\ngo 1.22\n",
                "counter.go": "package counter\n\nfunc Add(left, right int) int { return left - right }\n",
                "counter_test.go": (
                    "package counter\n\nimport \"testing\"\n\n"
                    "func TestAdd(t *testing.T) { if Add(2, 3) != 5 { t.Fatal(\"want 5\") } }\n"
                ),
            },
            test_command=("go", "test", "./..."),
            required_executable="go",
        ),
    }


def create_demo(
    language: str,
    destination: str | Path,
    *,
    force: bool = False,
    initialize_git: bool = False,
) -> DemoResult:
    definition = _definition(language)
    root = Path(destination).expanduser().resolve()
    marker = root / _MARKER
    if root.exists() and not root.is_dir():
        raise ValueError("Demo destination must be a directory")
    fresh_destination = not root.exists() or not any(root.iterdir())
    if initialize_git and not fresh_destination:
        raise ValueError("Git initialization requires a new empty demo destination")
    if root.exists() and any(root.iterdir()):
        if not force or not marker.is_file():
            raise ValueError(
                "Demo destination is not empty or is not owned by AgentBus; choose another path."
            )
        try:
            ownership = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("AgentBus demo ownership marker is invalid") from exc
        if ownership.get("language") != definition.language:
            raise ValueError("AgentBus demo language does not match the existing marker")
    root.mkdir(parents=True, exist_ok=True)
    files = {
        **definition.files,
        "AGENTBUS_TASK.md": f"# {definition.title}\n\n{definition.task}\n",
        _MARKER: json.dumps(
            {
                "schema": 1,
                "language": definition.language,
                "managed_files": sorted((*definition.files, "AGENTBUS_TASK.md", _MARKER)),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
    }
    for relative, content in files.items():
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Demo template attempted to escape its destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    if initialize_git:
        _initialize_git_repository(root, tuple(sorted(files)))
    return DemoResult(
        language=definition.language,
        workspace=root,
        created_files=tuple(sorted(files)),
        test_command=definition.test_command,
        git_initialized=initialize_git,
    )


def run_demo(
    language: str,
    *,
    workspace: str | Path | None = None,
    timeout_seconds: float = 60.0,
) -> DemoResult:
    if timeout_seconds <= 0 or timeout_seconds > 600:
        raise ValueError("Demo timeout must be between 0 and 600 seconds")
    definition = _definition(language)
    if workspace is None:
        temporary = Path(tempfile.mkdtemp(prefix=f"agentbus-demo-{language}-"))
        created = create_demo(language, temporary)
    else:
        root = Path(workspace).expanduser().resolve()
        if not (root / _MARKER).is_file():
            created = create_demo(language, root)
        else:
            marker = json.loads((root / _MARKER).read_text(encoding="utf-8"))
            if marker.get("language") != language:
                raise ValueError("Demo workspace language does not match")
            created = DemoResult(
                language=language,
                workspace=root,
                created_files=tuple(marker.get("managed_files", ())),
                test_command=definition.test_command,
                git_initialized=(root / ".git").is_dir(),
            )
    executable = definition.required_executable
    available = Path(executable).is_file() if Path(executable).is_absolute() else shutil.which(executable)
    if not available:
        return created
    result = subprocess.run(
        list(definition.test_command),
        cwd=created.workspace,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    return DemoResult(
        language=created.language,
        workspace=created.workspace,
        created_files=created.created_files,
        test_command=created.test_command,
        test_executed=True,
        test_exit_code=result.returncode,
        git_initialized=created.git_initialized,
    )


def _definition(language: str) -> DemoDefinition:
    try:
        return demo_definitions()[language.lower()]
    except KeyError as exc:
        raise ValueError(
            "Demo language must be one of: " + ", ".join(DEMO_LANGUAGES)
        ) from exc


def _initialize_git_repository(root: Path, managed_files: tuple[str, ...]) -> None:
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required for --git demo initialization")
    commands = [
        [git, "init"],
        [git, "config", "--local", "user.name", "AgentBus Demo"],
        [git, "config", "--local", "user.email", "agentbus-demo@example.invalid"],
        [git, "add", "--", *managed_files],
        [
            git,
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--no-verify",
            "-m",
            "chore: seed AgentBus demo",
        ],
    ]
    for command in commands:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ValueError(f"Git demo initialization failed: {detail[:500]}")
