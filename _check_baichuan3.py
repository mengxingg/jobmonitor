"""检查百川智能 - 点击社会招聘看跳转"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://www.baichuan-ai.com/', timeout=30000, wait_until='domcontentloaded')
    time.sleep(3)
    
    # 尝试点击"社会招聘"文本
    try:
        el = page.query_selector('text=社会招聘')
        if el:
            print(f"找到社会招聘元素: tag={el.evaluate('el => el.tagName')}, class={el.evaluate('el => el.className')}")
            # 检查父元素是否有链接
            parent_link = el.evaluate('el => el.closest("a") ? el.closest("a").href : null')
            print(f"父链接: {parent_link}")
    except Exception as e:
        print(f"查找社会招聘失败: {e}")
    
    # 尝试访问可能的招聘子域名
    test_urls = [
        'https://www.baichuan-ai.com/jobs',
        'https://www.baichuan-ai.com/careers',
        'https://www.baichuan-ai.com/join',
        'https://www.baichuan-ai.com/recruit',
        'https://www.baichuan-ai.com/hr',
        'https://baichuan.zhiye.com',
        'https://app.mokahr.com/social-recruitment/baichuan',
    ]
    for url in test_urls:
        try:
            resp = page.goto(url, timeout=10000, wait_until='domcontentloaded')
            time.sleep(1)
            title = page.title()
            status = resp.status if resp else 'N/A'
            print(f"[{status}] {url} -> {title[:60]}")
        except Exception as e:
            print(f"[ERR] {url} -> {str(e)[:60]}")
    
    browser.close()
