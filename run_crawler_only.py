"""
一键运行爬虫 - 面向非工程师成员的简化入口

使用方法：
  python run_crawler_only.py              # 单次爬取（所有源）
  python run_crawler_only.py --schedule   # 定时爬取模式（前台运行，Ctrl+C 停止）
  python run_crawler_only.py --gdelt      # 仅查询 GDELT 数据
  python run_crawler_only.py --rss        # 仅爬取 RSS 新闻源
"""
import sys
import os
import json
import time

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 50)
print("  缅甸新闻爬虫 - 一键运行")
print("=" * 50)

# 检查依赖
missing = []
for pkg in ["requests", "bs4", "yaml"]:
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)

if missing:
    print(f"\n[错误] 缺少依赖包，请先运行：")
    print(f"  pip install requests beautifulsoup4 lxml pyyaml")
    print(f"\n安装完成后重新运行本脚本即可。")
    input("\n按回车键退出...")
    sys.exit(1)

# 创建数据目录
os.makedirs(os.path.join("data", "raw"), exist_ok=True)

# 解析命令行参数
args = sys.argv[1:]

if "--schedule" in args:
    # === 定时调度模式 ===
    print("\n[模式] 定时调度（缅华网 + GDELT + 分析流水线）")
    print("[提示] 按 Ctrl+C 停止\n")

    from data.scheduler import get_scheduler
    scheduler = get_scheduler()
    scheduler.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止...")
        scheduler.stop()
        print("已停止")
        input("\n按回车键退出...")

elif "--gdelt" in args:
    # === 仅 GDELT 查询 ===
    print("\n[模式] 仅 GDELT 全球事件数据库查询")

    try:
        from data.gdelt_crawler import get_gdelt_crawler
        gdelt = get_gdelt_crawler()

        print("\n[1/2] 正在查询 GDELT 事件数据（7天）...")
        news = gdelt.crawl(timespan_days=7)
        filepath = gdelt.save_news(news)

        print(f"\n[2/2] 计算风险指标...")
        metrics = gdelt.get_risk_metrics(timespan_days=7)

        print(f"\n{'=' * 50}")
        print(f"  GDELT 查询完成！")
        print(f"  文章数: {metrics['article_count']}")
        print(f"  冲突数: {metrics['conflict_count']}")
        print(f"  冲突频率: {metrics['conflict_frequency']:.2%}")
        print(f"  平均风险: {metrics['avg_tone_risk']:.2f}")
        print(f"  事件摘要: {metrics['event_summary']}")
        if filepath:
            print(f"  保存至: {os.path.abspath(filepath)}")
        print(f"{'=' * 50}")

    except Exception as e:
        print(f"\n[错误] GDELT 查询失败: {e}")
        print("请检查网络连接后重试。")

    input("\n按回车键退出...")

elif "--rss" in args:
    # === 仅 RSS 新闻源 ===
    print("\n[模式] RSS 新闻源爬取（Frontier Myanmar + DVB）")

    try:
        from data.rss_crawler import get_rss_crawler
        rss = get_rss_crawler()
        news = rss.crawl_all()
        filepath = rss.save_news(news)

        print(f"\n{'=' * 50}")
        print(f"  RSS 爬取完成！")
        print(f"  共获取: {len(news)} 条新闻")
        if filepath:
            print(f"  保存至: {os.path.abspath(filepath)}")
        print(f"{'=' * 50}")

        if news:
            print("\n新闻列表预览：")
            for i, item in enumerate(news[:15]):
                title = item.get("title", "无标题")[:50]
                source = item.get("source", "未知")
                print(f"  {i+1:2d}. [{source}] {title}")
            if len(news) > 15:
                print(f"  ... 共 {len(news)} 条")

    except Exception as e:
        print(f"\n[错误] RSS 爬取失败: {e}")
        print("请检查网络连接后重试。")

    input("\n按回车键退出...")

else:
    # === 单次爬取模式（所有数据源） ===
    try:
        all_news = []

        # 1. 缅华网
        print("\n[1/4] 爬取缅华网...")
        from data.crawler import NewsCrawler
        crawler = NewsCrawler()
        news = crawler.crawl_all_sources()
        crawler.save_news(news)
        all_news.extend(news)
        print(f"  ✅ 缅华网: {len(news)} 条")

        # 2. 英文媒体
        print("\n[2/4] 爬取英文媒体 (Myanmar Now + Irrawaddy)...")
        try:
            from data.myanmar_now_crawler import get_english_crawler
            en_crawler = get_english_crawler()
            en_news = en_crawler.crawl_all()
            en_crawler.save_news(en_news)
            all_news.extend(en_news)
            print(f"  ✅ 英文媒体: {len(en_news)} 条")
        except Exception as e:
            print(f"  ⚠️ 英文媒体失败: {e}")

        # 3. RSS 源
        print("\n[3/4] 爬取 RSS 新闻源 (Frontier Myanmar + DVB)...")
        try:
            from data.rss_crawler import get_rss_crawler
            rss = get_rss_crawler()
            rss_news = rss.crawl_all()
            rss.save_news(rss_news)
            all_news.extend(rss_news)
            print(f"  ✅ RSS 源: {len(rss_news)} 条")
        except Exception as e:
            print(f"  ⚠️ RSS 源失败: {e}")

        # 4. GDELT
        print("\n[4/4] 查询 GDELT 全球事件数据库...")
        try:
            from data.gdelt_crawler import get_gdelt_crawler
            gdelt = get_gdelt_crawler()
            gdelt_news = gdelt.crawl(timespan_days=7)
            gdelt.save_news(gdelt_news)
            all_news.extend(gdelt_news)
            print(f"  ✅ GDELT: {len(gdelt_news)} 条")
        except Exception as e:
            print(f"  ⚠️ GDELT 失败: {e}")

        print(f"\n{'=' * 50}")
        print(f"  全部爬取完成！共获取: {len(all_news)} 条新闻")
        print(f"  数据保存至: {os.path.abspath(os.path.join('data', 'raw'))}")
        print(f"{'=' * 50}")

        if all_news:
            print("\n新闻列表预览：")
            for i, item in enumerate(all_news[:15]):
                title = item.get("title", "无标题")[:40]
                source = item.get("source", "未知")
                pub_time = item.get("pub_time", "")[:10]
                print(f"  {i+1:2d}. [{source:15s}] [{pub_time}] {title}")
            if len(all_news) > 15:
                print(f"  ... 共 {len(all_news)} 条")

    except Exception as e:
        print(f"\n[错误] 爬虫运行失败: {e}")
        print("请检查网络连接后重试。")

    input("\n按回车键退出...")

