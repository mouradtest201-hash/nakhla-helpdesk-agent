from app.workflow import workflow


def _edges():
    return {(e.source, e.target) for e in workflow.graph.get_graph().edges}


def test_blueprint_nodes_exist():
    nodes = set(workflow.graph.get_graph().nodes)
    assert {"guard", "triage", "precedence", "retriever", "resolver", "escalation_writer", "verifier"} <= nodes


def test_happy_path_and_escalation_edges():
    edges = _edges()
    assert ("retriever", "resolver") in edges
    assert ("resolver", "escalation_writer") in edges
    assert ("escalation_writer", "verifier") in edges
    assert ("verifier", "__end__") in edges


def test_every_early_exit_goes_through_verifier():
    edges = _edges()
    for node in ("guard", "triage", "precedence", "resolver"):
        assert (node, "verifier") in edges
