const { chromium } = require('playwright');
const TelegramBot = require('node-telegram-bot-api');
const fs = require('fs');

const TOKEN = '8357635747:AAEn0aob4h7mqrbkSITlyd0iYLcprqeCSc4';
const CHAT = '-1003477065559';
const URL = 'https://1xlite-7636770.bar/ru/live/twentyone';
const LAST_NUMBER_FILE = './last_number.txt';

const bot = new TelegramBot(TOKEN, { polling: false });

let lastMessageId = null;
let lastMessageText = '';

// Загружаем последний номер из файла
let lastGameNumber = '0';
if (fs.existsSync(LAST_NUMBER_FILE)) {
    lastGameNumber = fs.readFileSync(LAST_NUMBER_FILE, 'utf8');
    console.log('Загружен последний номер:', lastGameNumber);
}

function formatCards(cards) {
    return cards.join('');
}

// Определение победителя для Twenty One
function determineWinner(playerScore, bankerScore) {
    const p = parseInt(playerScore);
    const b = parseInt(bankerScore);
    
    // Если у игрока перебор (>21) - он проиграл
    if (p > 21 && b <= 21) return 'П2';
    if (b > 21 && p <= 21) return 'П1';
    if (p > 21 && b > 21) return 'X'; // оба перебор - ничья
    
    // Обычное сравнение
    if (p > b) return 'П1';
    if (b > p) return 'П2';
    return 'X';
}

async function sendOrEditTelegram(newMessage) {
    if (!newMessage || newMessage === lastMessageText) return;
    
    try {
        if (lastMessageId) {
            await bot.editMessageText(newMessage, {
                chat_id: CHAT,
                message_id: lastMessageId
            });
        } else {
            const msg = await bot.sendMessage(CHAT, newMessage);
            lastMessageId = msg.message_id;
        }
        lastMessageText = newMessage;
    } catch (e) {
        console.log('TG error:', e.message);
    }
}

// Функция для получения второго активного стола
async function getSecondTableLink(page) {
    const games = await page.$$('li.dashboard-champ__game');
    const activeGames = [];
    
    for (const game of games) {
        const hasTimer = await game.$('.dashboard-game-info__time') !== null;
        const isFinished = await game.evaluate(el => {
            const period = el.querySelector('.dashboard-game-info__period');
            return period && period.textContent.includes('Игра завершена');
        });
        
        // Сохраняем живые игры
        if (hasTimer && !isFinished) {
            const link = await game.$('a[href*="/ru/live/twentyone/"]');
            if (link) {
                const href = await link.getAttribute('href');
                activeGames.push(href);
            }
        }
    }
    
    // Возвращаем второй стол, если он есть
    if (activeGames.length >= 2) {
        console.log(`Найдено столов: ${activeGames.length}, берём второй`);
        return activeGames[1];
    } else if (activeGames.length === 1) {
        console.log('Найден только один стол, берём его');
        return activeGames[0];
    }
    
    return null;
}

async function getCards(page) {
    // Данные игрока (первый)
    const playerScore = await page.$eval('.live-twenty-one-field__player:first-child .live-twenty-one-field-score__label', 
        el => el.textContent.trim()
    ).catch(() => '0');

    const playerCards = await page.$$eval('.live-twenty-one-field__player:first-child .live-twenty-one-cards__item', 
        cards => cards.map(c => {
            const suitClass = Array.from(c.classList).find(cls => cls.includes('suit-'));
            const suitMap = {
                'suit-0': '♠️',
                'suit-1': '♥️',
                'suit-2': '♣️',
                'suit-3': '♦️'
            };
            const suit = suitMap[suitClass] || '';
            
            const valueClass = Array.from(c.classList).find(cls => cls.includes('value-'));
            let value = valueClass ? valueClass.split('-').pop() : '';
            
            const valueMap = {
                '1': 'A', '2': '2', '3': '3', '4': '4', '5': '5',
                '6': '6', '7': '7', '8': '8', '9': '9', '10': '10',
                '11': 'J', '12': 'Q', '13': 'K'
            };
            value = valueMap[value] || value;
            
            return value + suit;
        }).filter(c => c.length > 1)
    ).catch(() => []);

    // Данные дилера (второй)
    const bankerScore = await page.$eval('.live-twenty-one-field__player:last-child .live-twenty-one-field-score__label', 
        el => el.textContent.trim()
    ).catch(() => '0');

    const bankerCards = await page.$$eval('.live-twenty-one-field__player:last-child .live-twenty-one-cards__item', 
        cards => cards.map(c => {
            const suitClass = Array.from(c.classList).find(cls => cls.includes('suit-'));
            const suit = suitMap[suitClass] || '';
            
            const valueClass = Array.from(c.classList).find(cls => cls.includes('value-'));
            let value = valueClass ? valueClass.split('-').pop() : '';
            
            value = valueMap[value] || value;
            
            return value + suit;
        }).filter(c => c.length > 1)
    ).catch(() => []);

    return { player: playerCards, banker: bankerCards, pScore: playerScore, bScore: bankerScore };
}

async function monitorGame(page, gameNumber) {
    let lastCards = { player: [], banker: [], pScore: '0', bScore: '0' };
    
    while (true) {
        const cards = await getCards(page);
        
        // Проверка на завершение игры
        const isGameOver = await page.evaluate(() => {
            const timer = document.querySelector('.live-twenty-one-table-footer__timer .ui-game-timer__label');
            return timer && timer.textContent.includes('Игра завершена');
        });
        
        if (isGameOver) {
            const cards = await getCards(page);
            
            if (cards.player.length > 0 || cards.banker.length > 0) {
                console.log('Игра завершена, отправляю результат...');
                
                const total = parseInt(cards.pScore) + parseInt(cards.bScore);
                const winner = determineWinner(cards.pScore, cards.bScore);
                
                let message;
                if (winner === 'П1') {
                    message = `#N${gameNumber} ✅${cards.pScore} (${formatCards(cards.player)}) - ${cards.bScore} (${formatCards(cards.banker)}) #${winner} #T${total}`;
                } else if (winner === 'П2') {
                    message = `#N${gameNumber} ${cards.pScore} (${formatCards(cards.player)}) - ✅${cards.bScore} (${formatCards(cards.banker)}) #${winner} #T${total}`;
                } else {
                    message = `#N${gameNumber} ${cards.pScore} (${formatCards(cards.player)}) 🔰 ${cards.bScore} (${formatCards(cards.banker)}) #${winner} #T${total}`;
                }
                
                await sendOrEditTelegram(message);
            }
            
            console.log('Жду 10 секунд перед закрытием...');
            await page.waitForTimeout(10000);
            break;
        }
        
        if (cards.player.length > 0 && cards.banker.length > 0) {
            // Для Twenty One показываем просто текущий счёт
            const message = `⏱№${gameNumber} ${cards.pScore} (${formatCards(cards.player)}) - ${cards.bScore} (${formatCards(cards.banker)})`;
            
            const cardsChanged = 
                JSON.stringify(cards.player) !== JSON.stringify(lastCards.player) ||
                JSON.stringify(cards.banker) !== JSON.stringify(lastCards.banker) ||
                cards.pScore !== lastCards.pScore ||
                cards.bScore !== lastCards.bScore;
            
            if (cardsChanged) {
                await sendOrEditTelegram(message);
                lastCards = { ...cards };
            }
        }
        
        await page.waitForTimeout(2000);
    }
}

async function run() {
    let browser;
    let timeout;
    
    try {
        const startTime = new Date();
        console.log(`🟢 Браузер открыт в ${startTime.toLocaleTimeString()}.${startTime.getMilliseconds()}`);
        
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        
        timeout = setTimeout(async () => {
            console.log(`⏱ 2 минуты прошло, закрываю браузер`);
            if (browser) await browser.close();
        }, 120000);
        
        await page.goto(URL);
        console.log('Ищем второй активный стол...');
        
        // Ждем появления столов
        await page.waitForTimeout(5000);
        
        const activeLink = await getSecondTableLink(page);
        
        if (!activeLink) {
            console.log('Не найдено активных столов');
            return;
        }
        
        console.log('Заходим в стол:', activeLink);
        await page.click(`a[href="${activeLink}"]`);
        await page.waitForTimeout(5000);
        
        // Получаем номер игры
        let gameNumber = await page.evaluate(() => {
            const el = document.querySelector('.dashboard-game-info__additional-info');
            return el ? el.textContent.trim() : null;
        });
        
        if (!gameNumber) {
            gameNumber = (parseInt(lastGameNumber) + 1).toString();
            console.log('Номер не найден, присваиваю:', gameNumber);
        } else {
            console.log('Номер игры:', gameNumber);
        }
        
        lastGameNumber = gameNumber;
        fs.writeFileSync(LAST_NUMBER_FILE, gameNumber);
        
        // Ждем появления карт
        let attempts = 0;
        let cards = { player: [], banker: [] };
        while (attempts < 12 && (cards.player.length === 0 || cards.banker.length === 0)) {
            await page.waitForTimeout(5000);
            cards = await getCards(page);
            attempts++;
        }
        
        if (cards.player.length > 0 && cards.banker.length > 0) {
            await monitorGame(page, gameNumber);
        }
        
    } catch (e) {
        console.log('❌ Ошибка:', e.message);
    } finally {
        if (timeout) clearTimeout(timeout);
        if (browser) {
            await browser.close();
            lastMessageId = null;
            lastMessageText = '';
        }
    }
}

// Синхронизация с :02 секунд как в баккаре
function getDelayToNextGame() {
    const now = new Date();
    const seconds = now.getSeconds();
    const milliseconds = now.getMilliseconds();
    const targetSeconds = 2;
    
    let delaySeconds;
    if (seconds < targetSeconds) {
        delaySeconds = targetSeconds - seconds;
    } else {
        delaySeconds = (60 - seconds) + targetSeconds;
    }
    
    return (delaySeconds * 1000) - milliseconds;
}

// Запуск
(async () => {
    console.log('🤖 Бот Twenty One запущен');
    console.log('🎯 Всегда берём второй стол в списке');
    
    const initialDelay = getDelayToNextGame();
    console.log(`⏱ Первый запуск через ${(initialDelay/1000).toFixed(3)} секунд`);
    
    await new Promise(resolve => setTimeout(resolve, initialDelay));
    
    console.log('✅ Запуск каждые 60 секунд');
    
    while (true) {
        const now = new Date();
        console.log(`\n🚀 Запуск в ${now.toLocaleTimeString()}.${now.getMilliseconds()}`);
        run();
        await new Promise(resolve => setTimeout(resolve, 60000));
    }
})();