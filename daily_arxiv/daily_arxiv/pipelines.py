# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
import arxiv
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta


class DailyArxivPipeline:
    def __init__(self):
        self.page_size = 100
        self.client = arxiv.Client(self.page_size)
        self._metadata_cache = {}

    def process_item(self, item: dict, spider):
        item["pdf"] = f"https://arxiv.org/pdf/{item['id']}"
        item["abs"] = f"https://arxiv.org/abs/{item['id']}"
        paper_id = item["id"]
        if paper_id not in self._metadata_cache:
            search = arxiv.Search(id_list=[paper_id])
            paper = next(self.client.results(search))
            metadata = {
                "authors": [a.name for a in paper.authors],
                "title": paper.title,
                "categories": paper.categories,
                "comment": paper.comment,
                "summary": paper.summary,
            }
            # Cache only complete, successful results for this crawl instance.
            self._metadata_cache[paper_id] = deepcopy(metadata)
        # Each exported item owns its mutable fields; callers cannot alter the cache.
        item.update(deepcopy(self._metadata_cache[paper_id]))
        return item
