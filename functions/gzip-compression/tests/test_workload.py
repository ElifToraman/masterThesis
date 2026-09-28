from function.func import flask_app
from function.workload import run_gzip_compression


def test_gzip_compression_workload():
    result = run_gzip_compression(file_size_mb=1)

    assert result["benchmark"] == "gzip-compression"
    assert result["result"]["input_bytes"] == 1024 * 1024
    assert result["result"]["output_bytes"] > 0
    assert len(result["result"]["output_sha256"]) == 64


def test_gzip_compression_http_endpoint():
    response = flask_app.test_client().post(
        "/",
        json={"file_size_mb": 1},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
