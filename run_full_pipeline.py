"""Command-line entry point for the unified analysis pipeline."""
import argparse
import json

from pipeline import DEMO_NEWS, PipelineResult, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="缅甸地缘风险全流程")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--demo", action="store_true", help="使用内置数据并强制离线")
    source.add_argument("--skip-crawl", action="store_true", help="使用本地已有数据")
    parser.add_argument("--skip-llm", action="store_true", help="禁用 LLM 分析")
    parser.add_argument("--no-persist", action="store_true", help="不写入历史和产物")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    source_mode = "demo" if args.demo else "existing" if args.skip_crawl else "live"
    result = run_pipeline(
        source_mode=source_mode,
        include_llm=not args.skip_llm,
        persist=not args.no_persist,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
