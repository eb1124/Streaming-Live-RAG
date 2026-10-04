"""The AdaptiveRAG MCP server (phase 8): three tools over the Model Context Protocol (the official `mcp` SDK, low-level
Server), so the published schemas are exactly the contracts' and nothing is accepted that they do not allow.

  tools/list   document_search, metadata_lookup, database_query: inputSchema / outputSchema from services/mcp/contracts
  tools/call   arguments validated against the input model (unknown fields rejected)
                 ok                 -> structuredContent (the output model, validated) + the same JSON as text
                 invalid arguments  -> isError result, error code invalid_arguments, per-field details
                 ToolFailure        -> isError result with its code (unknown_organization, not_found, ...)
                 retrieval service  -> isError result, backend_unavailable
                 anything else      -> isError result, internal_error (the exception stays in the server log)
               unknown tool name -> JSON-RPC error -32602 (a protocol error: there is no tool to report it)

Every call runs in a worker thread with its correlation ids bound (services.correlation), so a remote retrieval
request carries request_id / job_id / session_id to the retrieval service, which echoes them.

Phase 10A: every call of a known tool is an mcp.tool.execute span (tool name, correlation ids; for an isError result
the error code and the status ERROR). The retrieval and database spans of the call are its children.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ValidationError

from .. import correlation, telemetry
from ..http import ServiceError
from .contracts import (Correlation, DatabaseQueryInput, DatabaseQueryOutput, DocumentSearchInput,
                        DocumentSearchOutput, MetadataLookupInput, MetadataLookupOutput, ToolErrorDetail)
from .tools import ToolFailure, Tools

log = logging.getLogger(__name__)
SERVER_NAME = "adaptiverag"
SERVER_VERSION = "8.0"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input: type[BaseModel]
    output: type[BaseModel]

    def tool(self) -> types.Tool:
        return types.Tool(name=self.name, title=self.title, description=self.description,
                          input_schema=self.input.model_json_schema(mode="validation"),
                          output_schema=self.output.model_json_schema(mode="serialization"),
                          annotations=types.ToolAnnotations(title=self.title, read_only_hint=True,
                                                            destructive_hint=False, idempotent_hint=True,
                                                            open_world_hint=False))


SPECS = {s.name: s for s in [
    ToolSpec("document_search", "Search the policy corpus",
             "Retrieve evidence chunks for a question with the AdaptiveRAG retrieval (dense + BM25 + RRF, temporal "
             "version resolution, cross-encoder rerank; or the phase 6 bounded loop). Every result carries its "
             "document, organization, version / effective date, pages, section, clause, chunk id and score. Optional "
             "filters (organization, doc_id, current_only, as_of) select among the retrieved chunks.",
             DocumentSearchInput, DocumentSearchOutput),
    ToolSpec("metadata_lookup", "Look up corpus metadata",
             "Document metadata from the repository (ingestion manifest and chunk metadata): list or filter documents "
             "by organization, series, domain, type or currency, or look up one chunk with its document.",
             MetadataLookupInput, MetadataLookupOutput),
    ToolSpec("database_query", "Query the metadata database",
             "Named, parameterized metadata queries (documents, document_versions, chunks_by_document). No SQL text "
             "is accepted. Served by PostgreSQL when the server has DATABASE_URL; otherwise calls fail with "
             "database_not_configured.",
             DatabaseQueryInput, DatabaseQueryOutput),
]}


def _error(err: ToolErrorDetail) -> types.CallToolResult:
    body = err.model_dump(mode="json")
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
                                structured_content={"error": body}, is_error=True)


def _validation_details(e: ValidationError) -> list[dict]:
    return [{"loc": [str(p) for p in d["loc"]], "type": d["type"], "msg": d["msg"]}
            for d in e.errors(include_url=False, include_context=False, include_input=False)]


def new_request_id() -> str:
    return f"mcp-{uuid.uuid4().hex[:16]}"


def build_server(tools: Tools) -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[s.tool() for s in SPECS.values()])

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        spec = SPECS.get(params.name)
        if spec is None:
            log.info("tool=%r rejected: unknown tool", params.name)
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {params.name}", {"tools": sorted(SPECS)})
        with telemetry.span("mcp.tool.execute", telemetry.MCP, {"mcp.tool.name": spec.name}) as span:
            return await execute(spec, params, span)

    async def execute(spec: ToolSpec, params: types.CallToolRequestParams, span) -> types.CallToolResult:
        def failed(err: ToolErrorDetail, exc: Exception) -> types.CallToolResult:
            span.fail(exc)  # phase 10A: the exception's type and the tool error code; never its message
            span.set({"mcp.error.code": err.code})
            return _error(err)

        try:
            inp = spec.input.model_validate(params.arguments or {})
        except ValidationError as e:
            details = _validation_details(e)
            log.info("tool=%s rejected arguments: %s", spec.name, [".".join(d["loc"]) for d in details])
            return failed(ToolErrorDetail(code="invalid_arguments",
                                          message=f"{len(details)} invalid argument(s) for {spec.name}",
                                          details=details), e)
        corr = Correlation(request_id=inp.request_id or new_request_id(), job_id=inp.job_id,
                           session_id=inp.session_id)
        span.correlate(corr.model_dump())

        def run():
            with correlation.bind(**corr.model_dump()):
                return getattr(tools, spec.name)(inp, corr)

        try:
            out = await anyio.to_thread.run_sync(run)
        except ToolFailure as f:
            log.info("tool=%s request=%s failed: %s", spec.name, corr.request_id, f.code)
            return failed(ToolErrorDetail(code=f.code, message=f.message, details=f.details, correlation=corr), f)
        except ServiceError as e:
            log.warning("tool=%s request=%s retrieval service: %s", spec.name, corr.request_id, e)
            return failed(ToolErrorDetail(code="backend_unavailable", message=str(e), correlation=corr), e)
        except Exception as e:
            log.exception("tool=%s request=%s crashed", spec.name, corr.request_id)
            return failed(ToolErrorDetail(code="internal_error", message=f"{spec.name} failed; see the server log",
                                          correlation=corr), e)
        structured = spec.output.model_validate(out.model_dump()).model_dump(mode="json")
        log.info("tool=%s request=%s job=%s session=%s ok", spec.name, corr.request_id, corr.job_id, corr.session_id)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))],
            structured_content=structured)

    return Server(SERVER_NAME, version=SERVER_VERSION, title="AdaptiveRAG",
                  instructions="Policy-corpus tools: search evidence with provenance, look up document metadata.",
                  on_list_tools=list_tools, on_call_tool=call_tool)
