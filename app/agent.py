import os
import asyncio
import uuid
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

async def run_agent():
    model = ChatOpenAI(model="gpt-4o", temperature=0)
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    # Pass current environment variables (including DATABASE_URL) to the MCP child process
    client = MultiServerMCPClient(
        {
            "financial_mcp": {
                "command": "python",
                "args": ["-m", "app.mcp_server"],
                "transport": "stdio",
                "env": os.environ.copy(),
            }
        }
    )

    tools = await client.get_tools()
    print(f"Session ID: {session_id}")
    print(f"Discovered MCP tools: {[t.name for t in tools]}")

    agent_executor = create_react_agent(model, tools)

    query = (
        f"Using session_id '{session_id}', check member ID 1's accounts "
        "and calculate their TFSA contribution room for 2025."
    )
    print(f"\nRunning Query: {query}\n" + "-" * 50)

    async for event in agent_executor.astream({"messages": [("user", query)]}):
        for value in event.values():
            if "messages" in value:
                value["messages"][-1].pretty_print()

if __name__ == "__main__":
    asyncio.run(run_agent())