from function.func import flask_app
from function.workload import run_dynamic_html


def test_dynamic_html_workload_generates_requested_items():
    result = run_dynamic_html(
        username="test-user",
        random_len=10,
        seed=42,
    )

    assert result["benchmark"] == "dynamic-html"
    assert result["result"]["generated_count"] == 10
    assert result["result"]["html"].count("<li>") == 10
    assert "Welcome test-user!" in result["result"]["html"]


def test_dynamic_html_http_endpoint():
    response = flask_app.test_client().post(
        "/",
        json={"username": "test-user", "random_len": 10, "seed": 42},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
