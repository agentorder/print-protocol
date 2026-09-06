"""MCP entrypoint; tools intentionally exclude approval, checkout, and payment."""

from mcp.server.fastmcp import FastMCP
from reference_agent import Agent

mcp = FastMCP("AgentOrder Reference")
_agent = None


def agent():
    global _agent
    if _agent is None:
        _agent = Agent("https://agent.example.invalid/profile.json")
    return _agent


@mcp.tool()
def discover_printer(url: str) -> dict:
    return agent().discover_printer(url + "/.well-known")


@mcp.tool()
def request_quote(
    printer_url: str, print_job: dict, buyer: dict, fulfillment_destination: dict
) -> dict:
    rfq, status, response = agent().request_quote(
        printer_url,
        print_job,
        buyer,
        fulfillment_destination,
        "mcp-" + __import__("secrets").token_urlsafe(12),
    )
    return {"rfq": rfq, "status": status, "response": response}


@mcp.tool()
def get_quote(printer_url: str, quote_id: str) -> dict:
    return agent().get_quote(printer_url, quote_id)
