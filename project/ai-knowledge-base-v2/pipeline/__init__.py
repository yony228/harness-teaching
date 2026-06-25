""" conocimiento :man_obstacle: pipeline package.

Four-step automated knowledge-base pipeline:
Collect (GitHub+RSS) -> Analyze (LLM) -> Organize -> Save.
"""

from pipeline.pipeline import main as main
from pipeline.pipeline import run_pipeline as run_pipeline
from pipeline.pipeline import collect_github as collect_github
from pipeline.pipeline import collect_rss as collect_rss
from pipeline.pipeline import analyze_items as analyze_items
from pipeline.pipeline import deduplicate as deduplicate
from pipeline.pipeline import standardize as standardize
from pipeline.pipeline import save_articles as save_articles
from pipeline.pipeline import BASE_DIR as BASE_DIR
from pipeline.pipeline import RAW_DIR as RAW_DIR
from pipeline.pipeline import ARTICLES_DIR as ARTICLES_DIR

__all__ = [
    "main",
    "run_pipeline",
    "collect_github",
    "collect_rss",
    "analyze_items",
    "deduplicate",
    "standardize",
    "save_articles",
    "BASE_DIR",
    "RAW_DIR",
    "ARTICLES_DIR",
]
