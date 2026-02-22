const { chromium } = require('playwright');
const TelegramBot = require('node-telegram-bot-api');
const fs = require('fs');

const TOKEN = '8357635747:AAEn0aob4h7mqrbkSITlyd0iYLcprqeCSc4';
const CHAT = '-1003477065559';
const URL = 'https://1xlite-7636770.bar/ru/live/twentyone';
const LAST_NUMBER_FILE = './last_number_twentyone.txt';

const bot = new TelegramBot(TOKEN, { polling: false });

let lastMessageId = null;
let lastMessageText = '';

// Загружаем последний номер из файла
let lastGameNumber = '0';
if (fs.existsSync(LAST_NUMBER_FILE)) {
    lastGameNumber = fs.readFileSync(LAST_NUMBER_FILE, 'utf8');
    console.log('Загружен последний номер Twenty One:', lastGameNumber);
}

function formatCards(cards) {
    return cards.join('');
}

function getCardValue(value) {
    const valueMap = {
        '1': 'A', '2': '2', '3': '3', '4': '4', '5': '5',
        '6': '6', '7': '7', '8': '8', '9': '9', '10': '10',
        '11': 'J', '12': 'Q', '13': 'K'
    };
    return valueMap[value] || value;
}

function getSuit(suitClass) {
    const suitMap = {
        'suit-0': '♠️',
        'suit-1': '♥️', 
        'suit-2': '♣️',
        'suit-3': '♦️'
    };
    return suitMap[suitClass] || '';
}

function determineWinner(playerScore, bankerScore) {
    const p = parseInt(playerScore);
    const b = parseInt(bankerScore);
    
    // Проверка на перебор (>21)
    if (p > 21 && b <= 21) return 'П2';
    if (b > 21 && p <= 21) return 'П1';
    if (p > 21 && b > 21) return 'X'; // оба перебор
    
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

async function checkTables(page) {
    const games = await page.$$('li.dashboard-champ__game');
    console.log(`Найдено столов: ${games.length}`);
    
    let activeGames = [];
    
    for (let i = 0; i < games.length; i++) {
        const game = games[i];
        
        // Проверяем есть ли таймер (игра активна)
        const hasTimer = await game.$('.dashboard-game-info__time') !== null;
        
        // Проверяем не завершена ли игра
        const isFinished = await game.evaluate(el => {
            const period = el.querySelector('.dashboard-game-info__period');
            return period && period.textContent.includes('Игра завершена');
        });
        
        // Берем только активные незавершенные игры
        if (hasTimer && !isFinished) {
            const link = await game.$('a[href*="/ru/live/twentyone/"]');
            if (link) {
                const href = await link.getAttribute('href');
                const gameNumber = await game.$eval('.dashboard-game-info__additional-info', 
                    el => el.textContent.trim()
                ).catch(() => '0');
                
                activeGames.push({ 
                    index: i, 
                    href, 
                    number: gameNumber 
                });
                
                console.log(`✅ Стол ${i+1}: номер ${gameNumber}`);
            }
        }
    }
    
    // Берем ВТОРОЙ активный стол (индекс 1)
    if (activeGames.length >= 2) {
        console.log(`🎯 Беру второй стол (номер ${activeGames[1].number})`);
        return activeGames[1].href;
    }
    
    // Если меньше двух - берем первый
    if (activeGames.length === 1) {
        console.log(`⚠️ Только один активный стол, беру его`);
        return activeGames[0].href;
    }
    
    console.log('❌ Активных столов не найдено');
    return null;
}

async function getCards(page) {
    // Данные игрока (первый игрок)
    const playerScore = await page.$eval('.live-twenty-one-field__player:first-child .live-twenty-one-field-score__label', 
        el => el.textContent.trim()
    ).catch(() => '0');
    
    const playerCards = await page.$$eval('.live-twenty-one-field__player:first-child .live-twenty-one-cards__item', 
        cards => cards.map(c => {
            const suitClass = Array.from(c.classList).find(cls => cls.includes('suit-'));
            const valueClass = Array.from(c.classList).find(cls => cls.includes('value-'));
            
            const suit = getSuit(suitClass);
            let value = valueClass ? valueClass.split('-').pop() : '';
            value = getCardValue(value);
            
            return value + suit;
        })
    ).catch(() => []);
    
    // Данные дилера (второй игрок)
    const bankerScore = await page.$eval('.live-twenty-one-field__player:last-child .live-twenty-one-field-score__label', 
        el => el.textContent.trim()
    ).catch(() => '0');
    
    const bankerCards = await page.$$eval('.live-twenty-one-field__player:last-child .live-twenty-one-cards__item', 
        cards => cards.map(c => {
            const suitClass = Array.from(c.classList).find(cls => cls.includes('suit-'));
            const valueClass = Array.from(c.classList).find(cls => cls.includes('value-'));
            
            const suit = getSuit(suitClass);
            let value = valueClass ? valueClass.split('-').pop() : '';
            value = getCardValue(value);
            
            return value + suit;
        })
    ).catch(() => []);
    
    // Статус игры (чей ход)
    const status = await page.$eval('.scoreboard-card-games-board-status', 
        el => el.textContent.trim()
    ).catch(() => '');
    
    return {
        player: playerCards,
        banker: bankerCards,
        pScore: playerScore,
        bScore: bankerScore,
        status: status
    };
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
            
            console.log('Жду 10 секунд перед закрытием...');
            await page.waitForTimeout(10000);
            break;
        }
        
        // Отправляем промежуточные обновления
        if (cards.player.length > 0 || cards.banker.length > 0) {
            const message = `⏱№${gameNumber} ${cards.pScore} (${formatCards(cards.player)}) - ${cards.bScore} (${formatCards(cards.banker)}) ${cards.status ? '[' + cards.status + ']' : ''}`;
            
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
        console.log(`\n🟢 Браузер открыт в ${startTime.toLocaleTimeString()}.${startTime.getMilliseconds()}`);
        
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        
        timeout = setTimeout(async () => {
            console.log(`⏱ 2 минуты прошло, закрываю браузер`);
            if (browser) await browser.close();
        }, 120000);
        
        await page.goto(URL);
        console.log('Проверяем столы Twenty One...');
        
        // Ищем активный стол
        let activeLink = null;
        let attempts = 0;
        while (!activeLink && attempts < 10) {
            activeLink = await checkTables(page);
            if (!activeLink) {
                console.log('Жду 5 секунд...');
                await page.waitForTimeout(5000);
                attempts++;
            }
        }
        
        if (!activeLink) {
            console.log('❌ Не нашел активный стол за 10 попыток');
            return;
        }
        
        console.log('Нашли активный стол:', activeLink);
        await page.click(`a[href="${activeLink}"]`);
        await page.waitForTimeout(3000);
        
        // Получаем номер игры
        let gameNumber = await page.evaluate(() => {
            const el = document.querySelector('.dashboard-game-info__additional-info');
            return el ? el.textContent.trim() : null;
        }).catch(() => null);
        
        if (!gameNumber) {
            gameNumber = (parseInt(lastGameNumber) + 1).toString();
            console.log('⚠️ Номер не найден, присваиваю:', gameNumber);
        } else {
            console.log('🎰 Номер игры:', gameNumber);
        }
        
        // Сохраняем номер
        lastGameNumber = gameNumber;
        fs.writeFileSync(LAST_NUMBER_FILE, gameNumber);
        
        // Ждем появления карт
        let cardsAttempts = 0;
        let cards = { player: [], banker: [] };
        while (cardsAttempts < 12 && (cards.player.length === 0 || cards.banker.length === 0)) {
            await page.waitForTimeout(5000);
            cards = await getCards(page);
            cardsAttempts++;
        }
        
        if (cards.player.length > 0 || cards.banker.length > 0) {
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

// Функция для расчета задержки до запуска
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
    console.log('🎯 Беру второй активный стол');
    
    const initialDelay = getDelayToNextGame();
    console.log(`⏱ Первый запуск через ${(initialDelay/1000).toFixed(3)} секунд`);
    
    await new Promise(resolve => setTimeout(resolve, initialDelay));
    console.log('✅ Запуск каждые 60 секунд');
    
    while (true) {
        const now = new Date();
        console.log(`\n🚀 Запуск в ${now.toLocaleTimeString()}.${now.getMilliseconds()}`);
        await run();
        await new Promise(resolve => setTimeout(resolve, 60000));
    }
})();
