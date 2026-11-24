#!/usr/bin/env python3
"""Free Kaggle GPU slots by deleting stale smoke kernels.

Kaggle refuses new GPU pushes with "Maximum batch GPU session count of 2 reached"
when two sessions are already held — and a kernel whose session never started
still counts as a pending session for a while. When the probe only pushes
`quota-smoke-*` kernels, the safe move is to delete the older ones (they carry no
results we need) so the new run can start immediately.

Only kernels whose slug starts with `--prefix` are considered, and any ref listed
by `--keep` (comma separated) is left alone. `--max-delete` caps the damage.
"""

from __future__ import annotations

import argparse
import json
import sys


def log(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="quota-smoke-")
    parser.add_argument("--keep", default="", help="comma separated refs to keep")
    parser.add_argument("--max-delete", type=int, default=8)
    parser.add_argument("--out", default="", help="write the report here")
    args = parser.parse_args()

    keep = {item.strip() for item in args.keep.split(",") if item.strip()}
    lines: list[str] = []

    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        from kagglesdk.kernels.types.kernels_api_service import ApiDeleteKernelRequest

        api = KaggleApi()
        api.authenticate()

        import subprocess

        listing = subprocess.run(
            ["kaggle", "kernels", "list", "-m", "--page-size", "50", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        text = (listing.stdout or "").strip()
        rows = json.loads(text) if text.startswith("[") else []
        refs = [row.get("ref") for row in rows if row.get("ref")]

        slug_prefix = args.prefix.split("/")[-1]
        candidates = [ref for ref in refs if ref.split("/")[-1].startswith(slug_prefix) and ref not in keep]
        lines.append(f"mine={len(refs)} candidates={candidates} keep={sorted(keep)}")

        for ref in candidates[: args.max_delete]:
            owner, _, slug = ref.partition("/")
            try:
                request = ApiDeleteKernelRequest()
                request.user_name = owner
                request.kernel_slug = slug
                with api.build_kaggle_client() as kaggle:
                    response = kaggle.kernels.kernels_api_client.delete_kernel(request)
                lines.append(f"deleted {ref} (errorMessage={getattr(response, 'error_message', None)!r})")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"delete failed {ref}: {type(exc).__name__}: {str(exc)[:200]}")

    except Exception as exc:  # noqa: BLE001
        lines.append(f"slot cleanup unavailable: {type(exc).__name__}: {str(exc)[:300]}")

    blob = "\n".join(lines)
    log(blob)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(blob + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
