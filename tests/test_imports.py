"""Smoke tests verifying clean imports for all monorepo workspace members."""


def test_import_domain() -> None:
    import accounting_domain

    assert accounting_domain.__version__ == "0.1.0"


def test_import_contracts() -> None:
    import accounting_contracts

    assert accounting_contracts.__version__ == "0.1.0"


def test_import_persistence() -> None:
    import accounting_persistence

    assert accounting_persistence.__version__ == "0.1.0"


def test_import_reporting() -> None:
    import accounting_reporting

    assert accounting_reporting.__version__ == "0.1.0"


def test_import_local_agent() -> None:
    import accounting_local_agent

    assert accounting_local_agent.__version__ == "0.1.0"


def test_import_server_api() -> None:
    import accounting_server_api

    assert accounting_server_api.__version__ == "0.1.0"


def test_import_worker() -> None:
    import accounting_worker

    assert accounting_worker.__version__ == "0.1.0"
