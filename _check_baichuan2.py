"""检查百川智能首页的招聘链接 - 详细版"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://www.baichuan-ai.com/', timeout=30000, wait_until='domcontentloaded')
    time.sleep(5)
    
    # 获取所有链接（完整列表）
    all_links = page.eval_on_selector_all('a', "els => els.map(el => ({href: el.href, text: el.innerText.trim()}))")
    print("=== 所有链接 ===")
    for l in all_links:
        if l['href'] and l['href'] != '#' and not l['href'].startswith('javascript'):
            print(f'  [{l["text"]:20s}] -> {l["href"]}')
    
    print("\n=== 页面文本（含招聘关键词）===")
    body = page.inner_text('body')
    for line in body.split('\n'):
        line = line.strip()
        if any(k in line.lower() for k in ['招聘','加入','career','job','join','recruit','talent','hr']):
            print(f'  {line[:120]}')
    
    browser.close()
