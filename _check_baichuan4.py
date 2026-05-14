"""检查百川智能 - 点击社会招聘按钮"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://www.baichuan-ai.com/', timeout=30000, wait_until='domcontentloaded')
    time.sleep(3)
    
    # 点击社会招聘按钮
    btn = page.query_selector('button.join-button')
    if btn:
        print("点击社会招聘按钮...")
        btn.click()
        time.sleep(3)
        
        # 检查是否有新页面打开
        print(f"当前页面: {page.url}")
        print(f"页面标题: {page.title()}")
        
        # 检查是否有弹窗
        dialogs = page.query_selector_all('[class*="dialog"], [class*="modal"], [class*="popup"]')
        print(f"弹窗数量: {len(dialogs)}")
        
        # 检查页面新内容
        body = page.inner_text('body')
        for line in body.split('\n'):
            line = line.strip()
            if any(k in line.lower() for k in ['招聘','加入','career','job','join','recruit','talent','hr','zhaopin']):
                print(f'  {line[:120]}')
    
    # 尝试点击校园招聘
    btn2 = page.query_selector('text=校园招聘')
    if btn2:
        print(f"\n校园招聘元素: tag={btn2.evaluate('el => el.tagName')}")
    
    browser.close()
