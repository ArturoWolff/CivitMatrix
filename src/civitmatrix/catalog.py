"""Shared filtered catalog iteration for UI populate and batch runs."""

from __future__ import annotations

from typing import Any, Iterator

from civitmatrix.client import CivitClient
from civitmatrix.model_filters import is_all_filter, model_passes_filters, parse_csv_list


def iter_filtered_models(
    client: CivitClient,
    *,
    base_model: str | None = None,
    model_type: str | None = None,
    nsfw: bool = True,
    sort: str = "Highest Rated",
    tag_include: list[str] | str | None = None,
    tag_exclude: list[str] | str | None = None,
    category: str | None = None,
    users: list[str] | str | None = None,
    users_deny: list[str] | str | None = None,
    file_format: str | None = None,
    checkpoint_type: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    min_downloads: int = 0,
    min_likes: int = 0,
    base_only: bool = False,
    max_nsfw_level: int | None = None,
    username: str | None = None,
    on_page: Any = None,
) -> Iterator[dict[str, Any]]:
    """Yield models from the API that pass local filter dimensions."""
    inc = parse_csv_list(tag_include) if not isinstance(tag_include, list) else list(tag_include or [])
    exc = parse_csv_list(tag_exclude) if not isinstance(tag_exclude, list) else list(tag_exclude or [])
    user_list = parse_csv_list(users) if not isinstance(users, list) else list(users or [])
    deny_list = (
        parse_csv_list(users_deny) if not isinstance(users_deny, list) else list(users_deny or [])
    )
    cat = None if is_all_filter(category) else category
    fmt = None if is_all_filter(file_format) else file_format
    ckpt = None if is_all_filter(checkpoint_type) else checkpoint_type
    api_user = username
    local_users: list[str] | None = None
    if api_user is None and len(user_list) == 1:
        api_user = user_list[0].lstrip("@")
    elif len(user_list) > 1:
        local_users = [u.lstrip("@") for u in user_list]

    for model in client.iter_models(
        base_model=base_model,
        model_type=model_type,
        nsfw=nsfw,
        sort=sort,
        username=api_user,
        checkpoint_type=ckpt,
        on_page=on_page,
    ):
        if not model_passes_filters(
            model,
            tag_include=inc,
            tag_exclude=exc,
            category=cat,
            users=local_users,
            users_deny=deny_list,
            file_format=fmt,
            updated_from=updated_from,
            updated_to=updated_to,
            min_downloads=min_downloads,
            min_likes=min_likes,
            base_model=base_model,
            base_only=base_only,
            max_nsfw_level=max_nsfw_level,
        ):
            continue
        yield model
