from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from civitmatrix import __version__
from civitmatrix.cancel_control import request_cancel_cli
from civitmatrix.client import CivitClient
from civitmatrix.downloader import run_batch, run_heal
from civitmatrix.logging_io import RunLogger
from civitmatrix.pause_control import request_pause_cli, request_resume_cli
from civitmatrix.status_control import print_status_cli

SORT_CHOICES = [
    "Highest Rated",
    "Most Downloaded",
    "Newest",
    "Most Liked",
    "Most Discussed",
    "Most Collected",
    "Most Buzz",
]

TYPE_CHOICES = [
    "Checkpoint",
    "TextualInversion",
    "Hypernetwork",
    "AestheticGradient",
    "LORA",
    "LoCon",
    "DoRA",
    "Controlnet",
    "Upscaler",
    "MotionModule",
    "VAE",
    "Poses",
    "Wildcards",
    "Workflows",
    "Other",
]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="civitmatrix",
        description=(
            "Batch-download CivitAI models with Stability Matrix–native sidecars "
            "(.safetensors + .cm-info.json + preview) so SM shows Installed."
        ),
    )
    p.add_argument("--version", action="version", version=f"civitmatrix {__version__}")
    p.add_argument("--dry-run", action="store_true", help="List actions without downloading")
    p.add_argument("--limit", type=int, default=0, help="Max models to process (0 = all)")
    p.add_argument(
        "--retry-failed",
        action="store_true",
        help="Only retry retryable entries from logs/failed.jsonl",
    )
    p.add_argument("--concurrency", type=int, default=0, help="Override MAX_CONCURRENT")
    p.add_argument(
        "--base-model",
        default=None,
        help="CivitAI base model filter (default: env BASE_MODEL or Anima)",
    )
    p.add_argument(
        "--type",
        dest="model_type",
        default=None,
        choices=TYPE_CHOICES,
        help="CivitAI model type (default: env MODEL_TYPE or LORA)",
    )
    p.add_argument(
        "--sort",
        default=None,
        choices=SORT_CHOICES,
        help="Listing sort order (default: env SORT or Highest Rated)",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output directory (default: env LORA_DIR or ./downloads/Lora)",
    )
    p.add_argument(
        "--nsfw",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Include NSFW results (default: env NSFW or true)",
    )
    p.add_argument(
        "--match-base-version",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Download newest version matching --base-model "
            "(default: env MATCH_BASE_VERSION or true)"
        ),
    )
    ctrl = p.add_mutually_exclusive_group()
    ctrl.add_argument(
        "--cancel",
        action="store_true",
        help="Request cooperative cancel of the active run (writes logs/cancel.request)",
    )
    ctrl.add_argument(
        "--pause",
        action="store_true",
        help="Request pause after in-flight work (writes logs/pause.request)",
    )
    ctrl.add_argument(
        "--resume",
        action="store_true",
        help="Resume a paused run (clears logs/pause.request)",
    )
    ctrl.add_argument(
        "--status",
        action="store_true",
        help="Print logs/job.json status (use --json for machine output)",
    )
    ctrl.add_argument(
        "--heal",
        action="store_true",
        help="Consolidate library: repair sidecars, fix bad/missing weights",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="With --status, print JSON instead of a human summary",
    )
    p.add_argument(
        "--keep-partials",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "On start, also keep preview download temps "
            "(default: env KEEP_PARTIALS or false). "
            "Weight *.safetensors.partial are always kept for Range resume."
        ),
    )
    p.add_argument(
        "--resume-partials",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "HTTP Range-resume weight *.safetensors.partial files "
            "(default: env RESUME_PARTIALS or true). "
            "Use --no-resume-partials to force a full re-download."
        ),
    )
    p.add_argument(
        "--skip-verify",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Skip post-download BLAKE3 verify "
            "(default: env SKIP_VERIFY or false — verify on)"
        ),
    )
    p.add_argument(
        "--use-listing-cache",
        action="store_true",
        help="Opt in: reuse complete listing cache or build one while listing",
    )
    p.add_argument(
        "--refresh-listing",
        action="store_true",
        help="Force fresh listing from API (with --use-listing-cache, rewrite cache)",
    )
    p.add_argument(
        "--disk-floor-gib",
        type=float,
        default=None,
        help="Abort if free disk on out dir is below this many GiB (default: env DISK_FLOOR_GIB or 2; 0=disable)",
    )
    p.add_argument(
        "--purge-orphans",
        action="store_true",
        help="With --heal, delete .cm-info/preview that have no matching weight",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    # Prefer config next to where the user runs the tool (repo clone / working dir)
    root = Path.cwd()
    load_dotenv(root / ".env")
    load_dotenv()  # also allow exported env vars / parent .env
    args = build_parser().parse_args(argv)

    logs_dir = root / "logs"
    if args.status:
        return print_status_cli(logs_dir, logs_dir / "job.json", as_json=args.json)
    if args.cancel:
        return request_cancel_cli(logs_dir, logs_dir / "job.json")
    if args.pause:
        return request_pause_cli(logs_dir, logs_dir / "job.json")
    if args.resume:
        return request_resume_cli(logs_dir, logs_dir / "job.json")

    api_key = os.environ.get("CIVITAI_API_KEY", "").strip()
    logger = RunLogger(logs_dir)
    if not api_key:
        logger.log("ERROR: CIVITAI_API_KEY missing — copy .env.example to .env and set your key")
        return 2

    base_url = os.environ.get("CIVITAI_BASE_URL", "https://civitai.red").rstrip("/")
    base_model = args.base_model or os.environ.get("BASE_MODEL", "Anima")
    model_type = args.model_type or os.environ.get("MODEL_TYPE", "LORA")
    sort = args.sort or os.environ.get("SORT", "Highest Rated")
    out_dir = Path(args.out or os.environ.get("LORA_DIR", "./downloads/Lora")).expanduser()
    if not out_dir.is_absolute():
        out_dir = (root / out_dir).resolve()
    concurrency = args.concurrency or int(os.environ.get("MAX_CONCURRENT", "2"))
    nsfw = args.nsfw if args.nsfw is not None else _env_bool("NSFW", True)
    match_base = (
        args.match_base_version
        if args.match_base_version is not None
        else _env_bool("MATCH_BASE_VERSION", True)
    )
    keep_partials = (
        args.keep_partials
        if args.keep_partials is not None
        else _env_bool("KEEP_PARTIALS", False)
    )
    resume_partials = (
        args.resume_partials
        if args.resume_partials is not None
        else _env_bool("RESUME_PARTIALS", True)
    )
    skip_verify = (
        args.skip_verify
        if args.skip_verify is not None
        else _env_bool("SKIP_VERIFY", False)
    )
    if args.disk_floor_gib is not None:
        disk_floor_gib = float(args.disk_floor_gib)
    else:
        try:
            disk_floor_gib = float(os.environ.get("DISK_FLOOR_GIB", "2"))
        except ValueError:
            disk_floor_gib = 2.0

    client = CivitClient(base_url, api_key)
    if args.heal:
        return run_heal(
            client=client,
            out_dir=out_dir,
            logger=logger,
            dry_run=args.dry_run,
            purge_orphans=bool(args.purge_orphans),
            keep_partials=keep_partials,
        )
    return run_batch(
        client=client,
        out_dir=out_dir,
        logger=logger,
        base_model=base_model,
        model_type=model_type,
        sort=sort,
        nsfw=nsfw,
        match_base_version=match_base,
        concurrency=concurrency,
        dry_run=args.dry_run,
        limit=args.limit,
        retry_failed=args.retry_failed,
        keep_partials=keep_partials,
        resume=resume_partials,
        skip_verify=skip_verify,
        use_listing_cache=bool(args.use_listing_cache),
        refresh_listing=bool(args.refresh_listing),
        disk_floor_gib=disk_floor_gib,
    )


if __name__ == "__main__":
    raise SystemExit(main())
