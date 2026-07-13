from agentbus.repo.context_pack import ContextPackBuilder
from agentbus.repo.scanner import RepoScanner
from agentbus.repo.test_detection import TestCommandDetector


def write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_repo_scanner_ignores_generated_dirs(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "app.py", "print('hello')")
    write(workspace / "node_modules" / "pkg" / "index.js", "")
    write(workspace / ".git" / "config", "")
    write(workspace / ".pytest_tmp" / "x.txt", "")

    result = RepoScanner(str(workspace)).scan()

    assert "app.py" in result["files"]
    assert "node_modules/pkg/index.js" not in result["files"]
    assert ".git/config" not in result["files"]
    assert ".pytest_tmp/x.txt" not in result["files"]
    assert "node_modules" in result["ignored_dirs"]


def test_repo_scanner_detects_python_project(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "requirements.txt", "pytest")
    write(workspace / "main.py", "print('hello')")
    write(workspace / "tests" / "test_main.py", "def test_ok(): assert True")

    result = RepoScanner(str(workspace)).scan()

    assert "Python" in result["detected_languages"]
    assert "Python" in result["detected_frameworks"]
    assert "pip" in result["package_managers"]
    assert "main.py" in result["entrypoints"]
    assert "tests/test_main.py" in result["test_files"]


def test_repo_scanner_detects_node_project(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "package.json", '{"scripts":{"test":"node test.js"}}')
    write(workspace / "src" / "index.ts", "")

    result = RepoScanner(str(workspace)).scan()

    assert "TypeScript" in result["detected_languages"]
    assert "Node.js" in result["detected_frameworks"]
    assert "npm" in result["package_managers"]


def test_repo_scanner_detects_maven_project(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "pom.xml", "<project></project>")
    write(workspace / "src" / "main" / "java" / "App.java", "class App {}")

    result = RepoScanner(str(workspace)).scan()

    assert "Java" in result["detected_languages"]
    assert "Maven Java" in result["detected_frameworks"]
    assert "maven" in result["package_managers"]


def test_test_command_detector_picks_pytest(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "pytest.ini", "[pytest]")

    result = TestCommandDetector(str(workspace)).detect()

    assert result["command"] == ["python", "-m", "pytest"]
    assert result["confidence"] == "high"


def test_test_command_detector_picks_root_level_pytest_file(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "test_calculator.py", "def test_add(): assert True")

    result = TestCommandDetector(str(workspace)).detect()

    assert result["command"] == ["python", "-m", "pytest"]


def test_test_command_detector_picks_npm_test(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "package.json", '{"scripts":{"test":"vitest"}}')

    result = TestCommandDetector(str(workspace)).detect()

    assert result["command"] == ["npm", "test"]


def test_test_command_detector_picks_mvn_test(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "pom.xml", "<project></project>")

    result = TestCommandDetector(str(workspace)).detect()

    assert result["command"] == ["mvn", "test"]


def test_context_pack_includes_stack_test_command_and_key_files(tmp_path):
    workspace = tmp_path / "workspace"
    write(workspace / "requirements.txt", "pytest")
    write(workspace / "calculator.py", "def add(a, b): return a + b")
    write(workspace / "tests" / "test_calculator.py", "def test_add(): assert True")
    scan = RepoScanner(str(workspace)).scan()
    detection = TestCommandDetector(str(workspace)).detect()

    context = ContextPackBuilder().build(
        scan,
        detection,
        user_task="Update calculator tests",
    )

    assert "Repo Context Pack" in context
    assert "Python" in context
    assert "requirements.txt" in context
    assert "['python', '-m', 'pytest']" in context
    assert "calculator.py" in context
