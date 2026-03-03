import asyncio
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)

async def main():
    print("="*50)
    print("ТЕСТ: Просто открыть страницу")
    print("="*50)
    
    async with async_playwright() as p:
        # Запускаем браузер
        browser = await p.chromium.launch(headless=False)  # НЕ headless!
        page = await browser.new_page()
        
        # Идем на страницу
        url = "https://1xlite-7636770.bar/ru/live/twentyone/2092323-21-classics"
        print(f"Открываю: {url}")
        await page.goto(url)
        
        # Ждем 30 секунд и смотрим
        print("Жду 30 секунд... СМОТРИ НА ЭКРАН!")
        await page.wait_for_timeout(30000)
        
        # Сохраняем скрин
        await page.screenshot(path="test.png")
        print("Скриншот сохранен")
        
        await browser.close()

asyncio.run(main())