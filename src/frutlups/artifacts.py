"""Artifact path conventions for the frutlups project template.

Path derivation is driven by a :class:`~frutlups.layout.LayoutProfile` when one is
supplied. When no profile is given, :class:`TemplatePaths` falls back to the
legacy hardcoded layout, so existing direct constructions keep their historical
behavior unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from frutlups.layout import LayoutProfile

REQUIRED_DIRECTORIES = (
    "00_brief",
    "03_experiments",
    "05_governance",
    "06_infra",
    "08_pkg",
    "prompts",
)

OPTIONAL_DIRECTORIES = (
    "01_data",
    "02_analysis",
    "04_delivery",
    "07_app",
    "09_ops",
    "90_legacy_review",
    "docs",
)


@dataclass(frozen=True)
class PromptDirectories:
    """Known prompt directories inside a project.

    ``coding_dir`` / ``review_dir`` may be set explicitly (absolute paths derived
    from a layout profile). When omitted, the legacy ``for_coding_agent`` /
    ``for_review_agent`` subfolders of ``root`` are used.
    """

    root: Path
    coding_dir: Path | None = None
    review_dir: Path | None = None

    @property
    def coding(self) -> Path:
        return self.coding_dir if self.coding_dir is not None else self.root / "for_coding_agent"

    @property
    def review(self) -> Path:
        return self.review_dir if self.review_dir is not None else self.root / "for_review_agent"


def _rel(root: Path, rel: str) -> Path:
    return root / PurePosixPath(rel)


@dataclass(frozen=True)
class TemplatePaths:
    """Resolved paths for the artifact-first template.

    When ``profile`` is provided, directory names, roadmap globs, prompt
    directories, the reviews directory, and the required-directory set are taken
    from it. When ``profile`` is ``None``, the legacy hardcoded layout is used.
    """

    root: Path
    profile: LayoutProfile | None = None

    @property
    def brief(self) -> Path:
        return self.root / "00_brief"

    @property
    def experiments(self) -> Path:
        if self.profile is not None:
            return _rel(self.root, self.profile.roadmap_dir)
        return self.root / "03_experiments"

    @property
    def governance(self) -> Path:
        return self.root / "05_governance"

    @property
    def infra(self) -> Path:
        return self.root / "06_infra"

    @property
    def package_workspace(self) -> Path:
        return self.root / "08_pkg"

    @property
    def prompts(self) -> PromptDirectories:
        if self.profile is not None:
            return PromptDirectories(
                self.root / "prompts",
                coding_dir=_rel(self.root, self.profile.coding_prompt_dir),
                review_dir=_rel(self.root, self.profile.review_prompt_dir),
            )
        return PromptDirectories(self.root / "prompts")

    @property
    def default_memory_root(self) -> Path:
        # M011-S01: profile-aware. When a layout profile is selected, its
        # ``llloom_memory_root`` (v2/template-v3 -> repo-root ``llloom_memory``;
        # legacy -> ``07_app/llloom_memory``) supplies the root so this no longer
        # contradicts a selected v2/template-v3 profile. With no profile (legacy
        # direct construction) the historical location is preserved unchanged.
        #
        # Prompt 044 Gate D: a selected profile carrying the explicit unsafe-root
        # disable sentinel (``llloom_memory_root == ""``) must fail closed rather
        # than return the historical legacy root, the project root, or any other
        # usable fallback. The property has no live product consumer, so an
        # invalid interpretation is surfaced as a bounded, non-echoing
        # ``ValueError`` that preserves the property and every valid return type.
        if self.profile is not None:
            rel = self.profile.llloom_memory_root
            if rel == "":
                raise ValueError(
                    "selected layout profile disabled the llloom memory root; "
                    "no default memory root is available"
                )
            return _rel(self.root, rel)
        return self.root / "07_app" / "llloom_memory"

    @property
    def _active_roadmap_glob(self) -> str:
        if self.profile is not None:
            return self.profile.active_roadmap_glob
        return "active_roadmap*.md"

    @property
    def _detailed_roadmap_glob(self) -> str:
        return (
            self.profile.development_roadmap_glob
            if self.profile is not None
            else "development_roadmap*.md"
        )

    @property
    def active_roadmaps(self) -> tuple[Path, ...]:
        if not self.experiments.exists():
            return ()
        return tuple(sorted(self.experiments.glob(self._active_roadmap_glob)))

    @property
    def detailed_roadmaps(self) -> tuple[Path, ...]:
        if not self.experiments.exists():
            return ()
        return tuple(sorted(self.experiments.glob(self._detailed_roadmap_glob)))

    @property
    def review_reports(self) -> Path:
        if self.profile is not None:
            return _rel(self.root, self.profile.reviews_dir)
        return self.governance / "reviews"

    @property
    def required_directories(self) -> tuple[str, ...]:
        if self.profile is not None:
            return self.profile.required_directories
        return REQUIRED_DIRECTORIES

    @property
    def required_missing(self) -> tuple[str, ...]:
        return tuple(name for name in self.required_directories if not (self.root / name).is_dir())
