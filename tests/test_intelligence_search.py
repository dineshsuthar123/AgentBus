from __future__ import annotations

import pytest

from agentbus.intelligence import (
    LexicalSearchLimits,
    Module,
    Project,
    ProjectKind,
    QueryLimitError,
    RepositoryLexicalIndex,
    SearchQuery,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    file_id,
    module_id,
    project_id,
    repository_identity,
    stable_id,
)


def _records():
    repository = repository_identity("fixtures/lexical-search")
    billing_id = project_id(
        repository.repository_id,
        "services/billing",
        ProjectKind.PYTHON,
        name="billing-service",
    )
    web_id = project_id(
        repository.repository_id,
        "apps/web",
        ProjectKind.NODE,
        name="billing-web",
    )
    projects = (
        Project(
            project_id=billing_id,
            repository_id=repository.repository_id,
            name="billing-service",
            kind=ProjectKind.PYTHON,
            root="services/billing",
            source_roots=("services/billing/src",),
            test_roots=("services/billing/tests",),
        ),
        Project(
            project_id=web_id,
            repository_id=repository.repository_id,
            name="billing-web",
            kind=ProjectKind.NODE,
            root="apps/web",
            source_roots=("apps/web/src",),
        ),
    )
    source_specs = (
        (
            "services/billing/src/invoice.py",
            billing_id,
            SourceLanguage.PYTHON,
            False,
            False,
            "1",
        ),
        (
            "services/billing/tests/test_invoice.py",
            billing_id,
            SourceLanguage.PYTHON,
            True,
            False,
            "2",
        ),
        (
            "services/billing/src/settings.py",
            billing_id,
            SourceLanguage.PYTHON,
            False,
            False,
            "3",
        ),
        (
            "apps/web/src/invoice.ts",
            web_id,
            SourceLanguage.TYPESCRIPT,
            False,
            False,
            "4",
        ),
        (
            "services/billing/src/secret.py",
            billing_id,
            SourceLanguage.PYTHON,
            False,
            True,
            "5",
        ),
    )
    files = tuple(
        SourceFile(
            file_id=file_id(repository.repository_id, path),
            repository_id=repository.repository_id,
            project_id=owner,
            relative_path=path,
            language=language,
            content_hash=hash_digit * 64,
            size_bytes=100,
            parser_name="fixture",
            parser_version="1.0.0",
            test=test,
            protected=protected,
        )
        for path, owner, language, test, protected, hash_digit in source_specs
    )
    files_by_path = {item.relative_path: item for item in files}
    invoice_module = Module(
        module_id=module_id(
            billing_id,
            "billing.invoice",
            "services/billing/src/invoice.py",
        ),
        project_id=billing_id,
        name="invoice",
        qualified_name="billing.invoice",
        relative_path="services/billing/src/invoice.py",
        language=SourceLanguage.PYTHON,
        public=True,
    )

    def symbol(
        key: str,
        path: str,
        name: str,
        qualified_name: str,
        kind: SymbolKind,
        *,
        documentation: str | None = None,
        signature: str | None = None,
        endpoint: str | None = None,
        exported: bool = False,
        test: bool = False,
        attributes: dict | None = None,
    ) -> Symbol:
        source = files_by_path[path]
        return Symbol(
            symbol_id=stable_id("symbol", "lexical", key),
            file_id=source.file_id,
            project_id=source.project_id,
            module_id=(
                invoice_module.module_id
                if path == "services/billing/src/invoice.py"
                else None
            ),
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            language=source.language,
            location=SymbolLocation(
                relative_path=path,
                start_line=1,
                start_column=0,
                end_line=1,
                end_column=1,
            ),
            documentation=documentation,
            signature=signature,
            endpoint=endpoint,
            exported=exported,
            test=test,
            attributes=attributes or {},
        )

    symbols = (
        symbol(
            "calculate",
            "services/billing/src/invoice.py",
            "calculate_invoice",
            "billing.invoice.calculate_invoice",
            SymbolKind.FUNCTION,
            documentation="Calculate the invoice total with tax.",
            signature="calculate_invoice(items: list[Item]) -> Decimal",
            exported=True,
        ),
        symbol(
            "endpoint",
            "services/billing/src/invoice.py",
            "list_invoices",
            "billing.invoice.list_invoices",
            SymbolKind.ENDPOINT,
            endpoint="/v1/invoices",
            exported=True,
        ),
        symbol(
            "test",
            "services/billing/tests/test_invoice.py",
            "test_calculate_invoice",
            "tests.test_invoice.test_calculate_invoice",
            SymbolKind.TEST,
            test=True,
        ),
        symbol(
            "setting",
            "services/billing/src/settings.py",
            "payment_timeout",
            "billing.settings.payment_timeout",
            SymbolKind.CONFIGURATION_UNIT,
            attributes={"env_key": "PAYMENT_TIMEOUT"},
        ),
        symbol(
            "web",
            "apps/web/src/invoice.ts",
            "calculateInvoice",
            "web.invoice.calculateInvoice",
            SymbolKind.FUNCTION,
            exported=True,
        ),
        symbol(
            "secret",
            "services/billing/src/secret.py",
            "REAL_API_KEY",
            "billing.secret.REAL_API_KEY",
            SymbolKind.CONSTANT,
        ),
    )
    return projects, files, (invoice_module,), symbols


def _index(
    *,
    limits: LexicalSearchLimits | None = None,
) -> RepositoryLexicalIndex:
    projects, files, modules, symbols = _records()
    return RepositoryLexicalIndex(
        projects,
        files,
        modules,
        symbols,
        snapshot_id=stable_id("snapshot", "lexical"),
        limits=limits,
    )


def test_ranks_exact_identifiers_deterministically() -> None:
    index = _index()
    query = SearchQuery(text="calculate_invoice")

    first = index.search(query)
    second = index.search(query)

    assert first == second
    assert first[0].symbol is not None
    assert first[0].symbol.name == "calculate_invoice"
    assert first[0].source_hash == "1" * 64
    assert first[0].score_components["exact_identifier"] == 120.0
    assert first[0].rank == 1
    assert first[0].explanation.startswith(
        "Deterministic lexical ranking:"
    )


def test_supports_phrase_endpoint_project_and_configuration_queries() -> None:
    index = _index()

    phrase = index.search(SearchQuery(text='"invoice total"'))
    endpoint = index.search(SearchQuery(text="/v1/invoices"))
    project = index.search(SearchQuery(text="billing-service"))
    configuration = index.search(SearchQuery(text="payment_timeout"))

    assert phrase[0].symbol is not None
    assert phrase[0].symbol.name == "calculate_invoice"
    assert phrase[0].score_components["phrase"] == 30.0
    assert endpoint[0].symbol is not None
    assert endpoint[0].symbol.name == "list_invoices"
    assert endpoint[0].score_components["exact_endpoint"] == 100.0
    assert project
    assert all(item.project_id == _records()[0][0].project_id for item in project)
    assert configuration[0].symbol is not None
    assert configuration[0].symbol.kind == SymbolKind.CONFIGURATION_UNIT


def test_applies_structured_filters_and_pagination() -> None:
    projects, _, _, _ = _records()
    index = _index()
    tests = index.search(
        SearchQuery(
            text="invoice",
            project_ids=(projects[0].project_id,),
            languages=(SourceLanguage.PYTHON,),
            symbol_kinds=(SymbolKind.TEST,),
            path_prefixes=("services/billing/tests",),
            test_only=True,
        )
    )
    page = index.search(
        SearchQuery(text="invoice", limit=1, offset=1)
    )

    assert len(tests) == 1
    assert tests[0].symbol is not None
    assert tests[0].symbol.name == "test_calculate_invoice"
    assert len(page) == 1
    assert page[0].rank == 2


def test_excludes_protected_files_and_bounds_index_work() -> None:
    index = _index()

    assert index.search(SearchQuery(text="REAL_API_KEY")) == ()
    with pytest.raises(QueryLimitError, match="document count"):
        _index(
            limits=LexicalSearchLimits(maximum_documents=2)
        )
