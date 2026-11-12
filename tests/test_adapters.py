"""The adapters: the runtime's is found beside the GitHub adapter."""

from workflow_core.adapters import runtime_adapter


def test_the_runtime_adapter_is_found_beside_the_github_adapter():
    import workflow_core.adapters.github as github

    adapter = runtime_adapter()

    assert adapter.__name__ != github.__name__
    assert hasattr(adapter, "make_board_reader")
    assert hasattr(adapter, "check_contract")
