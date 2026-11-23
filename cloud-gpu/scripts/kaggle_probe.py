#!/usr/bin/env python3
"""Low-level Kaggle API probe (read-only) for quota/session debugging.

Sends authenticated POSTs to Kaggle's Connect-RPC endpoints and prints status +
body so a failing CLI path can be diagnosed from a workflow log. Read-only:
GET-like service methods only (`GetKernelSessionStatus`, quota statistics,
`ListKernels`, `GetKernel`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def token() -> str | None:
    for key in ("KAGGLE_API_TOKEN", "KAGGLE_ACCESS_TOKEN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    for path in ("~/.kaggle/access_token", "~/.gpu-relay/kaggle_token"):
        full = os.path.expanduser(path)
        if os.path.exists(full):
            raw = open(full, "r", encoding="utf-8").read().strip()
            if raw:
                return raw if not raw.startswith("{") else None
    return None


def basic_creds() -> tuple[str, str] | None:
    path = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(path):
        try:
            data = json.load(open(path, "r", encoding="utf-8"))
            return data["username"], data["key"]
        except (OSError, KeyError, json.JSONDecodeError):
            return None
    return None


def post(method: str, body: dict, out_path: str = "") -> dict:
    import requests

    url = f"https://api.kaggle.com/v1/kernels.KernelsApiService/{method}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    auth = None
    tok = token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
        mode = "bearer"
    elif basic_creds():
        auth = basic_creds()
        mode = "basic"
    else:
        mode = "none"

    resp = requests.post(url, json=body, headers=headers, auth=auth, timeout=60)
    result = {
        "method": method,
        "auth_mode": mode,
        "status_code": resp.status_code,
        "body": resp.text[:4000],
    }
    print(f"{method}: HTTP {resp.status_code} ({mode}) {resp.text[:400]}", flush=True)
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="", help="'<owner>/<slug>' for status/get-kernel")
    parser.add_argument("--mine", action="store_true", help="also call ListKernels for my profile")
    parser.add_argument("--logs", default="", help="fetch the session log stream for '<owner>/<slug>'")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    try:
        import requests  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"requests unavailable: {exc}", file=sys.stderr)
        return 1

    if args.ref:
        owner, _, slug = args.ref.partition("/")
        post("GetKernelSessionStatus", {"userName": owner, "kernelSlug": slug}, args.out)
        post("GetKernel", {"userName": owner, "kernelSlug": slug}, args.out)

    if args.logs:
        owner, _, slug = args.logs.partition("/")
        from kaggle.api.kaggle_api_extended import KaggleApi
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionLogsStreamRequest

        api = KaggleApi()
        api.authenticate()
        req = ApiGetKernelSessionLogsStreamRequest()
        for attr, value in (("user_name", owner), ("kernel_slug", slug)):
            if hasattr(req, attr):
                setattr(req, attr, value)
        try:
            with api.build_kaggle_client() as kaggle:
                stream = kaggle.kernels.kernels_api_client.get_kernel_session_logs_stream(req)
            text = ""
            try:
                text = stream.read().decode("utf-8", "replace")[:6000] if hasattr(stream, "read") else str(stream)[:6000]
            except Exception:  # noqa: BLE001
                text = str(stream)[:6000]
            print(f"--- logs for {args.logs} ---\n{text}", flush=True)
            if args.out:
                with open(args.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"logs_ref": args.logs, "body": text}, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            print(f"logs failed: {type(exc).__name__}: {exc}", flush=True)

    if args.mine:
        post("ListKernels", {"group": "profile", "pageSize": 20}, args.out)

    for extra in ("ListKernelSessions", "GetKernelSessions", "ListSessions"):
        post(extra, {}, args.out)

    post("GetAcceleratorQuotaStatistics", {}, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
