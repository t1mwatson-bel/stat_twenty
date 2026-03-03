import asyncio
import logging
from playwright.async_api import async_playwright
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

TABLE_URL = "https://1xlite-7636770.bar/ru/live/twentyone/2092323-21-classics"

async def test_see_cards():
    """Просто проверяет, видит ли карты"""
    
    logging.info("="*50)
    logging.info("ТЕСТ: проверка видимости карт")
    logging.info("="*50)
    
    async with async_playwright() as p:
        # Запускаем браузер
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )
        page = await browser.new_page()
        
        # Идем на страницу
        logging.info(f"Открываю URL: {TABLE_URL}")
        await page.goto(TABLE_URL, timeout=30000)
        
        # Ждем загрузки
        await page.wait_for_timeout(5000)
        
        # Ищем карты
        logging.info("Ищу карты...")
        cards = await page.query_selector_all('.scoreboard-card-games-card')
        logging.info(f"Найдено карт: {len(cards)}")
        
        # Ищем счет
        scores = await page.query_selector_all('.live-twenty-one-field-score__label')
        logging.info(f"Найдено счетов: {len(scores)}")
        
        for i, score in enumerate(scores):
            text = await score.text_content()
            logging.info(f"Счет {i+1}: {text}")
        
        # Делаем скриншот
        await page.screenshot(path="test.png")
        logging.info("Скриншот сохранен как test.png")
        
        # Выводим заголовок страницы
        title = await page.title()
        logging.info(f"Заголовок страницы: {title}")
        
        await browser.close()
        
    logging.info("="*50)
    logging.info("ТЕСТ ЗАВЕРШЕН")
    logging.info("="*50)

async def main():
    await test_see_cards()

if __name__ == "__main__":
    asyncio.run(main())