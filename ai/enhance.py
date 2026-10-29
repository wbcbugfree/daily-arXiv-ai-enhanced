import os
import json
import sys
import re
import time
import math
import tempfile
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event
from typing import Dict, List
from urllib.parse import urlparse
# INSERT_YOUR_CODE
import requests

import dotenv
import argparse
from tqdm import tqdm

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from structure import Structure, create_structure

if os.path.exists('.env'):
    dotenv.load_dotenv()
template = Path(__file__).with_name("template.txt").read_text(encoding="utf-8")
system = Path(__file__).with_name("system.txt").read_text(encoding="utf-8")

DEFAULT_RETRY_DELAY_SECONDS = 1.0
DEFAULT_RATE_LIMIT_RETRY_DELAY_SECONDS = 5.0


class FatalAIError(RuntimeError):
    """Credentials or provider configuration prevent the batch from running."""


class PaperAIError(RuntimeError):
    """A paper exhausted retries; its fallback fields remain on the item."""


def _positive_seconds(value):
    """Return a positive numeric delay, or None for invalid values."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _error_payload(error: Exception) -> Dict:
    """Extract the OpenAI-compatible error payload when one is available."""
    body = getattr(error, "body", None)
    if not isinstance(body, dict) and error.args:
        body = error.args[0]
        if hasattr(body, "model_dump"):
            body = body.model_dump()
    if not isinstance(body, dict):
        return {}
    nested_error = body.get("error")
    return nested_error if isinstance(nested_error, dict) else body


def is_fatal_api_error(error: Exception) -> bool:
    """Distinguish batch-wide setup failures from transient or paper errors."""
    payload = _error_payload(error)
    status = getattr(error, "status_code", None)
    if status is None and isinstance(payload.get("code"), int):
        status = payload["code"]
    codes = {str(payload.get(key, "")).lower() for key in ("code", "type")}
    if status in {401, 402} or codes.intersection({
        "invalid_api_key", "authentication_error", "model_not_found", "invalid_model",
        "insufficient_quota", "credit_balance_exhausted",
        "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    }):
        return True

    message = str(payload.get("message") or error).lower()
    # A provider may reject just one abstract for length or content reasons.
    paper_error_codes = {"context_length_exceeded", "content_policy_violation", "content_filter"}
    paper_error_markers = ("context length", "context window", "moderation", "content policy", "content filter")
    if codes.intersection(paper_error_codes) or any(marker in message for marker in paper_error_markers):
        return False
    if status in {403, 404}:
        return True
    if status == 400:
        param = str(payload.get("param", "")).split(".", 1)[0].split("[", 1)[0]
        config_params = {
            "model", "tools", "tool_choice", "reasoning", "reasoning_effort",
            "text", "response_format", "service_tier",
        }
        config_markers = (
            "invalid model", "not a valid model", "unsupported parameter",
            "unrecognized request argument",
        )
        return param in config_params or any(marker in message for marker in config_markers)
    return False


def is_rate_limit_error(error: Exception) -> bool:
    """Recognize HTTP and OpenRouter upstream-provider rate-limit errors."""
    if getattr(error, "status_code", None) == 429:
        return True

    payload = _error_payload(error)
    metadata = payload.get("metadata", {})
    if payload.get("code") == 429:
        return True
    if isinstance(metadata, dict) and metadata.get("provider_error_code") == "upstream_429":
        return True

    error_text = str(error).lower()
    return "error code: 429" in error_text or "temporarily rate-limited" in error_text


def get_retry_delay_seconds(error: Exception) -> float:
    """Read Retry-After from response headers or OpenRouter error metadata."""
    response = getattr(error, "response", None)
    response_headers = getattr(response, "headers", None)
    if response_headers:
        retry_after = _positive_seconds(
            response_headers.get("Retry-After") or response_headers.get("retry-after")
        )
        if retry_after is not None:
            return retry_after

    payload = _error_payload(error)
    metadata = payload.get("metadata", {})
    if isinstance(metadata, dict):
        for key in ("retry_after_seconds", "retry_after_seconds_raw"):
            retry_after = _positive_seconds(metadata.get(key))
            if retry_after is not None:
                return retry_after

        metadata_headers = metadata.get("headers", {})
        if isinstance(metadata_headers, dict):
            retry_after = _positive_seconds(
                metadata_headers.get("Retry-After") or metadata_headers.get("retry-after")
            )
            if retry_after is not None:
                return retry_after

    if is_rate_limit_error(error):
        return DEFAULT_RATE_LIMIT_RETRY_DELAY_SECONDS
    return DEFAULT_RETRY_DELAY_SECONDS

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True, help="jsonline data file")
    parser.add_argument("--max_workers", type=int, default=1, help="Maximum number of parallel workers")
    return parser.parse_args()

def process_single_item(
    chain, item: Dict, language: str, max_retries: int = 3, abort_event: Event | None = None
) -> Dict:
    if abort_event is not None and abort_event.is_set():
        raise CancelledError("AI batch stopped after a fatal provider error")

    def check_github_code(content: str) -> Dict:
        """提取并验证 GitHub 链接"""
        code_info = {}

        # 1. 优先匹配 github.com/owner/repo 格式
        github_pattern = r"https?://github\.com/([a-zA-Z0-9-_]+)/([a-zA-Z0-9-_\.]+)"
        match = re.search(github_pattern, content)
        
        if match:
            owner, repo = match.groups()
            # 清理 repo 名称，去掉可能的 .git 后缀或末尾的标点
            repo = repo.rstrip(".,)").removesuffix(".git")
            
            full_url = f"https://github.com/{owner}/{repo}"
            code_info["code_url"] = full_url
            
            # 尝试调用 GitHub API 获取信息
            github_token = os.environ.get("TOKEN_GITHUB")
            headers = {"Accept": "application/vnd.github.v3+json"}
            if github_token:
                headers["Authorization"] = f"token {github_token}"
            
            try:
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                resp = requests.get(api_url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    code_info["code_stars"] = data.get("stargazers_count", 0)
                    code_info["code_last_update"] = data.get("pushed_at", "")[:10]
            except Exception:
                # API 调用失败不影响主流程
                pass
            return code_info

        # 2. 如果没有 github.com，尝试匹配 github.io
        github_io_pattern = r"https?://[a-zA-Z0-9-_]+\.github\.io(?:/[a-zA-Z0-9-_\.]+)*"
        match_io = re.search(github_io_pattern, content)
        
        if match_io:
            url = match_io.group(0)
            # 清理末尾标点
            url = url.rstrip(".,)")
            code_info["code_url"] = url
            # github.io 不进行 star 和 update 判断
                
        return code_info

    # 检测代码可用性
    code_info = check_github_code(item.get("summary", ""))
    if code_info:
        item.update(code_info)

    """处理单个数据项"""
    # Default structure with meaningful fallback values
    default_ai_fields = {
        "tldr": "Summary generation failed",
        "motivation": "Motivation analysis unavailable",
        "method": "Method extraction failed",
        "result": "Result analysis unavailable",
        "conclusion": "Conclusion extraction failed"
    }

    # Retry logic: attempt up to max_retries times
    for attempt in range(max_retries):
        if abort_event is not None and abort_event.is_set():
            raise CancelledError("AI batch stopped after a fatal provider error")
        try:
            response: Structure = chain.invoke({
                "language": language,
                "content": item['summary']
            })
            item['AI'] = response.model_dump()
            if not any(
                isinstance(item['AI'].get(field), str) and item['AI'][field].strip()
                for field in default_ai_fields
            ):
                raise ValueError("AI returned no usable analysis")
            # Success! Break out of retry loop
            if attempt > 0:
                print(f"Success on attempt {attempt + 1} for {item.get('id', 'unknown')}", file=sys.stderr)
            break
        except langchain_core.exceptions.OutputParserException as e:
            if attempt < max_retries - 1:
                print(f"OutputParserException on attempt {attempt + 1} for {item.get('id', 'unknown')}, retrying...", file=sys.stderr)
                time.sleep(1)  # Brief delay before retry to avoid hammering the API
                continue
            
            # Final attempt failed, try to extract partial data
            error_msg = str(e)
            partial_data = {}
            
            if "Function Structure arguments:" in error_msg:
                try:
                    # 提取 JSON 字符串
                    json_str = error_msg.split("Function Structure arguments:", 1)[1].strip().split('are not valid JSON')[0].strip()
                    # 预处理 LaTeX 数学符号 - 使用四个反斜杠来确保正确转义
                    json_str = json_str.replace('\\', '\\\\')
                    # 尝试解析修复后的 JSON
                    partial_data = json.loads(json_str)
                except Exception as json_e:
                    print(f"Failed to parse JSON for {item.get('id', 'unknown')}: {json_e}", file=sys.stderr)
            
            partial_data = {
                key: value for key, value in partial_data.items()
                if key in default_ai_fields and isinstance(value, str) and value.strip()
            } if isinstance(partial_data, dict) else {}
            # Keep usable partial analysis, but don't count an empty fallback as success.
            item['AI'] = {**default_ai_fields, **partial_data}
            if not partial_data:
                raise PaperAIError(f"No usable AI output for {item.get('id', 'unknown')}") from e
            print(f"Using partial AI data after {max_retries} attempts for {item.get('id', 'unknown')}: {list(partial_data.keys())}", file=sys.stderr)
        except Exception as e:
            if is_fatal_api_error(e):
                if abort_event is not None:
                    abort_event.set()
                raise FatalAIError(
                    f"AI authentication or configuration failed for {item.get('id', 'unknown')}: {e}"
                ) from e
            if attempt < max_retries - 1:
                retry_delay = get_retry_delay_seconds(e)
                print(
                    f"Error on attempt {attempt + 1} for {item.get('id', 'unknown')}: "
                    f"{e}, retrying in {retry_delay:g} seconds...",
                    file=sys.stderr,
                )
                time.sleep(retry_delay)
                continue
            
            # All retries failed, use default values
            print(f"All {max_retries} attempts failed for {item.get('id', 'unknown')}: {e}", file=sys.stderr)
            item['AI'] = default_ai_fields
            raise PaperAIError(f"AI retries exhausted for {item.get('id', 'unknown')}") from e

    # Final validation to ensure all required fields exist
    for field in default_ai_fields.keys():
        if field not in item['AI']:
            item['AI'][field] = default_ai_fields[field]

    return item

def create_analysis_chain(model_name: str, language: str):
    """Use Responses for OpenAI while preserving other providers' chat API."""
    model_name = model_name.strip()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    missing = [name for name, value in (("MODEL_NAME", model_name), ("OPENAI_API_KEY", api_key)) if not value]
    if missing:
        raise ValueError(f"Missing required configuration: {', '.join(missing)}")
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("OPENAI_BASE_URL must be an HTTP(S) URL")
    api_mode = os.environ.get("API_MODE", "auto").strip().lower() or "auto"
    if api_mode == "auto":
        api_mode = "responses" if urlparse(base_url).hostname == "api.openai.com" else "chat_completions"
    if api_mode not in {"responses", "chat_completions"}:
        raise ValueError("API_MODE must be auto, responses, or chat_completions")

    LocalizedStructure = create_structure(language)
    llm_options = {
        "model": model_name,
        "base_url": base_url,
        "api_key": api_key,
        "use_responses_api": api_mode == "responses",
    }
    if api_mode == "responses":
        # Both providers can run independent requests without server-side state.
        llm_options["model_kwargs"] = {"store": False}
        reasoning_effort = os.environ.get("REASONING_EFFORT", "").strip()
        if reasoning_effort:
            llm_options["reasoning_effort"] = reasoning_effort
        llm = ChatOpenAI(**llm_options).with_structured_output(
            LocalizedStructure, method="json_schema", strict=True
        )
    else:
        llm = ChatOpenAI(**llm_options).with_structured_output(
            LocalizedStructure, method="function_calling"
        )

    print('Connect to:', model_name, file=sys.stderr)
    print('API mode:', api_mode, file=sys.stderr)
    
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system),
        HumanMessagePromptTemplate.from_template(template=template)
    ])

    return prompt_template | llm


def process_all_items(data: List[Dict], model_name: str, language: str, max_workers: int) -> List[Dict]:
    """并行处理所有数据项"""
    chain = create_analysis_chain(model_name, language)
    
    # 使用线程池并行处理
    processed_data = [None] * len(data)  # 预分配结果列表
    failed_count = 0
    abort_event = Event()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_idx = {
            executor.submit(process_single_item, chain, item, language, abort_event=abort_event): idx
            for idx, item in enumerate(data)
        }
        
        # 使用tqdm显示进度
        for future in tqdm(
            as_completed(future_to_idx),
            total=len(data),
            desc="Processing items"
        ):
            idx = future_to_idx[future]
            try:
                result = future.result()
                processed_data[idx] = result
            except FatalAIError:
                abort_event.set()
                for pending in future_to_idx:
                    pending.cancel()
                raise
            except PaperAIError as e:
                failed_count += 1
                processed_data[idx] = data[idx]
                print(f"Paper at index {idx} kept with fallback AI fields: {e}", file=sys.stderr)
            except Exception as e:
                failed_count += 1
                print(f"Item at index {idx} generated an exception: {e}", file=sys.stderr)
                # Add default AI fields to ensure consistency
                processed_data[idx] = data[idx]
                processed_data[idx]['AI'] = {
                    "tldr": "Processing failed",
                    "motivation": "Processing failed",
                    "method": "Processing failed",
                    "result": "Processing failed",
                    "conclusion": "Processing failed"
                }
    
    if data and failed_count == len(data):
        raise RuntimeError(f"All {len(data)} papers failed AI enhancement; refusing to publish placeholders")
    print(f"AI enhancement results: {len(data) - failed_count} usable, {failed_count} failed, {len(data)} total", file=sys.stderr)
    return processed_data

def main():
    args = parse_args()
    model_name = os.environ.get("MODEL_NAME", 'deepseek-chat')
    language = os.environ.get("LANGUAGE", 'Chinese')

    # Preserve an existing output if configuration or batch processing fails.
    target_file = args.data.replace('.jsonl', f'_AI_enhanced_{language}.jsonl')

    # 读取数据
    data = []
    with open(args.data, "r", encoding="utf-8") as f:
        for line in f:
            data.append(json.loads(line))

    # 去重
    seen_ids = set()
    unique_data = []
    for item in data:
        if item['id'] not in seen_ids:
            seen_ids.add(item['id'])
            unique_data.append(item)

    data = unique_data
    print('Open:', args.data, file=sys.stderr)
    
    # 并行处理所有数据
    processed_data = process_all_items(
        data,
        model_name,
        language,
        args.max_workers
    )
    
    # Replace the previous output only after the complete new file is written.
    temporary_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=Path(target_file).parent,
            prefix=Path(target_file).name + ".", suffix=".tmp", delete=False,
        ) as f:
            temporary_file = f.name
            for item in processed_data:
                if item is not None:
                    f.write(json.dumps(item) + "\n")
        os.replace(temporary_file, target_file)
    finally:
        if temporary_file is not None and os.path.exists(temporary_file):
            os.remove(temporary_file)

if __name__ == "__main__":
    main()
