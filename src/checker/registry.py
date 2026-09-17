"""Target registry: who we scan, what they declared, what they are exempt from."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class CrawlConfig(BaseModel):
    max_depth: int = 1
    max_pages: int = 12
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=lambda: [r"\.pdf$", r"\.zip$", r"/search"])
    respect_robots: bool = True
    check_link_liveness: bool = True


class Exemption(BaseModel):
    rule_id: str
    reason: str
    expires: date | None = None
    granted_by: str | None = None

    def active_on(self, when: date) -> bool:
        return self.expires is None or when <= self.expires


class Target(BaseModel):
    id: str
    name: str
    type: str = "node_landing_page"
    landing_page: str
    catalogue_api: str | None = None
    contact: str | None = None
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    declarations: dict[str, Any] = Field(default_factory=dict)
    exemptions: list[Exemption] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not v or " " in v:
            raise ValueError("target id must be non-empty and contain no spaces")
        return v

    def exemption_for(self, rule_id: str, when: date | None = None) -> Exemption | None:
        when = when or date.today()
        for ex in self.exemptions:
            if ex.rule_id == rule_id and ex.active_on(when):
                return ex
        return None

    def declares(self, key: str) -> bool:
        return bool(self.declarations.get(key))


class TargetRegistry(BaseModel):
    schema_version: int = 1
    targets: list[Target] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> TargetRegistry:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def enabled_targets(self) -> list[Target]:
        return [t for t in self.targets if t.enabled]

    def get(self, target_id: str) -> Target | None:
        return next((t for t in self.targets if t.id == target_id), None)
