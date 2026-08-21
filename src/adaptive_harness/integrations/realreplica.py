"""Strict offline adapter for the frozen RealReplicaBench MiniBench16 split."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adaptive_harness.task_contract import (
    ContractBuilder,
    CriterionKind,
    RuleBasedTaskContractBuilder,
)

MINIBENCH_TASK_COUNT = 16


@dataclass(frozen=True)
class MiniBenchTask:
    task_id: str
    category: str
    difficulty_raw: str
    difficulty_band: str
    capability: str
    block: int
    max_actions: int
    timeout_sec: int
    language: str
    prompt: str
    prompt_sha256: str
    smoke_reuse: bool
    public_observation_subjects: tuple[str, ...] = ()

    def identity_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "category": self.category,
            "difficulty_raw": self.difficulty_raw,
            "difficulty_band": self.difficulty_band,
            "capability": self.capability,
            "block": self.block,
            "max_actions": self.max_actions,
            "timeout_sec": self.timeout_sec,
            "language": self.language,
            "prompt_sha256": self.prompt_sha256,
            "smoke_reuse": self.smoke_reuse,
            "public_observation_subjects": list(self.public_observation_subjects),
        }


@dataclass(frozen=True)
class MiniBenchDataset:
    dataset_id: str
    seed: int
    tasks: tuple[MiniBenchTask, ...]
    block_purposes: Mapping[int, str]
    fingerprint: str

    def counts(self) -> dict[str, dict[str, int] | int]:
        return {
            "total": len(self.tasks),
            "category": dict(sorted(Counter(task.category for task in self.tasks).items())),
            "difficulty_band": dict(
                sorted(Counter(task.difficulty_band for task in self.tasks).items())
            ),
            "capability": dict(sorted(Counter(task.capability for task in self.tasks).items())),
            "smoke_reused": sum(task.smoke_reuse for task in self.tasks),
        }


@dataclass(frozen=True)
class ContractCoverageRow:
    task_id: str
    block: int
    criterion_count: int
    criterion_kinds: tuple[str, ...]


@dataclass(frozen=True)
class ContractCoverageReport:
    rows: tuple[ContractCoverageRow, ...]

    @property
    def covered_count(self) -> int:
        return sum(row.criterion_count > 0 for row in self.rows)

    @property
    def total_count(self) -> int:
        return len(self.rows)

    @property
    def uncovered_task_ids(self) -> tuple[str, ...]:
        return tuple(row.task_id for row in self.rows if row.criterion_count == 0)


@dataclass(frozen=True)
class ProviderCoverageRow:
    task_id: str
    block: int
    enforced_criterion_count: int
    observe_only_criterion_count: int


@dataclass(frozen=True)
class ProviderCoverageReport:
    rows: tuple[ProviderCoverageRow, ...]

    @property
    def enforced_task_count(self) -> int:
        return sum(row.enforced_criterion_count > 0 for row in self.rows)

    @property
    def ready_blocks(self) -> tuple[int, ...]:
        blocks = sorted({row.block for row in self.rows})
        return tuple(
            block
            for block in blocks
            if all(
                row.enforced_criterion_count > 0
                for row in self.rows
                if row.block == block
            )
        )


@dataclass(frozen=True)
class VariantSpec:
    name: str
    profile_fingerprint: str
    model: str
    runtime_image: str
    seed: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "profile_fingerprint": self.profile_fingerprint,
            "model": self.model,
            "runtime_image": self.runtime_image,
            "seed": self.seed,
        }


@dataclass(frozen=True)
class PairedRunCell:
    task_id: str
    block: int
    variant: str
    pair_key: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "block": self.block,
            "variant": self.variant,
            "pair_key": self.pair_key,
        }


@dataclass(frozen=True)
class PairedRunManifest:
    dataset_fingerprint: str
    baseline: VariantSpec
    candidate: VariantSpec
    cells: tuple[PairedRunCell, ...]
    fingerprint: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "baseline": self.baseline.to_payload(),
            "candidate": self.candidate.to_payload(),
            "cells": [cell.to_payload() for cell in self.cells],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class HistoricalBaseline:
    task_id: str
    run_id: str
    started_at: str
    passed: bool
    capacity_score: float | None
    total_tokens: int | None
    tool_call_count: int | None
    elapsed_sec: float | None
    run_config_sha256: str


class RealReplicaMiniBenchAdapter:
    """Read public task inputs and evaluation summaries; never online verifier data."""

    def load(self, root: Path) -> MiniBenchDataset:
        root = root.resolve()
        collection = _read_json(root / "splits/minibench16.collection.json")
        selection = _read_json(root / "splits/minibench16.selection.json")
        blocks_document = _read_json(root / "splits/minibench16.blocks.json")
        task_ids = [str(item) for item in collection.get("task_ids") or ()]
        selected = selection.get("tasks") or []
        selected_ids = [str(item["task_id"]) for item in selected]
        block_rows = blocks_document.get("blocks") or []
        block_ids = [str(task_id) for block in block_rows for task_id in block.get("task_ids") or ()]

        if len(task_ids) != MINIBENCH_TASK_COUNT or len(set(task_ids)) != MINIBENCH_TASK_COUNT:
            raise ValueError("MiniBench must contain exactly 16 unique tasks")
        if task_ids != selected_ids or task_ids != block_ids:
            raise ValueError("collection, selection, and block ordering must match exactly")
        seed = int(selection["seed"])
        if int(blocks_document["seed"]) != seed:
            raise ValueError("selection and block seeds differ")
        expected_ids_hash = hashlib.sha256(("\n".join(task_ids) + "\n").encode()).hexdigest()
        if selection.get("task_ids_sha256") != expected_ids_hash:
            raise ValueError("frozen task id hash does not match")
        dev_ids = set(_read_json(root / "splits/dev.collection.json").get("task_ids") or ())
        heldout_ids = set(_read_json(root / "splits/heldout.collection.json").get("task_ids") or ())
        if not set(task_ids) <= dev_ids or set(task_ids) & heldout_ids:
            raise ValueError("MiniBench must remain Development-only")

        task_paths = self._task_paths(root / "datasets_domain_v1", set(task_ids))
        block_by_task = {
            str(task_id): int(block["block"])
            for block in block_rows
            for task_id in block.get("task_ids") or ()
        }
        selection_by_id = {str(item["task_id"]): item for item in selected}
        tasks: list[MiniBenchTask] = []
        for task_id in task_ids:
            task_path, task_document = task_paths[task_id]
            public_task = task_document.get("task") or {}
            public_environment = task_document.get("environment") or {}
            entrypoint = str(public_task["entrypoint"])
            prompt_path = (task_path.parent / entrypoint).resolve()
            if not prompt_path.is_relative_to(task_path.parent.resolve()):
                raise ValueError(f"task entrypoint escapes its task directory: {task_id}")
            prompt = prompt_path.read_text(encoding="utf-8")
            row = selection_by_id[task_id]
            category = task_path.relative_to(root / "datasets_domain_v1").parts[0]
            if category != row["category"]:
                raise ValueError(f"task category drift: {task_id}")
            tasks.append(
                MiniBenchTask(
                    task_id=task_id,
                    category=category,
                    difficulty_raw=str(row["difficulty_raw"]),
                    difficulty_band=str(row["difficulty_band"]),
                    capability=str(row["capability"]),
                    block=block_by_task[task_id],
                    max_actions=int(row["max_actions"]),
                    timeout_sec=int(row["timeout_sec"]),
                    language=str(public_task.get("language") or "unknown"),
                    prompt=prompt,
                    prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                    smoke_reuse=bool(row["smoke_reuse"]),
                    public_observation_subjects=_public_observation_subjects(
                        public_environment
                    ),
                )
            )
        identity = {
            "dataset_id": str(collection["collection_id"]),
            "seed": seed,
            "tasks": [task.identity_payload() for task in tasks],
        }
        fingerprint = _canonical_hash(identity)
        expected_counts = selection.get("counts") or {}
        dataset = MiniBenchDataset(
            str(collection["collection_id"]),
            seed,
            tuple(tasks),
            {int(block["block"]): str(block["purpose"]) for block in block_rows},
            fingerprint,
        )
        actual_counts = dataset.counts()
        for key in ("total", "category", "difficulty_band", "capability", "smoke_reused"):
            if actual_counts[key] != expected_counts.get(key):
                raise ValueError(f"selection count drift for {key}")
        return dataset

    def contract_coverage(
        self,
        dataset: MiniBenchDataset,
        builder: ContractBuilder | None = None,
    ) -> ContractCoverageReport:
        builder = builder or RuleBasedTaskContractBuilder()
        rows = []
        for task in dataset.tasks:
            contract = builder.build(task.task_id, task.prompt)
            rows.append(
                ContractCoverageRow(
                    task.task_id,
                    task.block,
                    len(contract.criteria),
                    tuple(criterion.kind.value for criterion in contract.criteria),
                )
            )
        return ContractCoverageReport(tuple(rows))

    def paired_manifest(
        self,
        dataset: MiniBenchDataset,
        baseline: VariantSpec,
        candidate: VariantSpec,
    ) -> PairedRunManifest:
        if baseline.name == candidate.name:
            raise ValueError("baseline and candidate names must differ")
        if baseline.profile_fingerprint == candidate.profile_fingerprint:
            raise ValueError("baseline and candidate profiles must differ")
        for field_name in ("model", "runtime_image", "seed"):
            if getattr(baseline, field_name) != getattr(candidate, field_name):
                raise ValueError(f"paired variants must use the same {field_name}")
        if baseline.seed != dataset.seed:
            raise ValueError("variant seed must match the frozen dataset")
        cells = []
        for task in dataset.tasks:
            pair_key = hashlib.sha256(
                f"{dataset.fingerprint}:{task.task_id}:{task.block}".encode()
            ).hexdigest()[:16]
            cells.extend(
                (
                    PairedRunCell(task.task_id, task.block, baseline.name, pair_key),
                    PairedRunCell(task.task_id, task.block, candidate.name, pair_key),
                )
            )
        payload = {
            "dataset_fingerprint": dataset.fingerprint,
            "baseline": baseline.to_payload(),
            "candidate": candidate.to_payload(),
            "cells": [cell.to_payload() for cell in cells],
        }
        return PairedRunManifest(
            dataset.fingerprint,
            baseline,
            candidate,
            tuple(cells),
            _canonical_hash(payload),
        )

    def provider_coverage(
        self,
        dataset: MiniBenchDataset,
        builder: ContractBuilder | None = None,
    ) -> ProviderCoverageReport:
        builder = builder or RuleBasedTaskContractBuilder()
        rows = []
        for task in dataset.tasks:
            contract = builder.build(task.task_id, task.prompt)
            enforced = 0
            for criterion in contract.criteria:
                if criterion.kind is CriterionKind.ARTIFACT_EXISTS:
                    enforced += 1
                elif (
                    criterion.kind is CriterionKind.EXACT_COUNT
                    and criterion.parameters.get("subject") == "files"
                ):
                    enforced += 1
                elif (
                    criterion.kind is CriterionKind.OBSERVATION_EQUALS
                    and criterion.parameters.get("subject")
                    in task.public_observation_subjects
                ):
                    enforced += 1
            rows.append(
                ProviderCoverageRow(
                    task.task_id,
                    task.block,
                    enforced,
                    len(contract.criteria) - enforced,
                )
            )
        return ProviderCoverageReport(tuple(rows))

    def historical_baselines(
        self,
        root: Path,
        dataset: MiniBenchDataset,
        baseline: VariantSpec,
    ) -> Mapping[str, HistoricalBaseline]:
        selected_ids = {task.task_id for task in dataset.tasks}
        candidates: dict[str, list[HistoricalBaseline]] = {task_id: [] for task_id in selected_ids}
        for summary_path in sorted((root / "runs").glob("*/summary.json")):
            try:
                summary = _read_json(summary_path)
            except (OSError, json.JSONDecodeError):
                continue
            experiment = summary.get("experiment") or {}
            if (
                summary.get("harness") != "deerflow"
                or summary.get("model_name") != baseline.model
                or summary.get("image") != baseline.runtime_image
                or experiment.get("variant") != baseline.name
                or int(experiment.get("seed", -1)) != baseline.seed
            ):
                continue
            for result in summary.get("results") or ():
                task_id = str(result.get("task_id") or "")
                if (
                    task_id not in selected_ids
                    or result.get("formal_result_eligible") is not True
                    or result.get("integrity_passed") is not True
                ):
                    continue
                usage = result.get("usage") or {}
                candidates[task_id].append(
                    HistoricalBaseline(
                        task_id=task_id,
                        run_id=str(summary["run_id"]),
                        started_at=str(summary.get("started_at") or ""),
                        passed=bool(result.get("passed")),
                        capacity_score=(
                            float(result["capacity_score"])
                            if result.get("capacity_score") is not None
                            else None
                        ),
                        total_tokens=(
                            int(usage["total_tokens"])
                            if usage.get("total_tokens") is not None
                            else None
                        ),
                        tool_call_count=(
                            int(result["tool_call_count"])
                            if result.get("tool_call_count") is not None
                            else None
                        ),
                        elapsed_sec=(
                            float(result["elapsed_sec"])
                            if result.get("elapsed_sec") is not None
                            else None
                        ),
                        run_config_sha256=str(summary.get("run_config_sha256") or ""),
                    )
                )
        return {
            task_id: max(rows, key=lambda row: (row.started_at, row.run_id))
            for task_id, rows in candidates.items()
            if rows
        }

    def _task_paths(
        self,
        datasets_dir: Path,
        selected_ids: set[str],
    ) -> Mapping[str, tuple[Path, Mapping[str, Any]]]:
        result: dict[str, tuple[Path, Mapping[str, Any]]] = {}
        for task_path in datasets_dir.rglob("task.toml"):
            document = tomllib.loads(task_path.read_text(encoding="utf-8"))
            task_id = str((document.get("task") or {}).get("id") or "")
            if task_id not in selected_ids:
                continue
            if task_id in result:
                raise ValueError(f"duplicate task metadata: {task_id}")
            result[task_id] = (task_path, document)
        missing = selected_ids - set(result)
        if missing:
            raise ValueError(f"missing selected task metadata: {sorted(missing)}")
        return result


def stable_profile_fingerprint(config: Mapping[str, Any]) -> str:
    _reject_secret_keys(config)
    return _canonical_hash(config)


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[- ]", "_", str(key).lower())
            if normalized in {"api_key", "apikey", "authorization", "access_token", "password", "secret"}:
                raise ValueError(f"variant fingerprints must not contain credentials: {key!r}")
            _reject_secret_keys(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _reject_secret_keys(item)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _public_observation_subjects(environment: Mapping[str, Any]) -> tuple[str, ...]:
    subjects: list[str] = []
    early = environment.get("early_terminate")
    if isinstance(early, Mapping):
        if early.get("match_field") == "status" and early.get("match_value") == "submitted":
            subjects.append("listing.submitted")
        elif early.get("match_field"):
            subjects.append(f"runtime.{early.get('match_field')}")
    runtime_mocks = environment.get("runtime_mocks")
    if isinstance(runtime_mocks, Mapping):
        if "gmail_mock" in runtime_mocks:
            subjects.extend(("mail.label_created", "calendar.event_created"))
        if "google_docs_mock" in runtime_mocks:
            subjects.append("document.updated")
    return tuple(dict.fromkeys(subjects))
