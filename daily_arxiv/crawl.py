"""Run the existing Scrapy spider and publish its JSONL only after validation."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from scrapy import signals
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings


def crawl_problems(stats):
    """Check final failures, without rejecting retries that later recovered."""
    problems = []
    if stats.get("finish_reason") != "finished":
        problems.append(f"finish_reason={stats.get('finish_reason', 'missing')}")
    failure_keys = {
        "retry/max_reached", "httperror/response_ignored_count",
        "integrity/item_error_count", "item_dropped_count",
        "robotstxt/forbidden", "offsite/filtered",
        "log_count/ERROR", "log_count/CRITICAL",
    }
    failure_prefixes = ("spider_exceptions/", "feedexport/failed_count/")
    for key, value in stats.items():
        if (key in failure_keys or key.startswith(failure_prefixes)) and value:
            problems.append(f"{key}={value}")
    return problems


def run_crawl(output, settings=None, spider="arxiv"):
    """Return 0 only for a clean crawl with a complete local export."""
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=output.name + ".", suffix=".partial", delete=False
    ) as temporary:
        staged = Path(temporary.name)

    try:
        settings = (settings if settings is not None else get_project_settings()).copy()
        settings.set("FEEDS", {staged.as_uri(): {
            "format": "jsonlines", "encoding": "utf-8",
            "overwrite": True, "store_empty": True,
        }}, priority="cmdline")
        process = CrawlerProcess(settings)
        crawler = process.create_crawler(spider)
        startup_errors = []

        def item_error():
            crawler.stats.inc_value("integrity/item_error_count")

        def crawl_error(failure):
            startup_errors.append(failure.getErrorMessage())

        crawler.signals.connect(item_error, signal=signals.item_error, weak=False)
        process.crawl(crawler).addErrback(crawl_error)
        process.start()

        stats = crawler.stats.get_stats() if crawler.stats is not None else {}
        problems = startup_errors + crawl_problems(stats)
        if process.bootstrap_failed:
            problems.append("crawler startup failed")
        if problems:
            raise RuntimeError("; ".join(problems))

        # Exporter signal errors do not necessarily make Scrapy fail. Check the
        # actual file as well, including a valid empty export on a quiet day.
        exported_count = 0
        with staged.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    if not isinstance(json.loads(line), dict):
                        raise ValueError("exported JSONL contains a non-object record")
                    exported_count += 1
        scraped_count = stats.get("item_scraped_count", 0)
        if exported_count != scraped_count:
            raise RuntimeError(f"exported {exported_count} records, expected {scraped_count}")

        os.replace(staged, output)
        print(f"Crawl integrity passed: {exported_count} records exported", file=sys.stderr)
        return 0
    except Exception as error:
        print(f"Crawl integrity check failed: {error}", file=sys.stderr)
        return 1
    finally:
        staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", required=True, type=Path, help="Output JSONL file")
    args = parser.parse_args()
    return run_crawl(args.output)


if __name__ == "__main__":
    sys.exit(main())
