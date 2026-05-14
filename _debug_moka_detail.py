"""调试 Moka 详情页的 DOM 结构"""
from playwright.sync_api import sync_playwright
import time, json

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    
    # 访问 DeepSeek 一个岗位详情页
    url = "https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/489eb4c6-91c8-4b2c-a2ea-7b257857722f"
    print(f"访问: {url}")
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    time.sleep(5)
    
    # 检查页面结构
    print("\n=== 页面标题 ===")
    print(page.title())
    
    print("\n=== 所有 class 包含 job/position/detail 的元素 ===")
    elements = page.evaluate("""
        () => {
            const results = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const cls = el.className;
                if (typeof cls === 'string' && 
                    /job|position|detail|content|desc|requirement/i.test(cls)) {
                    results.push({
                        tag: el.tagName,
                        class: cls.slice(0, 100),
                        id: el.id,
                        text_len: el.innerText.trim().length,
                        visible: el.offsetParent !== null
                    });
                }
            }
            return results.slice(0, 50);
        }
    """)
    for e in elements:
        print(f"  <{e['tag']}> .{e['class'][:60]} text={e['text_len']} visible={e['visible']}")
    
    print("\n=== 页面主要文本内容 (前 2000 字符) ===")
    body = page.inner_text('body')
    print(body[:2000])
    
    # 检查 iframe
    print("\n=== iframes ===")
    iframes = page.frames
    print(f"  frames count: {len(iframes)}")
    for i, f in enumerate(iframes):
        print(f"  [{i}] {f.url[:100]}")
    
    browser.close()
