"""Retrieval service client (phase 7): a drop-in for generation.pipeline.retrieve on the orchestrator side.

  retriever = RetrievalClient(httpx.Client(base_url=RETRIEVAL_URL), mode="iterative", max_rounds=3)
  SessionController(catalog, provider, retriever=retriever, ...)       # phase 6's retriever hook

`retriever(stack, query)` has retrieve()'s signature; the stack argument is ignored (the service owns the index). It
sends a RetrievalRequest with the bound correlation ids (services.correlation), checks that the response echoes
them, and returns the Retrieval phases 1 to 3 consume (StreamingRetrieval with its trace in iterative mode). An HTTP
error, a transport error or an invalid response raises ServiceError: the job fails, as for any exception.
"""

from __future__ import annotations

import httpx

from generation.pipeline import RETRIEVE_K, Retrieval

from .. import correlation
from ..contracts import RetrievalRequest, RetrievalResponse
from ..http import post


class RetrievalClient:
    def __init__(self, http: httpx.Client, mode: str = "single", max_rounds: int = 3):
        self.http, self.mode, self.max_rounds = http, mode, max_rounds

    def __call__(self, stack, query: str, k: int = RETRIEVE_K) -> Retrieval:
        req = RetrievalRequest(**correlation.current(), query=query, mode=self.mode, max_rounds=self.max_rounds, k=k)
        return post(self.http, "/retrieve", req, RetrievalResponse).retrieval()
