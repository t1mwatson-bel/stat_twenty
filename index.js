const { chromium } = require('playwright');
const TelegramBot = require('node-telegram-bot-api');
const fs = require('fs');

const TOKEN = '8357635747:AAEn0aob4h7mqrbkSITlyd0iYLcprqeCSc4';
const CHAT = '-1003477065559';
const URL = 'https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game';
const LAST_NUMBER_FILE = './last_number_twentyone.txt';

const bot = new TelegramBot(TOKEN, { polling: false });

let lastMessageId = null;
let lastMessageText = '';

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
    
    if (p > 21 && b <= 21) return 'П2';
    if (b > 21 && p <= 21) return 'П1';
    if (p > 21 && b > 21) return 'X';
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
        console.log('✅ Сообщение отправлено/обновлено');
    } catch (e) {
        console.log('❌ TG error:', e.message);
        try {
            const msg = await bot.sendMessage(CHAT, newMessage);
            lastMessageId = msg.message_id;
            lastMessageText = newMessage;
            console.log('✅ Отправлено новое сообщение');
        } catch (sendError) {
            console.log('❌ Критическая ошибка TG:', sendError.message);
        }
    }
}

// ===== ПОИСК НИЖНЕГО СТОЛА =====
async function findLastLiveGame(page) {
    console.log('🔍 Ищем столы Twenty One...');
    
    const games = await page.$$('.dashboard-champ__game');
    console.log(`Найдено столов: ${games.length}`);
    
    // Проходим с конца, чтобы найти последний активный стол
    for (let i = games.length - 1; i >= 0; i--) {
        const game = games[i];
        
        const hasTimer = await game.$('.dashboard-game-info__time') !== null;
        const isFinished = await game.evaluate(el => {
            const period = el.querySelector('.dashboard-game-info__period');
            return period && period.textContent.includes('Игра завершена');
        });
        
        console.log(`Стол ${i+1}: таймер=${hasTimer}, завершена=${isFinished}`);
        
        if (hasTimer && !isFinished) {
            const links = await game.$$('a[href*="/ru/live/twentyone/"]');
            let foundLink = null;
            
            for (const link of links) {
                const href = await link.getAttribute('href');
                if (href.includes('-player-dealer')) {
                    foundLink = href;
                    break;
                }
            }
            
            if (foundLink) {
                const gameNumber = await game.$eval('.dashboard-game-info__additional-info', 
                    el => el.textContent.trim()
                ).catch(() => '?');
                
                console.log(`✅ Найден нижний активный стол! Номер: ${gameNumber}`);
                return foundLink;
            }
        }
    }
    
    console.log('❌ Активных столов не найдено');
    return null;
}

// ===== ПОЛУЧЕНИЕ КАРТ (ИСПРАВЛЕНО) =====
async function getCards(page) {
    console.log('🔍 Получаю карты 21 очко...');
    
    // Счет игрока
    const playerScore = await page.$eval('.live-twenty-one-field__player:first-child .live-twenty-one-field-score__label', 
        el => el.textContent.trim()
    ).catch(() => '0');
    
    // Счет дилера
    const bankerScore = await page.$eval('.live-twenty-one-field__player:last-child .live-twenty-one-field-score__label', 
        el => el.textContent.trim()
    ).catch(() => '0');
    
    // Карты игрока
    const playerCards = await page.$$eval('.live-twenty-one-field__player:first-child .live-twenty-one-cards .scoreboard-card-games-card', 
        cards => cards.map(c => {
            const classList = c.className;
            const suitMatch = classList.match(/suit-(\d)/);
            const valueMatch = classList.match(/value-(\d+)/);
            
            const suitMap = { '0': '♠️', '1': '♥️', '2': '♣️', '3': '♦️' };
            const valueMap = { '1': 'A', '11': 'J', '12': 'Q', '13': 'K' };
            
            const suit = suitMap[suitMatch?.[1]] || '';
            let value = valueMatch?.[1] || '';
            value = valueMap[value] || value;
            
            return value + suit;
        })
    ).catch(() => []);
    
    // Карты дилера
    const bankerCards = await page.$$eval('.live-twenty-one-field__player:last-child .live-twenty-one-cards .scoreboard-card-games-card', 
        cards => cards.map(c => {
            const classList = c.className;
            const suitMatch = classList.match(/suit-(\d)/);
            const valueMatch = classList.match(/value-(\d+)/);
            
            const suitMap = { '0': '♠️', '1': '♥️', '2': '♣️', '3': '♦️' };
            const valueMap = { '1': 'A', '11': 'J', '12': 'Q', '13': 'K' };
            
            const suit = suitMap[suitMatch?.[1]] || '';
            let value = valueMatch?.[1] || '';
            value = valueMap[value] || value;
            
            return value + suit;
        })
    ).catch(() => []);
    
    // Статус игры
    const status = await page.$eval('.scoreboard-card-games-board-status', 
        el => el.textContent.trim()
    ).catch(() => '');
    
    console.log(`📊 Счет: ${playerScore}:${bankerScore}, Карты игрока: ${playerCards.length}, Карты дилера: ${bankerCards.length}`);
    
    return {
        player: playerCards,
        banker: bankerCards,
        pScore: playerScore,
        bScore: bankerScore,
        status: status
    };
}

// ===== МОНИТОРИНГ ИГРЫ =====
async function monitorGame(page, gameNumber) {
    console.log(`🎮 Мониторинг игры #${gameNumber}`);
    
    let lastCards = { player: [], banker: [], pScore: '0', bScore: '0' };
    let lastMessageText = '';
    let emptyCount = 0;
    
    while (true) {
        const cards = await getCards(page);
        
        // Если карты пустые, но не прошло много времени - ждем
        if (cards.player.length === 0 && cards.banker.length === 0 && cards.pScore === '0' && cards.bScore === '0') {
            emptyCount++;
            console.log(`⏳ Ожидание карт... (${emptyCount}/10)`);
            
            if (emptyCount < 10) {
                await page.waitForTimeout(2000);
                continue;
            }
        }
        
        const gameStatus = await page.$eval('.scoreboard-card-games-board-status', 
            el => el.textContent.trim()
        ).catch(() => '');
        
        const isGameOver = await page.evaluate(() => {
            const timer = document.querySelector('.live-twenty-one-table-footer__timer .ui-game-timer__label');
            return timer && timer.textContent.includes('Игра завершена');
        }).catch(() => false);
        
        if (isGameOver || gameStatus.includes('Победа')) {
            console.log('🏁 Игра завершена');
            
            const total = parseInt(cards.pScore) + parseInt(cards.bScore);
            const p = parseInt(cards.pScore);
            const b = parseInt(cards.bScore);
            
            let winner = 'X';
            if (p > 21 && b <= 21) winner = 'П2';
            else if (b > 21 && p <= 21) winner = 'П1';
            else if (p > 21 && b > 21) winner = 'X';
            else if (p > b) winner = 'П1';
            else if (b > p) winner = 'П2';
            
            let flags = [`#T${total}`];
            if (p > 21) flags.push('#O');
            if (b > 21) flags.push('#O');
            if (p === 21 || b === 21) flags.push('#G');
            flags.push(`#${winner}`);
            
            const message = `#N${gameNumber}. ${cards.pScore}(${formatCards(cards.player)}) - ${cards.bScore}(${formatCards(cards.banker)}) ${flags.join(' ')}`;
            
            await sendOrEditTelegram(message);
            
            try {
                await page.waitForTimeout(10000);
            } catch (e) {}
            break;
        }
        
        // Отправляем при любом изменении
        const cardsChanged = 
            cards.pScore !== lastCards.pScore ||
            cards.bScore !== lastCards.bScore ||
            cards.player.length !== lastCards.player.length ||
            cards.banker.length !== lastCards.banker.length;
        
        if (cardsChanged && (cards.player.length > 0 || cards.banker.length > 0 || cards.pScore !== '0' || cards.bScore !== '0')) {
            let arrow = '';
            if (gameStatus.includes('Ход игрока')) arrow = '▶';
            else if (gameStatus.includes('Ход дилера')) arrow = '◀';
            
            let message;
            if (arrow === '▶') {
                message = `⏰#N${gameNumber}. ▶ ${cards.pScore}(${formatCards(cards.player)}) - ${cards.bScore}(${formatCards(cards.banker)})`;
            } else if (arrow === '◀') {
                message = `⏰#N${gameNumber}. ${cards.pScore}(${formatCards(cards.player)}) - ▶ ${cards.bScore}(${formatCards(cards.banker)})`;
            } else {
                message = `⏰#N${gameNumber}. ${cards.pScore}(${formatCards(cards.player)}) - ${cards.bScore}(${formatCards(cards.banker)})`;
            }
            
            if (message !== lastMessageText) {
                console.log(`📤 Отправка: ${message}`);
                await sendOrEditTelegram(message);
                lastMessageText = message;
                lastCards = { ...cards };
                emptyCount = 0;
            }
        }
        
        await page.waitForTimeout(2000);
    }
}

// ===== ОСНОВНАЯ ФУНКЦИЯ =====
async function run() {
    let browser;
    let timeout;
    const startTime = Date.now();
    
    try {
        console.log(`\n🟢 Браузер открыт в ${new Date().toLocaleTimeString()}.${new Date().getMilliseconds()}`);
        
        browser = await chromium.launch({ 
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        });
        
        const page = await browser.newPage();
        
        timeout = setTimeout(async () => {
            console.log(`⏱ 6 минут прошло, закрываю браузер`);
            if (browser && browser.isConnected()) {
                await browser.close().catch(() => {});
            }
        }, 360000);
        
        await page.goto(URL, { timeout: 30000 }).catch(e => {
            console.log('❌ Ошибка загрузки страницы:', e.message);
            return;
        });
        
        // Ищем нижний активный стол
        let activeLink = null;
        let attempts = 0;
        while (!activeLink && attempts < 10) {
            if (page.isClosed()) break;
            activeLink = await findLastLiveGame(page).catch(() => null);
            if (!activeLink) {
                console.log('Жду 5 секунд...');
                await page.waitForTimeout(5000).catch(() => {});
                attempts++;
            }
        }
        
        if (!activeLink || page.isClosed()) {
            console.log('❌ Не нашел активный стол за 10 попыток');
            return;
        }
        
        console.log('Нашли нижний активный стол:', activeLink);
        await page.click(`a[href="${activeLink}"]`).catch(() => {});
        
        // Ждем загрузки страницы игры
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
        
        lastGameNumber = gameNumber;
        fs.writeFileSync(LAST_NUMBER_FILE, gameNumber);
        console.log('💾 Номер сохранен');
        
        // Начинаем мониторинг
        await monitorGame(page, gameNumber);
        
    } catch (e) {
        console.log('❌ Ошибка:', e.message);
    } finally {
        if (timeout) clearTimeout(timeout);
        if (browser && browser.isConnected()) {
            await browser.close().catch(() => {});
            console.log(`🔴 Браузер закрыт в ${new Date().toLocaleTimeString()}.${new Date().getMilliseconds()}, прожил ${(Date.now() - startTime)/1000} сек`);
            lastMessageId = null;
            lastMessageText = '';
        }
    }
}

// ===== ЗАДЕРЖКА ДО :58 =====
function getDelayTo58() {
    const now = new Date();
    const seconds = now.getSeconds();
    const milliseconds = now.getMilliseconds();
    const targetSeconds = 58;
    
    let delaySeconds;
    if (seconds < targetSeconds) {
        delaySeconds = targetSeconds - seconds;
    } else {
        delaySeconds = (60 - seconds) + targetSeconds;
    }
    
    return (delaySeconds * 1000) - milliseconds;
}

// ===== ЗАПУСК =====
(async () => {
    console.log('🤖 Бот Twenty One запущен');
    console.log('🎯 Захожу в НИЖНИЙ активный стол');
    console.log('⏱ Запуск в :58 каждой минуты');
    console.log('⏱ Жизнь браузера: 6 минут (360 секунд)');
    
    const initialDelay = getDelayTo58();
    const nextRunTime = new Date(Date.now() + initialDelay);
    console.log(`⏱ Первый запуск через ${(initialDelay/1000).toFixed(3)} секунд`);
    console.log(`⏱ Время первого запуска: ${nextRunTime.toLocaleTimeString()}.${nextRunTime.getMilliseconds()}`);
    
    await new Promise(resolve => setTimeout(resolve, initialDelay));
    console.log('✅ Синхронизировались!');
    
    while (true) {
        const now = new Date();
        console.log(`\n🚀 Запуск браузера в ${now.toLocaleTimeString()}.${now.getMilliseconds()}`);
        
        run();
        
        await new Promise(resolve => setTimeout(resolve, 60000));
    }
})();