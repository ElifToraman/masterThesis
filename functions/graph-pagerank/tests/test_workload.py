from function.func import flask_app
from function.workload import run_graph_pagerank


def test_graph_pagerank_workload():
    result = run_graph_pagerank(size=10, seed=42)

    assert result["benchmark"] == "graph-pagerank"
    assert result["result"]["vertex_count"] == 10
    assert 0 < result["result"]["pagerank_first_vertex"] <= 1


def test_graph_pagerank_http_endpoint():
    response = flask_app.test_client().post(
        "/",
        json={"size": 10, "seed": 42},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
