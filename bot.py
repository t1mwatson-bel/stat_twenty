import asyncio
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)

async def main():
    print("="*50)
    print("ТЕСТ: Headless режим")
    print("="*50)
    
    async with async_playwright() as p:
        # Запускаем браузер в headless режиме
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        page = await browser.new_page()
        
        # Идем на страницу
        url = "https://1xlite-7636770.bar/ru/live/twentyone/2092323-21-classics"
        print(f"Открываю: {url}")
        await page.goto(url, timeout=30000)
        
        # Ждем загрузку
        await page.wait_for_timeout(5000)
        
        # Проверяем заголовок
        title = await page.title()
        print(f"Заголовок: {title}")
        
        # Ищем карты
        cards = await page.query_selector_all('.scoreboard-card-games-card')
        print(f"Найдено карт: {len(cards)}")
        
        # Сохраняем скрин
        await page.screenshot(path="test.png")
        print("Скриншот сохранен как test.png")
        
        # Выводим часть HTML для проверки
        html = await page.content()
        print("Первые 500 символов HTML:")
        print(html[:500])
        
        await browser.close()

asyncio.run(main())