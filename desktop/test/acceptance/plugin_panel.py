"""Exercise an isolated, model-configured Electron renderer via its Chromium debugging port."""
import argparse
import time
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
FILES = ROOT / "examples/plugins"
TITLE = "文献证据助手"


def exercise(page):
    panel_requests = []
    page.on("response", lambda r: panel_requests.append(r.url) if "/panels/evidence-assistant/evidence?" in r.url and r.ok else None)
    page.get_by_role("button", name="新建会话", exact=True).click()
    page.get_by_placeholder("输入工作区名称").fill(f"panel-test-{time.time_ns()}")
    page.get_by_role("button", name="添加", exact=True).click()
    page.get_by_role("button", name="创建任务", exact=True).click()
    page.get_by_role("button", name="扩展", exact=True).click()
    nav = page.get_by_role("navigation", name="功能导航")
    nav.get_by_role("button", name="插件", exact=True).click()
    page.get_by_role("button", name="安装插件", exact=True).click()
    page.locator("#plugin-package-file").set_input_files(FILES / "文献证据助手.zip")
    page.get_by_role("button", name="安装插件包", exact=True).click()
    page.get_by_role("button", name="加载已安装插件").click()
    page.get_by_role("button", name="同意并加载").click()
    expect(page.get_by_role("dialog")).to_contain_text("当前已加载")
    page.get_by_role("button", name="返回插件列表").click()
    nav.get_by_role("button", name=TITLE, exact=True).click()
    panel = page.frame_locator('iframe[title="文献证据助手"]')
    panel.locator("#paper").set_input_files(FILES / "证据核对示例.pdf")
    panel.locator("#question").fill("分别提供第1页阅读任务完成情况和第2页研究局限的证据，每页一张卡，并逐字引用原文。")
    panel.get_by_role("button", name="查找证据").click()
    expect(panel.locator("blockquote")).to_have_count(2, timeout=240_000)
    original = panel.locator("blockquote").all_inner_texts()
    assert any("18 of 20" in item for item in original)
    assert any("not randomly assigned" in item for item in original)
    for idx in range(2):
        panel.get_by_role("button", name="核对这一条").nth(idx).click()
    expect(panel.get_by_label("条目复核").nth(0)).to_contain_text("复核结果", timeout=240_000)
    expect(panel.get_by_label("条目复核").nth(1)).to_contain_text("复核结果", timeout=240_000)
    page.reload()
    expect(panel.locator("blockquote")).to_have_count(2, timeout=25_000)
    expect(panel.get_by_label("条目复核")).to_have_count(2)
    idx = next(i for i, text in enumerate(original) if "not randomly assigned" in text)
    panel.get_by_role("button", name="带入对话").nth(idx).click()
    composer = page.locator('textarea:visible').first
    expect(composer).to_have_value(re.compile("not randomly assigned"))
    assert "18 of 20" not in composer.input_value()
    expect(page.get_by_text("证据核对示例.pdf", exact=True).first).to_be_visible()
    print("PASS: ZIP 安装、权限确认、文献团队两条证据与逐条复核、刷新恢复、所选证据和原 PDF 带入对话", flush=True)
    page.get_by_role("button", name="扩展", exact=True).click()
    expect(nav.get_by_role("button", name=TITLE, exact=True)).to_be_visible()
    page.get_by_role("button", name="高级配置").click()
    runtime = page.get_by_test_id("harness-runtime-select")
    mode = page.get_by_role("region",name="高级执行配置").get_by_role("combobox").nth(1)
    expect(mode.locator('option[value="evidence_assistant_review"]')).to_have_count(1)
    # Start a review while the primary conversation stays idle. Opening the panel
    # may close the composer popover, so reopen it before selecting the harness.
    panel.get_by_role("button", name="核对这一条").nth(idx).click()
    expect(panel.get_by_label("条目复核").filter(has_text="正在复核")).to_have_count(1)
    if not runtime.is_visible():
        page.get_by_role("button", name="高级配置").click()
    runtime.select_option("opencode")
    expect(page.get_by_role("dialog")).to_contain_text(re.compile(r"[1-9][0-9]* 项插件任务"))
    page.get_by_role("button",name="继续切换").click()
    expect(nav.get_by_role("button", name=TITLE, exact=True)).to_have_count(0)
    expect(page.locator('iframe[title="文献证据助手"]')).to_have_count(0)
    if not runtime.is_visible():
        page.get_by_role("button", name="高级配置").click()
    expect(runtime).to_have_value("opencode")
    expect(mode).to_be_visible()
    expect(mode.locator('option[value="evidence_assistant_review"]')).to_have_count(0)
    assert page.evaluate("async url => (await fetch(url)).status",panel_requests[-1]) == 409
    runtime.select_option("tcode-native")
    expect(runtime).to_have_value("tcode-native")
    page.get_by_role("button", name="高级配置").click()
    nav.get_by_role("button", name=TITLE, exact=True).click()
    expect(panel.get_by_label("条目复核").filter(has_text="复核结果")).to_have_count(3,timeout=240_000)
    print("PASS: Agent 选择器、运行中切换 OpenCode、后端拒绝访问、切回恢复全部运行",flush=True)
    nav.get_by_role("button", name="插件", exact=True).click()
    card = page.get_by_role("article", name="evidence-assistant")
    card.get_by_label("读取你选择的文档").uncheck()
    nav.get_by_role("button", name=TITLE, exact=True).click()
    panel.locator("#paper").set_input_files(FILES / "证据核对示例.pdf")
    panel.locator("#question").fill("再次读取文档")
    panel.get_by_role("button", name="查找证据").click()
    expect(panel.locator("#status")).to_contain_text("授权")
    nav.get_by_role("button", name="插件", exact=True).click()
    card.get_by_role("button", name="卸载", exact=True).click()
    expect(nav.get_by_role("button", name=TITLE, exact=True)).to_have_count(0)
    card.get_by_role("button", name="删除插件").click()
    page.get_by_role("dialog").get_by_role("button", name="删除插件", exact=True).click()
    expect(card).to_have_count(0)
    print("PASS: 撤销授权后拒绝读取、卸载清除入口、删除安装包登记",flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cdp", help="已启动的本地 --test-instance 调试地址")
    url = parser.parse_args().cdp
    if urlsplit(url).hostname not in {"localhost", "127.0.0.1"}:
        parser.error("仅支持本机测试实例")
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(url)
        page = next(p for ctx in browser.contexts for p in ctx.pages if "/desktop.html" in p.url)
        exercise(page)
