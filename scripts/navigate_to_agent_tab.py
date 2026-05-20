"""
Deterministic script: navigate_to_agent_tab
Clicks the specified agent tab on the SAI canvas.
Used by journey steps with type=browser_action.
"""
from scripts.base_script import DeterministicScript, ScriptResult
from app.browser.sai_navigator import SAINavigator
import asyncio


class NavigateToAgentTab(DeterministicScript):
    name = "scripts/navigate_to_agent_tab.py"

    async def run(self, page, args: dict) -> ScriptResult:
        agent_name = args.get("agent_name", "")
        if not agent_name:
            return ScriptResult(success=False, error="agent_name not specified")
        nav = SAINavigator(page)
        success = await nav.navigate_to_agent_tab(agent_name)
        await asyncio.sleep(1.5)
        return ScriptResult(
            success=success,
            output={"agent_tab": agent_name, "navigated": success},
            notes=f"Navigated to agent tab: {agent_name}",
        )
