"""Validate built React and Vue examples in a model-configured Electron test instance."""
import argparse
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

EXAMPLES = Path(__file__).resolve().parents[3] / "packages/tcode-panel-sdk/examples"


def exercise(page, profile):
    workspace = f"framework-panels-{time.time_ns()}"
    button = lambda name: page.get_by_role("button", name=name, exact=True)
    button("新建会话").click()
    page.get_by_placeholder("输入工作区名称").fill(workspace)
    button("添加").click()
    button("创建任务").click()
    button("扩展").click()
    nav = page.get_by_role("navigation", name="功能导航")
    matches = list((profile / "runtime/workdirs").glob(f"*/*/{workspace}"))
    assert len(matches) == 1, "找不到本次桌面创建的独立工作区"
    print(f"工作区 {matches[0]}", flush=True)
    for framework, question, answer in [
        ("react", "一盒有17支铅笔，9盒有多少支？只回答阿拉伯数字。", "153"),
        ("vue", "每箱有23本书，7箱一共有多少本？只回答阿拉伯数字。", "161"),
    ]:
        title = framework.title() + " 问题助手"
        shutil.copytree(EXAMPLES / framework / "plugin", matches[0] / framework)
        nav.get_by_role("button", name="插件", exact=True).click()
        button("安装插件").click()
        page.get_by_text("从本地开发目录导入", exact=True).click()
        page.get_by_placeholder("例如 plugins/my-plugin").fill(framework)
        button("登记开发目录").click()
        button("加载已安装插件").click()
        for grant in ("调用本插件的 Agent 团队", "将选中内容带入对话草稿"):
            expect(page.get_by_text(grant, exact=False).last).to_be_visible()
        button("同意并加载").click()
        expect(page.get_by_role("dialog")).to_contain_text("当前已加载")
        button("返回插件列表").click()
        nav.get_by_role("button", name=title, exact=True).click()
        panel = page.frame_locator(f'iframe[title="{title}"]')
        panel.get_by_label("问题").fill(question)
        expect(panel.get_by_text("本地模拟模式")).to_have_count(0)
        panel.get_by_role("button", name="提问").click()
        article = panel.get_by_role("article").filter(has_text=question)
        expect(article.get_by_role("button", name="带入对话")).to_be_visible(timeout=180000)
        result = article.locator("p").inner_text()
        assert answer in result, result
        article.get_by_role("button", name="带入对话").click()
        composer = page.locator("textarea:visible").first
        expect(composer).to_have_value(re.compile(re.escape(result)))
        draft = composer.input_value()
        assert title in draft and "来源 " in draft and "/answer" in draft
        print(f"{framework} 授权、回答、草稿通过：{draft}", flush=True)
        if framework == "react":
            composer.fill("")
            button("扩展").click()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cdp")
    parser.add_argument("profile", type=Path)
    args = parser.parse_args()
    if urlsplit(args.cdp).hostname not in {"localhost", "127.0.0.1"} or not (args.profile / "test-instance.json").is_file():
        parser.error("需要本机独立桌面实例和对应的配置目录")
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(args.cdp)
        page = next(p for ctx in browser.contexts for p in ctx.pages if "/desktop.html" in p.url)
        exercise(page, args.profile)
