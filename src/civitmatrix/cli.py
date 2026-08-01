from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from civitmatrix import __version__
from civitmatrix.cancel_control import request_cancel_cli
from civitmatrix.client import CivitClient
from civitmatrix.downloader import run_batch, run_heal
from civitmatrix.fix_swarm_architecture import fix_swarm_architecture
from civitmatrix.strip_swarm_thumbnails import strip_swarm_thumbnails
from civitmatrix.logging_io import RunLogger
from civitmatrix.pause_control import request_pause_cli, request_resume_cli
from civitmatrix.status_control import print_status_cli

# Public /api/v1/models sort enum (site Meilisearch labels like Relevancy / Most Buzz differ).
SORT_CHOICES = [
    "Highest Rated",
    "Most Downloaded",
    "Most Liked",
    "Most Discussed",
    "Most Collected",
    "Most Images",
    "Newest",
    "Oldest",
]

TYPE_CHOICES = [
    "All",
    "AestheticGradient",
    "Checkpoint",
    "CLIP",
    "CLIPVision",
    "Controlnet",
    "Detection",
    "DoRA",
    "Hypernetwork",
    "LORA",
    "LoCon",  # LyCORIS in civit.red UI
    "LLM",  # VLM in civit.red UI
    "MotionModule",
    "Other",
    "Poses",
    "TextEncoder",
    "TextualInversion",  # Embedding in civit.red UI
    "UNet",
    "Upscaler",
    "VAE",
    "VisionLanguage",
    "Wildcards",
    "Workflows",
]

CHECKPOINT_TYPE_CHOICES = [
    "All",
    "Merge",
    "Trained",
]

CATEGORY_CHOICES = [
    "All",
    "Action",
    "Animal",
    "Assets",
    "Background",
    "Base Model",
    "Buildings",
    "Celebrity",
    "Character",
    "Clothing",
    "Concept",
    "Objects",
    "Poses",
    "Style",
    "Tool",
    "Vehicle",
]

FORMAT_CHOICES = [
    "All",
    "SafeTensor",
    "PickleTensor",
    "Pt",
    "GGUF",
    "ONNX",
    "Core ML",
    "Diffusers",
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
        help="CivitAI base model filter (default: env BASE_MODEL or Anima; All = no filter)",
    )
    p.add_argument(
        "--type",
        dest="model_type",
        default=None,
        choices=TYPE_CHOICES,
        help="CivitAI model type (default: env MODEL_TYPE or LORA; All = no filter)",
    )
    p.add_argument(
        "--sort",
        default=None,
        choices=SORT_CHOICES,
        help="Listing sort order (default: env SORT or Highest Rated)",
    )
    p.add_argument(
        "--checkpoint-type",
        default=None,
        choices=CHECKPOINT_TYPE_CHOICES,
        help="Checkpoint type filter: Merge / Trained / All (no filter)",
    )
    p.add_argument(
        "--updated-from",
        default="",
        help="Inclusive start date (YYYY-MM-DD) for last updated filter",
    )
    p.add_argument(
        "--updated-to",
        default="",
        help="Inclusive end date (YYYY-MM-DD) for last updated filter",
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
    ctrl.add_argument(
        "--sm-parity",
        action="store_true",
        help="Scan out dir for SM sidecar parity issues (exit 1 if any)",
    )
    ctrl.add_argument(
        "--import-sm-manifest",
        action="store_true",
        help="Append existing .cm-info.json installs into logs/manifest.jsonl",
    )
    ctrl.add_argument(
        "--strip-swarm-thumbnails",
        action="store_true",
        help="One-shot: remove modelspec.thumbnail from existing *.swarm.json under out dir",
    )
    ctrl.add_argument(
        "--fix-swarm-architecture",
        action="store_true",
        help=(
            "One-shot: set modelspec.architecture on *.swarm.json from local "
            ".cm-info.json BaseModel/ModelType (no API, never touches weights)"
        ),
    )
    ctrl.add_argument(
        "--categorize",
        action="store_true",
        help=(
            "Plan moves into characters/ styles/ concepts/ clothes/ uncategorized/ "
            "(dry-run by default; pass --apply to write)"
        ),
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="With --categorize: perform moves (default is dry-run plan only)",
    )
    p.add_argument(
        "--write-swarm",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Write SwarmUI *.swarm.json beside weights "
            "(default: env WRITE_SWARM or false)"
        ),
    )
    p.add_argument(
        "--refresh-sidecars",
        action="store_true",
        help=(
            "With --heal: re-fetch API metadata and rewrite .cm-info.json "
            "(and .swarm.json if --write-swarm) for complete installs"
        ),
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
        "--keep-old-versions",
        action="store_true",
        help="Do not delete older local versions of the same model when a newer one is kept",
    )
    p.add_argument(
        "--update-only",
        action="store_true",
        help=(
            "Only download newer versions of already-installed ModelIds "
            "(skip not installed / up-to-date)"
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
        "--download-rate-limit",
        type=float,
        default=None,
        metavar="MIB_S",
        help=(
            "Global download cap in MiB/s shared across workers "
            "(default: env DOWNLOAD_RATE_LIMIT_MBS or 0=unlimited)"
        ),
    )
    p.add_argument(
        "--purge-orphans",
        action="store_true",
        help="With --heal, delete .cm-info/preview that have no matching weight",
    )
    p.add_argument(
        "--ui",
        action="store_true",
        help="Open local Win95 batch UI (default when no other args)",
    )
    p.add_argument(
        "--cli",
        action="store_true",
        help="Force CLI batch mode (no UI)",
    )
    p.add_argument(
        "--job-manifest",
        type=str,
        default="",
        help="JSON job from UI (filters + selection)",
    )
    p.add_argument("--tag-include", type=str, default="", help="Comma tags include")
    p.add_argument("--tag-exclude", type=str, default="", help="Comma tags exclude")
    p.add_argument(
        "--category",
        type=str,
        default="All",
        choices=CATEGORY_CHOICES,
        help="Category filter (All = no filter)",
    )
    p.add_argument("--users", type=str, default="", help="Comma creator usernames")
    p.add_argument(
        "--users-deny",
        type=str,
        default="",
        help="Comma creator usernames to exclude",
    )
    p.add_argument(
        "--format",
        dest="file_format",
        type=str,
        default="All",
        choices=FORMAT_CHOICES,
        help="File format filter (All = no filter)",
    )
    p.add_argument(
        "--min-downloads",
        type=int,
        default=0,
        help="Minimum stats.downloadCount (0 = no floor)",
    )
    p.add_argument(
        "--min-likes",
        type=int,
        default=0,
        help="Minimum stats.thumbsUpCount / likeCount (0 = no floor)",
    )
    p.add_argument(
        "--base-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Require every modelVersion.baseModel to match --base-model (drop multi-base)",
    )
    p.add_argument(
        "--max-nsfw-level",
        type=int,
        default=None,
        metavar="N",
        help="Keep models with nsfwLevel <= N (omit = no cap; missing level fails closed)",
    )
    p.add_argument(
        "--filter-preset",
        type=str,
        default="",
        metavar="NAME",
        help="Load filters from logs/filter-presets/NAME.json",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    import sys

    # Prefer config next to where the user runs the tool (repo clone / working dir)
    root = Path.cwd()
    load_dotenv(root / ".env")
    load_dotenv()  # also allow exported env vars / parent .env

    raw = list(argv) if argv is not None else list(sys.argv[1:])
    want_ui = False
    if not raw or raw == ["--ui"] or (raw and raw[0] == "--ui"):
        want_ui = True
    if raw and raw[0] == "--cli":
        raw = raw[1:]
        want_ui = False
    elif raw and any(
        a.startswith("-") and a not in {"--ui", "--no-open"} for a in raw
    ):
        want_ui = False
    if want_ui:
        from civitmatrix.ui.server import run_ui

        open_browser = "--no-open" not in raw
        return run_ui(open_browser=open_browser)

    args = build_parser().parse_args(raw)

    logs_dir = root / "logs"
    if args.status:
        return print_status_cli(logs_dir, logs_dir / "job.json", as_json=args.json)
    if args.cancel:
        return request_cancel_cli(logs_dir, logs_dir / "job.json")
    if args.pause:
        return request_pause_cli(logs_dir, logs_dir / "job.json")
    if args.resume:
        return request_resume_cli(logs_dir, logs_dir / "job.json")
    if args.strip_swarm_thumbnails:
        from civitmatrix.directories_config import load_directories, path_for_type

        model_type = args.model_type or os.environ.get("MODEL_TYPE", "LORA")
        if args.out:
            out_dir = Path(args.out).expanduser()
        elif os.environ.get("LORA_DIR"):
            out_dir = Path(os.environ["LORA_DIR"]).expanduser()
        else:
            dirs_cfg = load_directories(logs_dir / "directories.json")
            out_dir = path_for_type(dirs_cfg, model_type)
        if not out_dir.is_absolute():
            out_dir = (root / out_dir).resolve()
        counts = strip_swarm_thumbnails(out_dir, dry_run=args.dry_run)
        print(f"strip-swarm-thumbnails: {counts}")
        return 0

    if args.fix_swarm_architecture:
        from civitmatrix.directories_config import load_directories, path_for_type

        model_type = args.model_type or os.environ.get("MODEL_TYPE", "LORA")
        if args.out:
            out_dir = Path(args.out).expanduser()
        elif os.environ.get("LORA_DIR"):
            out_dir = Path(os.environ["LORA_DIR"]).expanduser()
        else:
            dirs_cfg = load_directories(logs_dir / "directories.json")
            out_dir = path_for_type(dirs_cfg, model_type)
        if not out_dir.is_absolute():
            out_dir = (root / out_dir).resolve()
        counts = fix_swarm_architecture(out_dir, dry_run=args.dry_run)
        print(f"fix-swarm-architecture: {counts}")
        return 0

    if args.categorize:
        from civitmatrix.categorize import apply_categorize, plan_categorize
        from civitmatrix.directories_config import load_directories, path_for_type

        model_type = args.model_type or os.environ.get("MODEL_TYPE", "LORA")
        if args.out:
            out_dir = Path(args.out).expanduser()
        elif os.environ.get("LORA_DIR"):
            out_dir = Path(os.environ["LORA_DIR"]).expanduser()
        else:
            dirs_cfg = load_directories(logs_dir / "directories.json")
            out_dir = path_for_type(dirs_cfg, model_type)
        if not out_dir.is_absolute():
            out_dir = (root / out_dir).resolve()
        # --categorize is dry-run unless --apply; --dry-run without --apply stays plan-only
        dry_run = not bool(args.apply)
        if args.dry_run and args.apply:
            print("ERROR: --dry-run and --apply are mutually exclusive with --categorize")
            return 2
        plan = plan_categorize(out_dir)
        by_bucket: dict[str, int] = {}
        for entry in plan:
            b = str(entry.get("toDir") or "?")
            by_bucket[b] = by_bucket.get(b, 0) + 1
        print(
            f"categorize plan: {len(plan)} move(s)"
            + (f" {by_bucket}" if by_bucket else "")
            + (" (dry-run)" if dry_run else " (--apply)")
        )
        counts = apply_categorize(out_dir, plan, dry_run=dry_run)
        print(f"categorize: {counts}")
        return 0 if counts.get("errors", 0) == 0 else 1

    def _resolve_out_dir_early() -> Path:
        from civitmatrix.directories_config import load_directories, path_for_type

        mt = args.model_type or os.environ.get("MODEL_TYPE", "LORA")
        if args.out:
            od = Path(args.out).expanduser()
        elif os.environ.get("LORA_DIR"):
            od = Path(os.environ["LORA_DIR"]).expanduser()
        else:
            dirs_cfg = load_directories(logs_dir / "directories.json")
            od = path_for_type(dirs_cfg, mt)
        if not od.is_absolute():
            od = (root / od).resolve()
        return od

    if args.sm_parity:
        from civitmatrix.sm_parity import check_sm_parity, format_parity_summary

        report = check_sm_parity(_resolve_out_dir_early())
        print(format_parity_summary(report))
        return 0 if report.ok else 1

    if args.import_sm_manifest:
        from civitmatrix.sm_parity import import_sm_manifest

        counts = import_sm_manifest(_resolve_out_dir_early(), logs_dir / "manifest.jsonl")
        print(f"import-sm-manifest: {counts}")
        return 0

    api_key = os.environ.get("CIVITAI_API_KEY", "").strip()
    logger = RunLogger(logs_dir)
    if not api_key:
        logger.log("ERROR: CIVITAI_API_KEY missing — copy .env.example to .env and set your key")
        return 2

    base_url = os.environ.get("CIVITAI_BASE_URL", "https://civitai.red").rstrip("/")
    base_model = args.base_model or os.environ.get("BASE_MODEL", "Anima")
    model_type = args.model_type or os.environ.get("MODEL_TYPE", "LORA")
    sort = args.sort or os.environ.get("SORT", "Highest Rated")
    checkpoint_type = args.checkpoint_type or "All"
    updated_from = (args.updated_from or "").strip()
    updated_to = (args.updated_to or "").strip()

    from civitmatrix.directories_config import load_directories, path_for_type
    from civitmatrix.filter_presets import FilterPresetError, load_filter_preset
    from civitmatrix.model_filters import is_all_filter, parse_csv_list

    preset: dict = {}
    if (args.filter_preset or "").strip():
        try:
            preset = load_filter_preset(logs_dir, args.filter_preset.strip())
        except FilterPresetError as e:
            logger.log(f"ERROR: {e}")
            return 2

    job_manifest: dict = {}
    selection_map: dict[int, list] = {}
    if args.job_manifest:
        import json as _json

        job_manifest = _json.loads(Path(args.job_manifest).read_text(encoding="utf-8"))
        base_model = str(job_manifest.get("baseModel") or base_model)
        model_type = str(job_manifest.get("type") or model_type)
        sort = str(job_manifest.get("sort") or sort)
        checkpoint_type = str(job_manifest.get("checkpointType") or checkpoint_type)
        updated_from = str(job_manifest.get("updatedFrom") or updated_from).strip()
        updated_to = str(job_manifest.get("updatedTo") or updated_to).strip()
        for row in job_manifest.get("selection") or []:
            try:
                selection_map[int(row["modelId"])] = list(row.get("versionIds") or ["latest"])
            except (KeyError, TypeError, ValueError):
                continue

    # Resolve out dir: --out > LORA_DIR > manifest outDir > directories.json > ./downloads/Lora
    if args.out:
        out_dir = Path(args.out).expanduser()
    elif os.environ.get("LORA_DIR"):
        out_dir = Path(os.environ["LORA_DIR"]).expanduser()
    elif job_manifest.get("outDir"):
        out_dir = Path(str(job_manifest["outDir"])).expanduser()
    else:
        dirs_cfg = load_directories(logs_dir / "directories.json")
        out_dir = path_for_type(dirs_cfg, model_type)

    def _preset_list(key: str) -> list[str]:
        raw = preset.get(key)
        if isinstance(raw, list):
            return [str(x) for x in raw if str(x).strip()]
        return parse_csv_list(raw if isinstance(raw, str) else None)

    tag_include = (
        parse_csv_list(args.tag_include)
        or list(job_manifest.get("tagInclude") or [])
        or _preset_list("tagInclude")
    )
    tag_exclude = (
        parse_csv_list(args.tag_exclude)
        or list(job_manifest.get("tagExclude") or [])
        or _preset_list("tagExclude")
    )
    category = args.category or job_manifest.get("category") or preset.get("category") or ""
    users = (
        parse_csv_list(args.users)
        or list(job_manifest.get("users") or [])
        or _preset_list("users")
    )
    users_deny = (
        parse_csv_list(args.users_deny)
        or list(job_manifest.get("usersDeny") or [])
        or _preset_list("usersDeny")
    )
    file_format = args.file_format or job_manifest.get("format") or preset.get("format") or "All"

    min_downloads = int(args.min_downloads or 0)
    if not min_downloads and "minDownloads" in (job_manifest or {}):
        try:
            min_downloads = int(job_manifest.get("minDownloads") or 0)
        except (TypeError, ValueError):
            min_downloads = 0
    if not min_downloads and preset.get("minDownloads") is not None:
        try:
            min_downloads = int(preset.get("minDownloads") or 0)
        except (TypeError, ValueError):
            min_downloads = 0

    min_likes = int(args.min_likes or 0)
    if not min_likes and "minLikes" in (job_manifest or {}):
        try:
            min_likes = int(job_manifest.get("minLikes") or 0)
        except (TypeError, ValueError):
            min_likes = 0
    if not min_likes and preset.get("minLikes") is not None:
        try:
            min_likes = int(preset.get("minLikes") or 0)
        except (TypeError, ValueError):
            min_likes = 0

    base_only = bool(args.base_only)
    if not base_only and "baseOnly" in job_manifest:
        base_only = bool(job_manifest.get("baseOnly"))
    if not base_only and preset.get("baseOnly") is not None:
        base_only = bool(preset.get("baseOnly"))

    max_nsfw_level = args.max_nsfw_level
    if max_nsfw_level is None and "maxNsfwLevel" in job_manifest:
        raw_lvl = job_manifest.get("maxNsfwLevel")
        if raw_lvl is not None and str(raw_lvl).strip() != "":
            try:
                max_nsfw_level = int(raw_lvl)
            except (TypeError, ValueError):
                max_nsfw_level = None
    if max_nsfw_level is None and preset.get("maxNsfwLevel") is not None:
        try:
            max_nsfw_level = int(preset["maxNsfwLevel"])
        except (TypeError, ValueError):
            max_nsfw_level = None

    if not updated_from and preset.get("updatedFrom"):
        updated_from = str(preset.get("updatedFrom") or "").strip()
    if not updated_to and preset.get("updatedTo"):
        updated_to = str(preset.get("updatedTo") or "").strip()
    if not args.checkpoint_type and not job_manifest.get("checkpointType") and preset.get(
        "checkpointType"
    ):
        checkpoint_type = str(preset.get("checkpointType") or checkpoint_type)

    if not out_dir.is_absolute():
        out_dir = (root / out_dir).resolve()
    concurrency = args.concurrency or int(os.environ.get("MAX_CONCURRENT", "2"))
    nsfw = args.nsfw if args.nsfw is not None else _env_bool("NSFW", True)
    if "nsfw" in job_manifest:
        nsfw = bool(job_manifest["nsfw"])
    match_base = (
        args.match_base_version
        if args.match_base_version is not None
        else _env_bool("MATCH_BASE_VERSION", True)
    )
    if is_all_filter(base_model):
        match_base = False

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
    write_swarm = (
        args.write_swarm
        if args.write_swarm is not None
        else _env_bool("WRITE_SWARM", False)
    )
    if args.disk_floor_gib is not None:
        disk_floor_gib = float(args.disk_floor_gib)
    else:
        try:
            disk_floor_gib = float(os.environ.get("DISK_FLOOR_GIB", "2"))
        except ValueError:
            disk_floor_gib = 2.0

    from civitmatrix.rate_limit import mib_per_sec_to_bytes, parse_rate_limit_mib

    if args.download_rate_limit is not None:
        rate_mib = float(args.download_rate_limit)
    else:
        rate_mib = parse_rate_limit_mib(os.environ.get("DOWNLOAD_RATE_LIMIT_MBS"), 0.0)
    rate_bps = mib_per_sec_to_bytes(rate_mib)
    client = CivitClient(base_url, api_key, rate_limit_bytes_per_sec=rate_bps)
    if rate_bps > 0:
        logger.log(f"Download rate limit: {rate_mib:g} MiB/s (global, shared)")
    else:
        logger.log("Download rate limit: unlimited")

    if args.heal:
        return run_heal(
            client=client,
            out_dir=out_dir,
            logger=logger,
            dry_run=args.dry_run,
            purge_orphans=bool(args.purge_orphans),
            keep_partials=keep_partials,
            refresh_sidecars=bool(args.refresh_sidecars),
            write_swarm=write_swarm,
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
        keep_old_versions=bool(args.keep_old_versions),
        tag_include=tag_include,
        tag_exclude=tag_exclude,
        category=category,
        users=users,
        users_deny=users_deny,
        file_format=file_format,
        checkpoint_type=checkpoint_type,
        updated_from=updated_from,
        updated_to=updated_to,
        min_downloads=min_downloads,
        min_likes=min_likes,
        base_only=base_only,
        max_nsfw_level=max_nsfw_level,
        selection_map=selection_map,
        write_swarm=write_swarm,
        update_only=bool(args.update_only),
    )


if __name__ == "__main__":
    raise SystemExit(main())
