"""
性騷擾防治智能 AI — 抽象 Agent 基底類別
所有 Agent 都應繼承此類別，以確保統一的介面與擴展性。

使用方式：
    class MyAgent(BaseAgent):
        def _build_agent(self) -> LlmAgent:
            return LlmAgent(
                name="my_agent",
                model=self.model,
                instruction="...",
                tools=[...],
            )
"""

import base64
from abc import ABC, abstractmethod
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger

logger = get_logger(__name__)


class BaseAgent(ABC):
    """
    所有 Agent 的抽象基底類別。

    子類別必須實作 `_build_agent()` 方法，回傳一個已設定好的 `LlmAgent`。
    此基底類別負責統一管理 Runner 的初始化與請求的執行。
    """

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.gemini_model
        self._agent: LlmAgent = self._build_agent()
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=self._agent,
            app_name=self._agent.name,
            session_service=self._session_service,
        )

    @abstractmethod
    def _build_agent(self) -> LlmAgent:
        """
        建立並回傳 LlmAgent 實例。
        子類別必須在此設定 Agent 的 instruction、tools 等參數。
        """
        ...

    async def run(
        self,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        session_id: str = "default",
        user_id: str = "anonymous",
        image_base64: str | None = None,
    ) -> str:
        """
        執行一次 Agent 對話。

        Args:
            user_message: 當前使用者的訊息（已完成匿名化）
            history: 前端傳入的對話歷史，格式為
                     [{"role": "user"|"assistant", "content": "..."}]
            session_id: 本次會話的 ID（每次對話結束後不保留）
            user_id: 匿名使用者識別（不含個人資訊）

        Returns:
            Agent 回覆的文字內容
        """
        # 將前端傳入的對話歷史轉換成 ADK Content 格式
        contents = self._build_contents(history or [], user_message, image_base64)

        logger.info(
            "Running agent %s for session %s (history turns: %d)",
            self._agent.name,
            session_id,
            len(history or []),
        )

        # 1. 建立 Session
        session = await self._session_service.create_session(
            app_name=self._agent.name,
            user_id=user_id,
            session_id=session_id,
        )

        # 2. 將歷史對話記錄轉為 Event 寫入 Session 儲存
        for content in contents[:-1]:
            # user role 為 'user'，model role 為 agent 的名稱，避免 ADK 混淆
            author = "user" if content.role == "user" else self._agent.name
            event = Event(
                author=author,
                content=content,
            )
            await self._session_service.append_event(session=session, event=event)

        # 使用 InMemorySessionService，每次請求完成後記憶體自動清空
        response_text = ""
        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=contents[-1],
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text

        logger.info(
            "Agent %s run completed. Response len: %d chars",
            self._agent.name,
            len(response_text),
        )
        return response_text.strip()

    # ── 私有輔助方法 ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_contents(
        history: list[dict[str, str]], current_message: str, image_base64: str | None = None
    ) -> list[genai_types.Content]:
        """將對話歷史轉換為 ADK 所需的 Content 格式。"""
        contents: list[genai_types.Content] = []

        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not content:
                continue
            # ADK 使用 "model" 而非 "assistant"
            adk_role = "model" if role == "assistant" else "user"
            contents.append(
                genai_types.Content(
                    role=adk_role,
                    parts=[genai_types.Part(text=content)],
                )
            )

        # 處理當前訊息與圖片
        parts = []
        if current_message:
            parts.append(genai_types.Part(text=current_message))
        elif image_base64:
            # 如果沒有文字但有圖片，可以給一個預設文字
            parts.append(genai_types.Part(text="[使用者上傳了圖片]"))

        if image_base64:
            try:
                # 格式通常為 "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
                header, encoded = image_base64.split(",", 1)
                mime_type = header.split(":")[1].split(";")[0]
                image_bytes = base64.b64decode(encoded)
                # 使用 from_bytes 建立多模態 Part
                parts.append(genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
            except Exception as e:
                logger.error("Failed to decode image_base64: %s", e)

        contents.append(
            genai_types.Content(
                role="user",
                parts=parts,
            )
        )
        return contents

    def get_agent_info(self) -> dict[str, Any]:
        """回傳此 Agent 的基本資訊（供 API 文件使用）。"""
        return {
            "name": self._agent.name,
            "model": self.model,
            "description": self.__class__.__doc__ or "",
        }
