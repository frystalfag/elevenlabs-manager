from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from loguru import logger

from app.utils.paths import APP_NAME, APP_VERSION


@dataclass
class UpdateInfo:
    version: str
    url: str
    notes: str


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (0,)


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return _parse(remote) > _parse(local)


def check_for_update(repo: str, timeout: float = 10.0) -> Optional[UpdateInfo]:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        return None
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.info("Update check: HTTP {}", resp.status_code)
            return None
        data = resp.json()
        tag = str(data.get("tag_name", ""))
        if tag and is_newer(tag):
            return UpdateInfo(version=tag, url=str(data.get("html_url", "")), notes=str(data.get("body", ""))[:2000])
    except (requests.RequestException, ValueError) as e:
        logger.info("Update check failed: {}", e)
    return None
