from DrissionPage import ChromiumPage, ChromiumOptions
import time

print("启动浏览器进行人工授权...")
# ⚠️ 极其重要：必须和 scraper_drission.py 使用完全相同的用户数据目录！
co = ChromiumOptions()
co.set_user_data_path('./.chrome_profile')

# 取消无头模式，确保能看到界面
co.headless(False)

page = ChromiumPage(co)

# 直接访问 Boss 直聘登录页
print("正在打开 Boss 直聘登录页...")
page.get('https://www.zhipin.com/web/user/?ka=header-login')

# 挂机死等，直到用户在终端按下回车
input("\n🚨 [人工介入请求] 🚨\n请在弹出的浏览器窗口中，使用微信扫码登录。\n当你在浏览器里确认登录成功，并且看到了 Boss 直聘的正常主页后，请回到这个终端界面，按下【回车键 (Enter)】继续...")

print("授权信息已成功写入本地缓存！正在安全关闭浏览器...")
page.quit()
print("✅ 授权完成，你可以重新启动后台爬虫了。")
