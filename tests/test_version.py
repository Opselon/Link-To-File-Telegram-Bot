"""Release plumbing sanity: the version file and packaging must stay in sync."""

import re
from pathlib import Path

from linktofile.infrastructure.system.version import VERSION

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION
    assert tuple(int(p) for p in VERSION.split(".")) >= (2, 0, 0)


def test_readme_mentions_the_package_image():
    readme = (ROOT / "README.md").read_text()
    assert "ghcr.io/opselon/link-to-file-telegram-bot" in readme


def test_release_workflow_publishes_image_and_archives():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "docker/build-push-action" in workflow
    assert "gh release create" in workflow
    assert "sha256sum" in workflow
    assert "linux/arm64" in workflow


def test_dockerfile_runs_as_non_root_with_healthcheck():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER bot" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "ffmpeg" in dockerfile


def test_compose_declares_restart_and_volume():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "restart: unless-stopped" in compose
    assert "bot-data:/data" in compose
