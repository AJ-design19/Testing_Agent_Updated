"""
Deterministic script: read_subtab
Navigates to a specific agent+sub-tab and returns its text content.
"""
from scripts.base_script import DeterministicScript, ScriptResult
from app.browser.sai_navigator import SAINavigator
import asyncio


class ReadSubtab(DeterministicScript):
    name = "scripts/read_subtab.py"

    async def run(self, page, args: dict) -> ScriptResult:
        agent_name = args.get("agent_name", "")
        sub_tab = args.get("sub_tab", "Output")
        nav = SAINavigator(page)
        if agent_name:
            await nav.navigate_to_agent_tab(agent_name)
            await asyncio.sleep(1)
        content = await nav.read_sub_tab_content(sub_tab)
        return ScriptResult(
            success=True,
            output={"agent": agent_name, "sub_tab": sub_tab, "content": content[:500]},
            notes=f"Read {agent_name}/{sub_tab}: {len(content)} chars",
        )
