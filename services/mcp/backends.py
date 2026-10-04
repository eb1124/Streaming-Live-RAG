"""Where document_search retrieves (phase 8). Both backends take the phase 7 RetrievalRequest and return the phase 7
RetrievalResponse; neither retrieves by itself:

  LocalSearch(RetrievalService(stack))      in-process: the phase 7 retrieval service logic over a loaded stack
  RemoteSearch(httpx.Client(base_url=...))  the running retrieval service (python -m services.retrieval), POST /retrieve

So document_search is the frozen retrieval (generation.pipeline.retrieve) or the phase 6 loop, exactly as the
orchestrator gets them. The remote call goes through services.http.post: the response contract is validated and must
echo the request's correlation ids.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from ..contracts import RetrievalRequest, RetrievalResponse
from ..http import post
from ..retrieval.service import RetrievalService


class SearchBackend(Protocol):
    name: str

    def search(self, req: RetrievalRequest) -> RetrievalResponse: ...


class LocalSearch:
    name = "local"

    def __init__(self, service: RetrievalService):
        self.service = service

    def search(self, req: RetrievalRequest) -> RetrievalResponse:
        return self.service.handle(req)


class RemoteSearch:
    def __init__(self, http: httpx.Client):
        self.http = http
        self.name = f"remote {http.base_url}"

    def search(self, req: RetrievalRequest) -> RetrievalResponse:
        return post(self.http, "/retrieve", req, RetrievalResponse)
