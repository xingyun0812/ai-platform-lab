from __future__ import annotations

import base64
import logging

from packages.agent.computer_use.models import ActionResult

logger = logging.getLogger("ai_platform.agent.computer_use.executor")


class ComputerUseExecutor:
    """Computer Use 动作执行器。

    支持的操作：
    - screenshot: 截取当前屏幕
    - click(x, y): 点击指定坐标
    - type(text): 输入文本
    - key(key): 按键
    - scroll(dx, dy): 滚动
    - move(x, y): 移动鼠标
    - done(answer): 完成任务
    """

    def __init__(self, sandbox_mode: str = "process", display: str | None = None):
        self._sandbox_mode = sandbox_mode
        self._display = display or ":99"
        self._screen_width = 1024
        self._screen_height = 768

    async def screenshot(self) -> bytes:
        """截取当前屏幕，返回 PNG 字节。

        优先使用 mss，回退到 pyautogui，最后 mock。
        """
        return await self._capture_screen()

    async def _capture_screen(self) -> bytes:
        # 尝试 mss（最快）
        try:
            import mss

            with mss.mss() as sct:
                monitor = sct.monitors[1]  # 主显示器
                self._screen_width = monitor["width"]
                self._screen_height = monitor["height"]
                sct_img = sct.grab(monitor)
                from PIL import Image

                img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
                import io

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("mss screenshot failed: %s", exc)

        # 尝试 pyautogui
        try:
            import io

            import pyautogui
            from PIL import Image

            img = pyautogui.screenshot()
            self._screen_width, self._screen_height = img.size
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("pyautogui screenshot failed: %s", exc)

        # mock：生成空白截图
        logger.info("computer_use: using mock screenshot")
        return self._mock_screenshot()

    def _mock_screenshot(self) -> bytes:
        """生成一张空白测试截图。"""
        try:
            import io

            from PIL import Image

            img = Image.new("RGB", (self._screen_width, self._screen_height), color=(240, 240, 240))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            return b"MOCK_SCREENSHOT"

    async def execute(self, action: ActionResult) -> ActionResult:
        """执行一个动作，返回执行结果。"""
        action_type = action.action_type

        # 先截图（done 和 screenshot 不需要前截图）
        if action_type not in ("done", "screenshot"):
            before_bytes = await self._capture_screen()
            action.screenshot_before = base64.b64encode(before_bytes).decode()

        try:
            if action_type == "click":
                await self._click(action.x, action.y)
            elif action_type == "type":
                await self._type(action.text or "")
            elif action_type == "key":
                await self._key(action.key or "")
            elif action_type == "scroll":
                await self._scroll(action.dx or 0, action.dy or 0)
            elif action_type == "move":
                await self._move(action.x, action.y)
            elif action_type == "screenshot":
                pass  # 只需要截图
            elif action_type == "done":
                pass  # 无动作
        except Exception as exc:
            action.error = str(exc)
            logger.warning("computer_use: action %s failed: %s", action_type, exc)

        # 动作后截图（done 不需要）
        if action_type != "done":
            after_bytes = await self._capture_screen()
            action.screenshot_after = base64.b64encode(after_bytes).decode()

        return action

    async def _click(self, x: int | None, y: int | None) -> None:
        if x is None or y is None:
            raise ValueError("click 需要 x 和 y 坐标")
        try:
            import pyautogui

            pyautogui.click(x, y)
        except ImportError:
            logger.info("mock click(%d, %d)", x, y)

    async def _type(self, text: str) -> None:
        if not text:
            return
        try:
            import pyautogui

            pyautogui.write(text)
        except ImportError:
            logger.info("mock type(%s)", text[:30])

    async def _key(self, key: str) -> None:
        try:
            import pyautogui

            pyautogui.press(key)
        except ImportError:
            logger.info("mock key(%s)", key)

    async def _scroll(self, dx: int, dy: int) -> None:
        try:
            import pyautogui

            pyautogui.scroll(dy)
        except ImportError:
            logger.info("mock scroll(%d, %d)", dx, dy)

    async def _move(self, x: int | None, y: int | None) -> None:
        if x is None or y is None:
            return
        try:
            import pyautogui

            pyautogui.moveTo(x, y)
        except ImportError:
            logger.info("mock moveTo(%d, %d)", x, y)
