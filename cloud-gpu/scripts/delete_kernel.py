#!/usr/bin/env python3
"""Delete a Kaggle kernel (with output) by ref, with an explicit confirmation flag.

Used by the quota probe when a kernel that never started a session is still
listed as owning a GPU slot. Deleting is irreversible, so `--confirm` is
required; the workflow passes it only when the request file asks for it.
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ref", help="kernel ref '<owner>/<slug>'")
    parser.add_argument("--confirm", action="store_true", help="required: actually delete")
    parser.add_argument("--out", default="", help="write request/response log here")
    args = parser.parse_args()

    lines: list[str] = []
    owner, _, slug = args.ref.partition("/")
    if not owner or not slug:
        print("ref must look like '<owner>/<slug>'", file=sys.stderr)
        return 2

    lines.append(f"delete target: {owner}/{slug} confirm={args.confirm}")
    if not args.confirm:
        print("\n".join(lines) + "\n(refusing without --confirm)", file=sys.stderr)
        return 2

    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        from kagglesdk.kernels.types.kernels_api_service import ApiDeleteKernelRequest

        api = KaggleApi()
        api.authenticate()

        request = ApiDeleteKernelRequest()
        for attr, value in (("user_name", owner), ("kernel_slug", slug)):
            if hasattr(request, attr):
                setattr(request, attr, value)
        lines.append(f"delete request: {json.dumps({k: v for k, v in vars(request).items() if not k.startswith('_')}, default=str)}")

        with api.build_kaggle_client() as kaggle:
            response = kaggle.kernels.kernels_api_client.delete_kernel(request)
        lines.append(f"delete response: {response}")
        rc = 0
    except Exception as exc:  # noqa: BLE001
        lines.append(f"delete failed: {type(exc).__name__}: {exc}"[:1500])
        rc = 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
