"""检查百川智能首页的招聘链接"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://www.baichuan-ai.com/', timeout=30000, wait_until='domcontentloaded')
    time.sleep(3)
    # 获取所有链接
    links = page.eval_on_selector_all('a', "els => els.map(el => ({href: el.href, text: el.innerText.trim()}))")
    for l in links:
        if any(k in (l['text']+l['href']).lower() for k in ['招聘','加入','career','job','join','recruit','talent']):
            print(f'{l["text"]:30s} -> {l["href"]}')
    print('---')
    # 也检查页面文本
    body = page.inner_text('body')
    for line in body.split('\n'):
        if any(k in line.lower() for k in ['招聘','加入我们','career','join us']):
            print(line.strip()[:100])
    browser.close()
