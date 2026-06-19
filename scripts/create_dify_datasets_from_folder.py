from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


DEFAULT_FOLDER = r"D:\桌面文件\PDFwenzhang"
DEFAULT_OUTPUT = "output/dify_dataset_import_results.json"
DATASET_NAME_LIMIT = 40


def main() -> int:
    args = parse_args()
    env = load_dotenv(Path(args.env_file))
    base_url = normalize_base_url(
        args.base_url
        or env.get("DIFY_DATASET_BASE_URL")
        or env.get("DIFY_BASE_URL")
        or "https://api.dify.ai/v1"
    )
    api_key = args.api_key or env.get("DIFY_DATASET_API_KEY") or env.get("DIFY_API_KEY")

    source_folder = Path(args.folder)
    output_path = Path(args.output)
    files = collect_files(source_folder, recursive=args.recursive)

    if not files:
        print(f"No files found in {source_folder}", file=sys.stderr)
        return 1

    embedding_model = (
        args.embedding_model
        or env.get("DIFY_DATASET_EMBEDDING_MODEL")
        or "text-embedding-3-small"
    )
    embedding_model_provider = (
        args.embedding_model_provider
        or env.get("DIFY_DATASET_EMBEDDING_MODEL_PROVIDER")
        or "openai"
    )

    planned = [
        {
            "file": str(file),
            "size": file.stat().st_size,
            "dataset_name": make_dataset_name(file.stem, args.name_prefix),
        }
        for file in files
    ]

    if args.dry_run:
        result = {
            "dry_run": True,
            "base_url": base_url,
            "source_folder": str(source_folder),
            "file_count": len(planned),
            "embedding_model": embedding_model,
            "embedding_model_provider": embedding_model_provider,
            "retrieval_model": build_retrieval_model(
                args.semantic_weight,
                embedding_model=embedding_model,
                embedding_model_provider=embedding_model_provider,
            ),
            "items": [
                {
                    **item,
                    "would_create_dataset": True,
                    "would_upload_file": True,
                }
                for item in planned
            ],
        }
        write_json(output_path, result)
        print(f"Dry run complete. Planned {len(planned)} datasets.")
        print(f"Result written to {output_path}")
        return 0

    if not api_key:
        print(
            "Missing Dify knowledge API key. Set DIFY_DATASET_API_KEY in .env "
            "or pass --api-key.",
            file=sys.stderr,
        )
        return 2

    results: list[dict[str, Any]] = []
    resume_items = load_resume_items(output_path) if args.resume else {}
    with httpx.Client(timeout=args.timeout_seconds) as client:
        for index, file in enumerate(files, start=1):
            resume_item = resume_items.get(str(file))
            if resume_item and resume_item.get("status") == "uploaded":
                print(f"[{index}/{len(files)}] Skipping uploaded file: {file.name}")
                skipped = dict(resume_item)
                skipped["resume_action"] = "skipped_uploaded"
                results.append(skipped)
                write_json(
                    output_path,
                    build_result(
                        args,
                        base_url,
                        results,
                        embedding_model=embedding_model,
                        embedding_model_provider=embedding_model_provider,
                    ),
                )
                continue

            dataset_name = make_dataset_name(file.stem, args.name_prefix)
            print(f"[{index}/{len(files)}] Creating dataset: {dataset_name}")
            item: dict[str, Any] = {
                "file": str(file),
                "size": file.stat().st_size,
                "dataset_name": dataset_name,
                "dataset_id": None,
                "document_id": None,
                "batch": None,
                "status": "pending",
                "error": None,
            }
            try:
                if resume_item and resume_item.get("dataset_id"):
                    dataset_id = resume_item["dataset_id"]
                    item.update(
                        {
                            "dataset_id": dataset_id,
                            "resume_action": "reuse_dataset_upload_file",
                        }
                    )
                    print(
                        f"[{index}/{len(files)}] Reusing dataset for upload: "
                        f"{dataset_id}"
                    )
                else:
                    dataset = create_dataset(
                        client=client,
                        base_url=base_url,
                        api_key=api_key,
                        name=dataset_name,
                        description=f"Imported from {file.name}",
                        embedding_model=embedding_model,
                        embedding_model_provider=embedding_model_provider,
                        retrieval_model=build_retrieval_model(
                            args.semantic_weight,
                            embedding_model=embedding_model,
                            embedding_model_provider=embedding_model_provider,
                        ),
                    )
                    dataset_id = dataset["id"]
                    item["dataset_id"] = dataset_id

                print(f"[{index}/{len(files)}] Uploading file: {file.name}")
                uploaded = upload_document_by_file(
                    client=client,
                    base_url=base_url,
                    api_key=api_key,
                    dataset_id=dataset_id,
                    file_path=file,
                    doc_language=args.doc_language,
                    embedding_model=embedding_model,
                    embedding_model_provider=embedding_model_provider,
                    retrieval_model=build_retrieval_model(
                        args.semantic_weight,
                        embedding_model=embedding_model,
                        embedding_model_provider=embedding_model_provider,
                    ),
                )
                document = uploaded.get("document") or {}
                item["document_id"] = document.get("id")
                item["batch"] = uploaded.get("batch")
                item["indexing_status"] = document.get("indexing_status")
                item["status"] = "uploaded"
            except Exception as exc:  # noqa: BLE001 - script records per-file failures.
                item["status"] = "failed"
                item["error"] = str(exc)
                print(f"[{index}/{len(files)}] Failed: {exc}", file=sys.stderr)
                if not args.continue_on_error or is_systemic_error(exc):
                    results.append(item)
                    write_json(
                        output_path,
                        build_result(
                            args,
                            base_url,
                            results,
                            embedding_model=embedding_model,
                            embedding_model_provider=embedding_model_provider,
                        ),
                    )
                    return 1
            results.append(item)
            write_json(
                output_path,
                build_result(
                    args,
                    base_url,
                    results,
                    embedding_model=embedding_model,
                    embedding_model_provider=embedding_model_provider,
                ),
            )

            if args.sleep_seconds > 0 and index < len(files):
                time.sleep(args.sleep_seconds)

    failed_count = sum(1 for item in results if item["status"] == "failed")
    print(
        f"Import complete. Uploaded {len(results) - failed_count}/{len(results)} files. "
        f"Failed {failed_count}."
    )
    print(f"Result written to {output_path}")
    return 1 if failed_count else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create one Dify knowledge base per file in a folder and upload each "
            "file into its own knowledge base."
        )
    )
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Source folder.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Result JSON path.")
    parser.add_argument("--base-url", default=None, help="Dify API base URL.")
    parser.add_argument("--api-key", default=None, help="Dify knowledge API key.")
    parser.add_argument("--name-prefix", default="", help="Optional dataset name prefix.")
    parser.add_argument(
        "--doc-language",
        default="Chinese",
        help="Document language sent to Dify document creation API.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model name for Dify high quality indexing.",
    )
    parser.add_argument(
        "--embedding-model-provider",
        default=None,
        help="Embedding model provider for Dify high quality indexing.",
    )
    parser.add_argument(
        "--semantic-weight",
        type=float,
        default=0.5,
        help="Semantic search weight for Dify hybrid search.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="HTTP timeout per Dify request.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1,
        help="Delay between files to reduce API pressure.",
    )
    parser.add_argument("--recursive", action="store_true", help="Scan subfolders.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; no API calls.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the output JSON, skipping uploaded files and reusing failed dataset ids.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        dest="continue_on_error",
        help="Continue after per-file failures. Systemic API errors still stop.",
    )
    parser.set_defaults(continue_on_error=False)
    return parser.parse_args()


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("Dify base URL is empty.")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def collect_files(folder: Path, *, recursive: bool) -> list[Path]:
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")

    iterator = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(path for path in iterator if path.is_file())


def make_dataset_name(stem: str, prefix: str) -> str:
    clean_stem = " ".join(stem.strip().split())
    if not clean_stem:
        clean_stem = "dataset"
    raw_name = f"{prefix}{clean_stem}"
    if len(raw_name) <= DATASET_NAME_LIMIT:
        return raw_name

    digest = hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:8]
    keep = DATASET_NAME_LIMIT - len(digest) - 1
    return f"{raw_name[:keep]}-{digest}"


def build_retrieval_model(
    semantic_weight: float,
    *,
    embedding_model: str,
    embedding_model_provider: str,
) -> dict[str, Any]:
    if semantic_weight < 0 or semantic_weight > 1:
        raise ValueError("--semantic-weight must be between 0 and 1.")
    keyword_weight = round(1 - semantic_weight, 4)
    return {
        "search_method": "hybrid_search",
        "reranking_enable": False,
        "reranking_mode": "weighted_score",
        "reranking_model": {
            "reranking_provider_name": "",
            "reranking_model_name": "",
        },
        "weights": {
            "weight_type": "customized",
            "vector_setting": {
                "vector_weight": semantic_weight,
                "embedding_provider_name": embedding_model_provider,
                "embedding_model_name": embedding_model,
            },
            "keyword_setting": {
                "keyword_weight": keyword_weight,
            },
        },
        "top_k": 3,
        "score_threshold_enabled": False,
        "score_threshold": None,
    }


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def create_dataset(
    *,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    name: str,
    description: str,
    embedding_model: str,
    embedding_model_provider: str,
    retrieval_model: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "name": name,
        "description": description[:400],
        "permission": "only_me",
        "provider": "vendor",
        "indexing_technique": "high_quality",
        "embedding_model": embedding_model,
        "embedding_model_provider": embedding_model_provider,
        "retrieval_model": retrieval_model,
    }
    response = client.post(
        f"{base_url}/datasets",
        headers={**auth_headers(api_key), "Content-Type": "application/json"},
        json=payload,
    )
    ensure_success(response, "create dataset")
    data = response.json()
    if "id" not in data:
        raise RuntimeError(f"Dify create dataset response missing id: {data}")
    return data


def upload_document_by_file(
    *,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    dataset_id: str,
    file_path: Path,
    doc_language: str,
    embedding_model: str,
    embedding_model_provider: str,
    retrieval_model: dict[str, Any],
) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    data = {
        "indexing_technique": "high_quality",
        "doc_form": "text_model",
        "doc_language": doc_language,
        "process_rule": {"mode": "automatic"},
        "embedding_model": embedding_model,
        "embedding_model_provider": embedding_model_provider,
        "retrieval_model": retrieval_model,
    }
    with file_path.open("rb") as file_obj:
        response = client.post(
            f"{base_url}/datasets/{dataset_id}/document/create-by-file",
            headers=auth_headers(api_key),
            data={"data": json.dumps(data, ensure_ascii=False)},
            files={"file": (file_path.name, file_obj, mime_type)},
        )
    ensure_success(response, "upload document")
    return response.json()


def ensure_success(response: httpx.Response, operation: str) -> None:
    if 200 <= response.status_code < 300:
        return
    try:
        detail = response.json()
    except json.JSONDecodeError:
        detail = response.text
    raise RuntimeError(f"Dify {operation} failed: HTTP {response.status_code} {detail}")


def is_systemic_error(exc: Exception) -> bool:
    message = str(exc)
    systemic_markers = (
        "DatasetCreatePayload",
        "KnowledgeConfig",
        "request rate limit",
        "subscription",
        "invalid_param",
        "forbidden",
    )
    return any(marker in message for marker in systemic_markers)


def build_result(
    args: argparse.Namespace,
    base_url: str,
    items: list[dict[str, Any]],
    *,
    embedding_model: str,
    embedding_model_provider: str,
) -> dict[str, Any]:
    return {
        "dry_run": False,
        "base_url": base_url,
        "source_folder": args.folder,
        "embedding_model": embedding_model,
        "embedding_model_provider": embedding_model_provider,
        "retrieval_model": build_retrieval_model(
            args.semantic_weight,
            embedding_model=embedding_model,
            embedding_model_provider=embedding_model_provider,
        ),
        "items": items,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_resume_items(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    return {
        str(item["file"]): item
        for item in items
        if isinstance(item, dict) and item.get("file")
    }


if __name__ == "__main__":
    raise SystemExit(main())
