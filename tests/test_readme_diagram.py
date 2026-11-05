"""The README's flowchart of the graph is checked against the compiled graph, so the picture a
reader trusts cannot drift from the code that runs."""

import re
import tempfile
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.fakes import scripted_model_for
from agentops.graph import build_graph
from agentops.sources.scenario import load_scenario
from agentops.sources.simulator import SimulatorSource

ROOT = Path(__file__).resolve().parents[1]
# The README names LangGraph's start and end nodes `START((start))` and `END((end))`.
README_NAMES = {"START": "__start__", "END": "__end__"}
NODE = re.compile(r"^(\w+)")


def readme_edges() -> set[tuple[str, str]]:
    """Every edge drawn in the README's mermaid flowchart, with `a & b` fanned out."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    block = text.split("```mermaid\n", 1)[1].split("```", 1)[0]
    edges: set[tuple[str, str]] = set()
    for line in block.splitlines()[1:]:
        if "-->" not in line:
            continue
        left, right = line.split("-->", 1)
        right = re.sub(r"^\|[^|]*\|", "", right.strip())
        for source in left.split("&"):
            for target in right.split("&"):
                edges.add((name_of(source), name_of(target)))
    return edges


def name_of(text: str) -> str:
    """The graph node a mermaid node reference stands for."""
    match = NODE.match(text.strip())
    assert match, text
    return README_NAMES.get(match.group(1), match.group(1))


def compiled_edges() -> tuple[set[str], set[tuple[str, str]]]:
    """The compiled graph's nodes and edges, as LangGraph draws them."""
    scenario = load_scenario(ROOT / "scenarios" / "clean_bad_deploy.json")
    with tempfile.TemporaryDirectory() as audit_dir:
        graph = build_graph(
            SimulatorSource(scenario),
            scripted_model_for(scenario),
            default_registry(),
            AuditLog(Path(audit_dir)),
            InMemorySaver(),
        ).get_graph()
    return set(graph.nodes), {(edge.source, edge.target) for edge in graph.edges}


def test_every_edge_in_the_readme_is_an_edge_of_the_compiled_graph() -> None:
    _, edges = compiled_edges()
    drawn = readme_edges()
    assert len(drawn) > 20
    assert drawn - edges == set()


def test_every_node_of_the_compiled_graph_is_in_the_readme() -> None:
    nodes, _ = compiled_edges()
    drawn = {node for edge in readme_edges() for node in edge}
    assert nodes - drawn == set()
