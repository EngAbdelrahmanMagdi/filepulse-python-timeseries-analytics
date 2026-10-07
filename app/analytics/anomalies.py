import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.analytics.queries import AGGREGATES, WINDOWS, EventQueries, totals
from app.api.anomaly_schemas import (
    AnomaliesResponse,
    AnomalyBucket,
    AnomalyDetail,
    AnomalyQuery,
    Comparison,
    ModelStatus,
)
from app.api.schemas import ActivityTotals

logger = logging.getLogger(__name__)
FEATURES = ["total", "created", "modified", "deleted", "moved", "unique_files", "unique_extensions"]


def minute(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)


def feature_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["timestamp", *FEATURES])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame[FEATURES] = frame[FEATURES].astype("int64")
    return frame


@dataclass(frozen=True)
class TrainedModel:
    forest: IsolationForest
    statistics: pd.DataFrame
    status: ModelStatus


class AnomalyService:
    """Explicit process-local model; queries and inference never initiate training."""

    def __init__(self, queries: EventQueries) -> None:
        self.queries = queries
        self._training = Lock()
        self._state = Lock()
        self._model: TrainedModel | None = None
        self._status = ModelStatus(features=FEATURES)

    def status(self) -> ModelStatus:
        with self._state:
            return self._status.model_copy(update={"training": self._training.locked()})

    def buckets(self, start: datetime, end: datetime) -> list[dict]:
        if not start < end or end - start > timedelta(days=7):
            raise ValueError("Bucket query must be bounded to seven days")
        rows = self.queries.rows(
            f"SELECT time_bucket('1 minute', timestamp) AS timestamp, {AGGREGATES}, "
            "count(DISTINCT extension) FILTER (WHERE NOT is_directory) AS unique_extensions "
            "FROM file_events WHERE timestamp >= %s AND timestamp < %s GROUP BY 1 ORDER BY 1",
            (start, end),
        )
        measured = {}
        for row in rows:
            stamp = row.pop("timestamp").astimezone(UTC)
            extensions = row.pop("unique_extensions")
            measured[stamp] = {**totals(row).model_dump(), "unique_extensions": extensions}
        empty = {**ActivityTotals().model_dump(), "unique_extensions": 0}
        return [
            {"timestamp": stamp.to_pydatetime(), **measured.get(stamp.to_pydatetime(), empty)}
            for stamp in pd.date_range(start, end, freq="min", inclusive="left")
        ]

    def train(self, now: datetime | None = None) -> ModelStatus:
        if not self._training.acquire(blocking=False):
            return self.status().model_copy(update={"last_attempt": "busy"})
        try:
            cutoff = minute(now)
            start = cutoff - timedelta(days=7)
            raw = feature_frame(self.buckets(start, cutoff))
            active = raw.loc[raw["total"] > 0].reset_index(drop=True)
            sufficient = (
                len(active) >= 30
                and (active["timestamp"].max() - active["timestamp"].min()).total_seconds()
                >= 1800
                and len(active[FEATURES].drop_duplicates()) >= 3
            )
            if not sufficient:
                return self._failed_attempt(
                    "insufficient_data",
                    (
                        f"Found {len(active)} active complete buckets. Need at least 30, "
                        "spanning 30 minutes, with three distinct activity patterns."
                    ),
                )
            forest = IsolationForest(
                n_estimators=100,
                max_samples="auto",
                contamination="auto",
                random_state=42,
                n_jobs=1,
            ).fit(np.log1p(active[FEATURES]))
            statistics = pd.DataFrame(
                {
                    "median": active[FEATURES].median(),
                    "p95": active[FEATURES].quantile(0.95),
                }
            )
            status = ModelStatus(
                state="ready",
                trained_at=now or datetime.now(UTC),
                training_start=start,
                training_cutoff=cutoff,
                sample_count=len(active),
                features=FEATURES,
                last_attempt="ready",
            )
            replacement = TrainedModel(forest, statistics, status)
            with self._state:
                self._model, self._status = replacement, status
            logger.info("model_trained samples=%s cutoff=%s", len(active), cutoff.isoformat())
            return status
        except Exception as exc:
            logger.warning("model_training_failed reason=%s", type(exc).__name__)
            return self._failed_attempt("failed", "Training unavailable. Check data and retry.")
        finally:
            self._training.release()

    def _failed_attempt(self, outcome: str, message: str) -> ModelStatus:
        with self._state:
            self._status = self._status.model_copy(
                update={
                    "state": "ready" if self._model else outcome,
                    "last_attempt": outcome,
                    "message": message,
                }
            )
            return self._status.model_copy()

    def _snapshot(self) -> tuple[TrainedModel | None, ModelStatus]:
        with self._state:
            return self._model, self._status.model_copy(
                update={"training": self._training.locked()}
            )

    @staticmethod
    def comparisons(row: dict, model: TrainedModel) -> list[Comparison]:
        return [
            Comparison(
                feature=name,
                observed=row[name],
                median=float(model.statistics.loc[name, "median"]),
                p95=float(model.statistics.loc[name, "p95"]),
            )
            for name in FEATURES
        ]

    def score(self, rows: list[dict], model: TrainedModel) -> list[AnomalyBucket]:
        eligible = [
            row
            for row in rows
            if row["timestamp"] >= model.status.training_cutoff and row["total"] > 0
        ]
        scores = {}
        if eligible:
            frame = feature_frame(eligible)
            scores = dict(
                zip(
                    frame["timestamp"],
                    -model.forest.decision_function(np.log1p(frame[FEATURES])),
                    strict=True,
                )
            )
        result = []
        for row in rows:
            state = (
                "outside_scoring_period"
                if row["timestamp"] < model.status.training_cutoff
                else "inactive"
                if row["total"] == 0
                else "scored"
            )
            score = float(scores[row["timestamp"]]) if state == "scored" else None
            context = (
                [item for item in self.comparisons(row, model) if item.observed > item.p95]
                if state == "scored"
                else []
            )
            result.append(
                AnomalyBucket(
                    **row,
                    state=state,
                    anomaly_score=score,
                    flagged=score is not None and score > 0,
                    observed_context=context,
                )
            )
        return result

    def list(self, query: AnomalyQuery, now: datetime | None = None) -> AnomaliesResponse:
        model, status = self._snapshot()
        end = minute(now)
        start = end - timedelta(seconds=WINDOWS[query.window])
        response = AnomaliesResponse(
            model=status,
            start=start,
            end=end,
            limit=query.limit,
            offset=query.offset,
        )
        if model is None:
            return response
        rows = self.score(self.buckets(start, end), model)
        flagged = [row for row in reversed(rows) if row.flagged]
        response.timeline = rows
        response.scored_count = sum(row.state == "scored" for row in rows)
        response.flagged_count = len(flagged)
        response.latest_flagged = flagged[0].timestamp if flagged else None
        response.anomalies = flagged[query.offset : query.offset + query.limit]
        response.has_more = len(flagged) > query.offset + query.limit
        return response

    def detail(self, timestamp: datetime, now: datetime | None = None) -> AnomalyDetail | None:
        model, status = self._snapshot()
        end = minute(now)
        if model is None or timestamp >= end or timestamp < end - timedelta(days=7):
            return None
        start = max(timestamp - timedelta(minutes=5), end - timedelta(days=7))
        stop = min(timestamp + timedelta(minutes=6), end)
        rows = self.score(self.buckets(start, stop), model)
        bucket = next(row for row in rows if row.timestamp == timestamp)
        return AnomalyDetail(
            model=status,
            bucket=bucket,
            comparisons=self.comparisons(bucket.model_dump(), model),
            surrounding=[row for row in rows if row.timestamp != timestamp],
        )
