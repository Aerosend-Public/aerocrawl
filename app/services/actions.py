from __future__ import annotations

import base64
from typing import List

from playwright.async_api import Page


async def execute_actions(page: Page, actions: List[dict]) -> List[dict]:
    """Execute a sequence of browser actions on a page. Failures don't stop execution."""
    results: list = []

    for action_def in actions:
        action_type = action_def.get("type", "")
        result: dict = {"action": action_type, "success": False}

        try:
            if action_type == "click":
                selector = action_def["selector"]
                await page.click(selector)
                result["success"] = True

            elif action_type == "type":
                selector = action_def["selector"]
                text = action_def["text"]
                await page.fill(selector, text)
                result["success"] = True

            elif action_type == "scroll":
                amount = action_def.get("amount", 1)
                direction_raw = action_def.get("direction", "down")
                direction = 1 if direction_raw == "down" else -1
                delta = 720 * amount * direction
                await page.mouse.wheel(0, delta)
                result["success"] = True

            elif action_type == "wait":
                ms = action_def.get("milliseconds", action_def.get("ms", 1000))
                await page.wait_for_timeout(ms)
                result["success"] = True

            elif action_type == "screenshot":
                screenshot_bytes = await page.screenshot()
                result["success"] = True
                result["data"] = base64.b64encode(screenshot_bytes).decode()

            elif action_type == "press_key":
                key = action_def["key"]
                await page.keyboard.press(key)
                result["success"] = True

            else:
                result["error"] = f"Unknown action type: {action_type}"

        except Exception as exc:
            result["error"] = str(exc)

        results.append(result)

    return results
