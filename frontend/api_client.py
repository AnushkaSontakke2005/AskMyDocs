import json
import os
import uuid
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st
from dotenv import load_dotenv


load_dotenv()

DEFAULT_API_BASE_URL = "https://askmydocs-production-ed37.up.railway.app"
APP_BUILD_VERSION = "2026-05-09-streamlit-api-debug"


def _api_base_url() -> str:
    if os.getenv("ASKMYDOCS_API_URL"):
        return os.getenv("ASKMYDOCS_API_URL", "").rstrip("/")
    try:
        return st.secrets.get("ASKMYDOCS_API_URL", DEFAULT_API_BASE_URL).rstrip("/")
    except Exception:
        return DEFAULT_API_BASE_URL


def get_api_base_url() -> str:
    return _api_base_url()


class ApiClientError(Exception):
    pass


def get(path: str, params: Optional[Dict[str, Any]] = None):
    query = f"?{urlencode(params)}" if params else ""
    return _request("GET", f"{path}{query}")


def post(path: str, payload: Optional[Dict[str, Any]] = None):
    return _request("POST", path, payload)


def delete(path: str):
    return _request("DELETE", path)


def upload(path: str, file_name: str, file_bytes: bytes, content_type: str = "application/pdf"):
    boundary = f"----AskMyDocsBoundary{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return _request(
        "POST",
        path,
        raw_body=body,
        extra_headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def _request(
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    raw_body: Optional[bytes] = None,
    extra_headers: Optional[Dict[str, str]] = None,
):
    headers = extra_headers.copy() if extra_headers else {}
    token = st.session_state.get("api_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = raw_body
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{_api_base_url()}{path}", data=data, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=120)
        if response.status == 204:
            return None
        content = response.read()
        return json.loads(content.decode("utf-8")) if content else None
    except HTTPError as exc:
        detail = exc.read().decode("utf-8")
        try:
            parsed = json.loads(detail)
            detail = parsed.get("detail", detail)
        except json.JSONDecodeError:
            pass
        raise ApiClientError(str(detail))
    except URLError as exc:
        api_base_url = _api_base_url()
        raise ApiClientError(
            "Could not reach the AskMyDocs API. "
            f"Current API URL: {api_base_url}. "
            "If this is Streamlit Cloud, set ASKMYDOCS_API_URL to your deployed backend URL."
        ) from exc
