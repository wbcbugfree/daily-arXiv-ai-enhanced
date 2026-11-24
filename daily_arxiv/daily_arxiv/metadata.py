import logging
import re
import time
from copy import deepcopy
from itertools import batched

import arxiv
import requests


logger = logging.getLogger(__name__)
BATCH_SIZE = 50
MAX_ATTEMPTS = 4
VERSION_SUFFIX = re.compile(r"v\d+$")


def fetch_batch(client, paper_ids, *, versioned):
    """Keep successful results across attempts, indexed by the requested ID."""
    found = {}
    pending = list(paper_ids)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        search = arxiv.Search(id_list=pending, max_results=len(pending))
        reason = "incomplete response"
        try:
            for paper in client.results(search):
                paper_id = paper.get_short_id()
                if not versioned:
                    paper_id = VERSION_SUFFIX.sub("", paper_id)
                if paper_id in pending:
                    found[paper_id] = {
                        "authors": [a.name for a in paper.authors],
                        "title": paper.title,
                        "categories": paper.categories,
                        "comment": paper.comment,
                        "summary": paper.summary,
                    }
        except arxiv.HTTPError as exc:
            if exc.status != 429 and not 500 <= exc.status < 600:
                raise
            reason = f"HTTP {exc.status}"
        except (arxiv.UnexpectedEmptyPageError, requests.ConnectionError, requests.Timeout) as exc:
            reason = str(exc)

        pending = [paper_id for paper_id in pending if paper_id not in found]
        if not pending:
            return found
        if attempt < MAX_ATTEMPTS:
            delay = 5 * 2 ** (attempt - 1)
            logger.warning("arXiv metadata: retrying %d missing IDs in %ds (%s)",
                           len(pending), delay, reason)
            time.sleep(delay)

    raise RuntimeError(f"Missing arXiv metadata after {MAX_ATTEMPTS} attempts "
                       f"({reason}): {', '.join(pending)}")


def enrich_papers(items):
    """Fetch metadata for distinct IDs, then restore listing order and duplicates."""
    # This loop owns retries; keep the SDK's serial pacing between all requests.
    client = arxiv.Client(page_size=BATCH_SIZE, delay_seconds=3, num_retries=0)
    paper_ids = dict.fromkeys(item["id"] for item in items)
    metadata = {}
    # Keep latest-version requests separate from explicitly pinned versions:
    # a response for v1 must never satisfy an independent request for latest.
    for versioned in (False, True):
        ids = (pid for pid in paper_ids if bool(VERSION_SUFFIX.search(pid)) == versioned)
        for batch in batched(ids, BATCH_SIZE):
            metadata.update(fetch_batch(client, batch, versioned=versioned))

    for item in items:
        paper_id = item["id"]
        yield {
            **item,
            "pdf": f"https://arxiv.org/pdf/{paper_id}",
            "abs": f"https://arxiv.org/abs/{paper_id}",
            **deepcopy(metadata[paper_id]),
        }
