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


@mcp.tool(description="Discover and validate a printer's UCP business profile.")
def discover_printer(url: str) -> dict:
    return agent().discover_printer(url + "/.well-known")


@mcp.tool(
    description="Submit an RFQ only; this tool cannot approve, check out, or pay for a print job."
)
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
    return {"rfq": rfq, "status": status, "response": response, "currency": response["currency"]}


@mcp.tool(description="Get a validated quote result, including its currency and expiry.")
def get_quote(printer_url: str, quote_id: str) -> dict:
    return agent().get_quote(printer_url, quote_id)
