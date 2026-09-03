"""Versioned prompt templates with content hashes."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from astra.core.ids import content_hash


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    version: str
    body: str
    content_hash: str


def load_prompt(*parts: str) -> PromptTemplate:
    resource = resources.files("astra.prompts")
    for part in parts[:-1]:
        resource = resource.joinpath(part)
    filename = f"{parts[-1]}.txt"
    body = resource.joinpath(filename).read_text(encoding="utf-8")
    name = "/".join(parts)
    version = parts[-1]
    return PromptTemplate(
        name=name,
        version=version,
        body=body,
        content_hash=content_hash({"name": name, "version": version, "body": body}),
    )
