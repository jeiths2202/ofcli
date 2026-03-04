"""BaseAgent 추상 클래스"""
from abc import ABC, abstractmethod

from app.models.search import PipelineState


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def execute(self, state: PipelineState) -> None:
        """Agent 핵심 실행. state를 직접 변경한다."""
        ...
