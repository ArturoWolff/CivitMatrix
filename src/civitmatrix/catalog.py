"""Shared filtered catalog iteration for UI populate and batch runs."""

from __future__ import annotations

from typing import Any, Iterator

from civitmatrix.client import CivitClient
from civitmatrix.model_filters import model_passes_filters, parse_csv_list


def iter_filtered_models(
    client: CivitClient,
    *,
    base_model: str,
    model_type: str,
    nsfw: bool = True,
    sort: str = "Highest Rated",
    tag_include: list[str] | str | None = None,
    tag_exclude: list[str] | str | None = None,
    category: str | None = None,
    users: list[str] | str | None = None,
    file_format: str | None = None,
    username: str | None = None,
    on_page: Any = None,
) -> Iterator[dict[str, Any]]:
    """Yield models from the API that pass local filter dimensions."""
    inc = parse_csv_list(tag_include) if not isinstance(tag_include, list) else list(tag_include or [])
    exc = parse_csv_list(tag_exclude) if not isinstance(tag_exclude, list) else list(tag_exclude or [])
    user_list = parse_csv_list(users) if not isinstance(users, list) else list(users or [])
    cat = category if category and str(category).lower() != "any" else None
    fmt = (
        file_format
        if file_format and str(file_format).lower() not in {"", "any"}
        else None
    )
    api_user = username
    local_users: list[str] | None = None
    if api_user is None and len(user_list) == 1:
        api_user = user_list[0]
    elif len(user_list) > 1:
        local_users = user_list

    for model in client.iter_models(
        base_model=base_model,
        model_type=model_type,
        nsfw=nsfw,
        sort=sort,
        username=api_user,
        on_page=on_page,
    ):
        if not model_passes_filters(
            model,
            tag_include=inc,
            tag_exclude=exc,
            category=cat,
            users=local_users,
            file_format=fmt,
        ):
            continue
        yield model
