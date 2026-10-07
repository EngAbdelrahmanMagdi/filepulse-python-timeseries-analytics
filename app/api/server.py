import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import Error as PostgresError
from psycopg_pool import PoolTimeout

from app.analytics.anomalies import AnomalyService
from app.analytics.queries import EventQueries, bounds
from app.analytics.scan_cache import ScanCache
from app.api.anomaly_schemas import (
    AnomaliesResponse,
    AnomalyDetail,
    AnomalyQuery,
    DetailQuery,
    ModelStatus,
)
from app.api.schemas import (
    ActivityQuery,
    EventsQuery,
    EventsResponse,
    ExtensionsResponse,
    HealthResponse,
    OverviewResponse,
    ScanRequest,
    ScanResponse,
    SeriesQuery,
    SeriesResponse,
)
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.paths import RootPaths
from app.database.mongo import MongoStore
from app.database.postgres import PostgresStore

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config = settings or Settings()
        configure_logging()
        postgres = PostgresStore(config)
        mongo = MongoStore(config)
        application.state.settings = config
        application.state.queries = EventQueries(postgres)
        application.state.anomalies = AnomalyService(application.state.queries)
        application.state.mongo = mongo
        application.state.scans = ScanCache(RootPaths(config.watch_root))
        logger.info("api_started")
        try:
            yield
        finally:
            postgres.close()
            mongo.close()

    application = FastAPI(title="FilePulse", version="0.3.0", lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Invalid request parameters.",
                "fields": [".".join(str(item) for item in error["loc"]) for error in exc.errors()],
            },
        )

    @application.exception_handler(Exception)
    @application.exception_handler(PostgresError)
    @application.exception_handler(PoolTimeout)
    async def unavailable(request: Request, exc: Exception):
        logger.warning("api_request_failed reason=%s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"detail": "Data temporarily unavailable. Please retry shortly."},
        )

    @application.get("/health", response_model=HealthResponse)
    def health(request: Request):
        postgres = timescale = mongodb = "unavailable"
        latest = None
        try:
            request.app.state.queries.rows("SELECT 1")
            postgres = "ready"
            row = request.app.state.queries.rows(
                "SELECT EXISTS (SELECT 1 FROM timescaledb_information.hypertables "
                "WHERE hypertable_name='file_events') AS hypertable"
            )[0]
            timescale = "ready" if row["hypertable"] else "unavailable"
            latest_rows = request.app.state.queries.rows(
                "SELECT timestamp FROM file_events WHERE timestamp >= %s AND timestamp < %s "
                "ORDER BY timestamp DESC, event_id DESC LIMIT 1",
                bounds(ActivityQuery(window="7d")),
            )
            latest = latest_rows[0]["timestamp"] if latest_rows else None
        except Exception as exc:
            logger.warning("health_postgres_failed reason=%s", type(exc).__name__)
        try:
            request.app.state.mongo.client.admin.command("ping")
            mongodb = "ready"
        except Exception as exc:
            logger.warning("health_mongodb_failed reason=%s", type(exc).__name__)
        result = HealthResponse(
            status="healthy" if postgres == timescale == mongodb == "ready" else "degraded",
            postgres=postgres,
            timescaledb=timescale,
            mongodb=mongodb,
            scan=request.app.state.scans.status(),
            latest_event_at=latest,
            runtime_mode=request.app.state.settings.observer_mode,
        )
        return JSONResponse(
            status_code=200 if result.status == "healthy" else 503,
            content=result.model_dump(mode="json"),
        )

    @application.get("/api/v1/activity/timeseries", response_model=SeriesResponse)
    def timeseries(request: Request, query: Annotated[SeriesQuery, Query()]):
        return request.app.state.queries.timeseries(query)

    @application.get("/api/v1/anomalies/model", response_model=ModelStatus)
    def model_status(request: Request):
        return request.app.state.anomalies.status()

    @application.post("/api/v1/anomalies/train", response_model=ModelStatus)
    def train_model(request: Request, body: ScanRequest):
        result = request.app.state.anomalies.train()
        code = {"ready": 200, "busy": 409, "insufficient_data": 422, "failed": 503}
        return JSONResponse(
            status_code=code[result.last_attempt], content=result.model_dump(mode="json")
        )

    @application.get("/api/v1/anomalies", response_model=AnomaliesResponse)
    def anomalies(request: Request, query: Annotated[AnomalyQuery, Query()]):
        return request.app.state.anomalies.list(query)

    @application.get("/api/v1/anomalies/detail", response_model=AnomalyDetail)
    def anomaly_detail(request: Request, query: Annotated[DetailQuery, Query()]):
        result = request.app.state.anomalies.detail(query.timestamp)
        if result is None:
            raise HTTPException(
                404, "No model or complete bucket available in the last seven days."
            )
        return result

    @application.get("/api/v1/events", response_model=EventsResponse)
    def events(request: Request, query: Annotated[EventsQuery, Query()]):
        return request.app.state.queries.events(query)

    @application.get("/api/v1/overview", response_model=OverviewResponse)
    def overview(request: Request, query: Annotated[ActivityQuery, Query()]):
        start, end = bounds(query, datetime.now(UTC))
        scan = request.app.state.scans.get()
        try:
            activity = request.app.state.queries.overview(query, end)
            return OverviewResponse(
                start=start,
                end=end,
                activity_available=True,
                scan=scan,
                **activity,
            )
        except Exception as exc:
            logger.warning("overview_activity_failed reason=%s", type(exc).__name__)
            return OverviewResponse(
                start=start,
                end=end,
                activity_available=False,
                scan=scan,
                activity_message="Activity unavailable. Static scan data remains independent.",
            )

    @application.get("/api/v1/files/summary", response_model=ScanResponse)
    def summary(request: Request):
        return request.app.state.scans.get()

    @application.get("/api/v1/files/extensions", response_model=ExtensionsResponse)
    def extensions(request: Request):
        result = request.app.state.scans.get()
        return ExtensionsResponse(
            **result.model_dump(exclude={"summary"}),
            extensions=result.summary.extensions if result.summary else [],
        )

    @application.post("/api/v1/files/scan", response_model=ScanResponse)
    def refresh(request: Request, body: ScanRequest):
        result = request.app.state.scans.get(refresh=True)
        return JSONResponse(
            status_code=200 if result.state == "ready" else 503,
            content=result.model_dump(mode="json"),
        )

    return application


app = create_app()
