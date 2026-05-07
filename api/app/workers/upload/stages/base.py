import time
from abc import ABC, abstractmethod
from typing import Any

from app.schemas.material import UploadStatus
from app.workers.upload.context import WorkerContext


class BaseStage[T](ABC):
    """Base class for all upload pipeline stages."""

    def __init__(self, pipeline: Any) -> None:
        self.pipeline = pipeline
        self.ctx: WorkerContext = pipeline.ctx
        self.tracer = pipeline.tracer

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def label(self) -> str: ...

    @abstractmethod
    async def execute(self) -> T: ...

    async def run(self) -> T:
        with self.tracer.start_as_current_span(f"upload.stage.{self.name}") as span:
            span.set_attribute("upload.id", self.pipeline.upload_id)
            start_time = time.monotonic()
            try:
                await self.pipeline.emit_status(
                    UploadStatus.PROCESSING,
                    detail=self.label,
                    stage_name_or_label=self.name,
                    stage_percent=0.0,
                )
                result = await self.execute()
                await self.pipeline.emit_status(
                    UploadStatus.PROCESSING,
                    detail=self.label,
                    stage_name_or_label=self.name,
                    stage_percent=1.0,
                )
                return result
            finally:
                duration = time.monotonic() - start_time
                span.set_attribute("upload.stage.duration", duration)
