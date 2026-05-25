import argparse
import json
from pathlib import Path
from typing import Any

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local Dify workflow endpoints.")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("dify_local_endpoints.json")),
        help="Path to local endpoint config JSON.",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).with_name("dify_local_test_results.json")),
        help="Path to write JSON results.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    result_path = Path(args.out)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    results: list[dict[str, Any]] = []
    timeout = httpx.Timeout(30.0)
    with httpx.Client(timeout=timeout) as client:
        for endpoint in config["endpoints"]:
            workflow_result = call_workflow_api(client, config, endpoint)
            webhook_result = call_webhook(client, endpoint)
            results.append(
                {
                    "workflow_code": endpoint["workflow_code"],
                    "name": endpoint["name"],
                    "workflow_api": workflow_result,
                    "webhook": webhook_result,
                }
            )

    payload = {"results": results}
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for item in results:
        workflow_api = item["workflow_api"]
        webhook = item["webhook"]
        print(f"[{item['workflow_code']}] {item['name']}")
        print(
            "  workflow_api:",
            workflow_api["http_status"],
            workflow_api.get("dify_status"),
            summarize_outputs(workflow_api.get("outputs")),
        )
        print("  webhook:", webhook["http_status"], webhook.get("body"))
    print(f"\nResults written to {result_path}")
    return 0


def call_workflow_api(
    client: httpx.Client,
    config: dict[str, Any],
    endpoint: dict[str, Any],
) -> dict[str, Any]:
    url = f"{config['base_url'].rstrip('/')}/v1/workflows/run"
    body = {
        "inputs": {"payload": endpoint["inputs"]},
        "response_mode": "blocking",
        "user": config.get("user", "local-dify-test"),
    }
    headers = {
        "Authorization": f"Bearer {endpoint['api_key']}",
        "Content-Type": "application/json",
    }
    try:
        response = client.post(url, json=body, headers=headers)
        parsed = parse_response_body(response)
        data = parsed.get("data") if isinstance(parsed, dict) else None
        return {
            "http_status": response.status_code,
            "ok": 200 <= response.status_code < 300,
            "dify_status": data.get("status") if isinstance(data, dict) else None,
            "outputs": data.get("outputs") if isinstance(data, dict) else None,
            "body": parsed,
        }
    except Exception as exc:
        return {
            "http_status": None,
            "ok": False,
            "error": str(exc),
        }


def call_webhook(client: httpx.Client, endpoint: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post(endpoint["webhook_url"], json=endpoint["inputs"])
        return {
            "http_status": response.status_code,
            "ok": 200 <= response.status_code < 300,
            "body": parse_response_body(response),
        }
    except Exception as exc:
        return {
            "http_status": None,
            "ok": False,
            "error": str(exc),
        }


def parse_response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def summarize_outputs(outputs: Any) -> str:
    if outputs is None:
        return "outputs=null"
    text = json.dumps(outputs, ensure_ascii=False)
    if len(text) > 180:
        text = text[:177] + "..."
    return text


if __name__ == "__main__":
    raise SystemExit(main())
