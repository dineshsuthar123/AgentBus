from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import DependencyKind, SourceLanguage
from agentbus.intelligence.parsers import ParseRequest, TypeScriptStaticParser


def _parse(content: str):
    repository = repository_identity("example/typescript-references")
    request = ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, "src/service.ts"),
        relative_path="src/service.ts",
        language=SourceLanguage.TYPESCRIPT,
        content=content,
    )
    return TypeScriptStaticParser().parse(request)


def test_indexes_esm_commonjs_and_reexport_specifiers() -> None:
    result = _parse(
        """
import Client, { User as Account } from "@/client";
import "./setup";
const utility = require("../utility");
const lazy = import("./lazy");
export { Service as DefaultService } from "./service";
""".lstrip()
    )
    imports = {
        reference.target: reference
        for reference in result.references
        if reference.kind == DependencyKind.IMPORTS
    }
    exports = {
        reference.target: reference
        for reference in result.references
        if reference.kind == DependencyKind.EXPORTS
    }

    assert imports["@/client"].attributes["imported_names"] == (
        "Client",
        "User",
        "Account",
    )
    assert "./setup" in imports
    assert imports["../utility"].attributes["commonjs"] is True
    assert imports["./lazy"].attributes["dynamic"] is True
    assert exports["./service"].attributes["reexport"] is True
    assert "Service" in exports


def test_indexes_type_relationships_and_scoped_calls() -> None:
    result = _parse(
        """
interface Runner extends BaseRunner<Context> {}

class Service extends BaseService implements Runner, Disposable {
    run() {
        this.client.send();
        new Worker();
    }
}
""".lstrip()
    )
    relationships = {
        (reference.kind, reference.target): reference
        for reference in result.references
        if reference.kind
        in {DependencyKind.INHERITS, DependencyKind.IMPLEMENTS}
    }
    calls = {
        (reference.kind, reference.target): reference
        for reference in result.references
        if reference.kind in {DependencyKind.CALLS, DependencyKind.INSTANTIATES}
    }

    assert (DependencyKind.INHERITS, "BaseRunner") in relationships
    assert (DependencyKind.INHERITS, "Context") not in relationships
    assert (DependencyKind.INHERITS, "BaseService") in relationships
    assert (DependencyKind.IMPLEMENTS, "Runner") in relationships
    assert (DependencyKind.IMPLEMENTS, "Disposable") in relationships
    assert (DependencyKind.CALLS, "this.client.send") in calls
    worker = calls[(DependencyKind.INSTANTIATES, "Worker")]
    assert worker.source_qualified_name == "src.service.Service.run"
    assert worker.confidence < 1.0
