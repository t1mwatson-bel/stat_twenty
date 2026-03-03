import threading
import time
import asyncio
from playwright.async_api import async_playwright

class BrowserPool:
    def __init__(self, size=3):
        self.size = size
        self.browsers = []  # все браузеры
        self.available = []  # свободные
        self.busy = {}       # занятые: {номер_игры: браузер}
        self.lock = threading.Lock()
        self.playwright = None
        self.running = True
    
    async def start(self):
        """Запускает пул и создает браузеры"""
        self.playwright = await async_playwright().start()
        for i in range(self.size):
            browser = await self.playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )
            self.browsers.append(browser)
            self.available.append(browser)
        print(f"✅ Запущено {self.size} браузеров")
    
    async def get_browser(self, game_number):
        """Получить свободный браузер для игры"""
        with self.lock:
            if not self.available:
                return None
            browser = self.available.pop()
            self.busy[game_number] = browser
            return browser
    
    async def release_browser(self, game_number):
        """Освободить браузер после игры"""
        with self.lock:
            if game_number in self.busy:
                browser = self.busy.pop(game_number)
                self.available.append(browser)
                print(f"🔄 Браузер для игры #{game_number} освобожден")
    
    async def stop_all(self):
        """Закрыть все браузеры при остановке"""
        self.running = False
        for browser in self.browsers:
            await browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("🛑 Все браузеры закрыты")