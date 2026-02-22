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

// Функция для расчета номера игры по времени (МСК, старт в 3:00, интервал 1 минута)
function getGameNumberByTime() {
    const now = new Date();
    
    // Конвертируем в МСК (UTC+3)
    const mskTime = new Date(now.toLocaleString('en-US', { timeZone: 'Europe/Moscow' }));
    
    // Стартовое время сегодня в 3:00 МСК
    const startTime = new Date(mskTime);
    startTime.setHours(3, 0, 0, 0);
    startTime.setSeconds(0);
    startTime.setMilliseconds(0);
    
    // Если сейчас меньше 3:00 МСК, значит старт был вчера
    if (mskTime < startTime) {
        startTime.setDate(startTime.getDate() - 1);
    }
    
    // Разница в минутах от старта
    const diffMinutes = Math.floor((mskTime - startTime) / (60 * 1000));
    
    // Номер игры (первая игра в 3:00 = номер 1)
    const gameNumber = diffMinutes + 1;
    
    console.log(`⏰ Текущее время МСК: ${mskTime.toLocaleTimeString()}.${mskTime.getMilliseconds()}`);
    console.log(`📊 Минут от старта (3:00): ${diffMinutes}`);
    console.log(`🎲 Номер игры: ${gameNumber}`);
    
    return gameNumber.toString();
}

// Получение значения карты из класса
async function getCardValue(cardElement) {
    const classAttr = await cardElement.getAttribute('class');
    const match = classAttr.match(/scoreboard-card-games-card--value-(\d+)/);
    if (!match) return null;
    
    const value = parseInt(match[1]);
    
    // Маппинг чисел в реальные карты
    if (value >= 6 && value <= 10) {
        return { card: value.toString(), points: value };
    } else if (value === 11) {
        return { card: 'J', points: 2 };
    } else if (value === 12) {
        return { card: 'Q', points: 3 };
    } else if (value === 13) {
        return { card: 'K', points: 4 };
    } else if (value === 14) {
        return { card: 'A', points: 11 };
    }
    return null;
}

// Получение масти
async function getSuit(cardElement) {
    const classAttr = await cardElement.getAttribute('class');
    const match = classAttr.match(/scoreboard-card-games-card--suit-(\d+)/);
    if (match) {
        const suitNum = parseInt(match[1]);
        const suits = ['♠️', '♥️', '♣️', '♦️'];
        return suits[suitNum] || '';
    }
    return '';
}

// Форматирование карт для вывода
async function formatCards(cardElements) {
    let result = '';
    for (const card of cardElements) {
        const value = await getCardValue(card);
        const suit = await getSuit(card);
        if (value) {
            result += value.card + suit;
        }
    }
    return result;
}

// Проверка, являются ли карты двумя тузами
async function hasTwoAces(cardElements) {
    if (cardElements.length !== 2) return false;
    
    let aceCount = 0;
    for (const card of cardElements) {
        const classAttr = await card.getAttribute('class');
        const match = classAttr.match(/scoreboard-card-games-card--value-(\d+)/);
        if (match && parseInt(match[1]) === 14) { // 14 = A
            aceCount++;
        }
    }
    return aceCount === 2;
}

// Получение карт и счета со стола
async function getCards(page) {
    // Блок игрока
    const playerBlock = await page.$('.live-twenty-one-field__player');
    const playerCards = playerBlock ? await playerBlock.$$('.live-twenty-one-cards__item') : [];
    const playerScore = playerBlock ? await playerBlock.$eval('.live-twenty-one-field-score__label', el => el.textContent).catch(() => '0') : '0';

    // Блок дилера
    const dealerBlock = await page.$('.live-twenty-one-field__dealer');
    const dealerCards = dealerBlock ? await dealerBlock.$$('.live-twenty-one-cards__item') : [];
    const dealerScore = dealerBlock ? await dealerBlock.$eval('.live-twenty-one-field-score__label', el => el.textContent).catch(() => '0') : '0';

    // Проверка на два туза
    const playerTwoAces = await hasTwoAces(playerCards);
    const dealerTwoAces = await hasTwoAces(dealerCards);

    return { 
        player: playerCards, 
        banker: dealerCards, 
        pScore: playerScore, 
        bScore: dealerScore,
        playerTwoAces,
        dealerTwoAces
    };
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

// ИЗМЕНЕНО: всегда берем второй стол
async function checkTables(page) {
    const games = await page.$$('li.dashboard-game--theme-gray-100.dashboard-game.dashboard-champ__game');
    
    console.log(`Найдено столов: ${games.length}`);
    
    // Берем второй стол (индекс 1)
    if (games.length >= 2) {
        const game = games[1];
        
        // Получаем номер стола для лога
        const gameNumber = await game.$eval('.dashboard-game-info__additional-info', el => el.textContent).catch(() => 'unknown');
        console.log(`✅ Выбран второй стол: ${gameNumber}`);
        
        const link = await game.$('a[href*="/ru/live/twentyone/"]');
        if (link) {
            return await link.getAttribute('href');
        }
    }
    
    console.log('❌ Второй стол не найден');
    return null;
}

async function monitorGame(page, gameNumber) {
    let lastCards = { player: [], banker: [], pScore: '0', bScore: '0' };
    
    while (true) {
        const cards = await getCards(page);
        
        // Проверка на завершение игры
        const isGameOver = await page.evaluate(() => {
            const el = document.querySelector('.live-twenty-one-table__footer .ui-game-timer__label');
            return el && el.textContent.includes('Игра завершена');
        });
        
        if (isGameOver) {
            const cards = await getCards(page);
            
            if (cards.player.length > 0 || cards.banker.length > 0) {
                console.log('Игра завершена, отправляю результат...');
                
                const pScore = parseInt(cards.pScore);
                const bScore = parseInt(cards.bScore);
                const total = pScore + bScore;
                
                // Определяем победителя
                let winner = 'X';
                let winnerSymbol = '🔰';
                if (pScore > bScore) {
                    winner = 'П1';
                    winnerSymbol = '✅';
                }
                if (bScore > pScore) {
                    winner = 'П2';
                    winnerSymbol = '✅';
                }
                
                // Проверяем раннюю победу (2 карты у обоих)
                const isEarly = cards.player.length === 2 && cards.banker.length === 2;
                
                // Проверяем наличие 21 очка
                const has21 = pScore === 21 || bScore === 21;
                
                // Проверяем золотое очко (два туза)
                const hasGolden = (pScore === 21 && cards.playerTwoAces) || (bScore === 21 && cards.bankerTwoAces);
                
                // Формируем хештеги
                const tags = [];
                if (isEarly) tags.push('#R');
                if (has21) tags.push('#O');
                if (hasGolden) tags.push('#G');
                const tagsStr = tags.length > 0 ? ' ' + tags.join(' ') : '';
                
                const playerCardsStr = await formatCards(cards.player);
                const bankerCardsStr = await formatCards(cards.banker);
                
                let message;
                if (pScore > bScore) {
                    message = `#N${gameNumber}. ${winnerSymbol}${pScore}(${playerCardsStr}) - ${bScore}(${bankerCardsStr}) #T${total}${tagsStr} #${winner}`;
                } else if (bScore > pScore) {
                    message = `#N${gameNumber}. ${pScore}(${playerCardsStr}) - ${winnerSymbol}${bScore}(${bankerCardsStr}) #T${total}${tagsStr} #${winner}`;
                } else {
                    message = `#N${gameNumber}. ${pScore}(${playerCardsStr}) 🔰 ${bScore}(${bankerCardsStr}) #T${total}${tagsStr} #X`;
                }
                
                await sendOrEditTelegram(message);
            }
            
            console.log('Игра завершена, выхожу...');
            break;
        }
        
        const cardsChanged = 
            JSON.stringify(cards.player.map(c => c.toString())) !== JSON.stringify(lastCards.player.map(c => c.toString())) ||
            JSON.stringify(cards.banker.map(c => c.toString())) !== JSON.stringify(lastCards.banker.map(c => c.toString())) ||
            cards.pScore !== lastCards.pScore ||
            cards.bScore !== lastCards.bScore;
        
        if (cardsChanged && cards.player.length > 0 && cards.banker.length > 0) {
            const playerCardsStr = await formatCards(cards.player);
            const bankerCardsStr = await formatCards(cards.banker);
            const message = `⏱№${gameNumber}. ${cards.pScore}(${playerCardsStr}) -${cards.bScore} (${bankerCardsStr})`;
            
            await sendOrEditTelegram(message);
            lastCards = { 
                player: [...cards.player], 
                banker: [...cards.banker], 
                pScore: cards.pScore, 
                bScore: cards.bScore 
            };
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
        
        // ИЗМЕНЕНО: таймаут 3 минуты (180000 мс)
        timeout = setTimeout(async () => {
            console.log(`⏱ 3 минуты прошло, закрываю браузер`);
            if (browser) await browser.close();
        }, 180000);
        
        await page.goto(URL);
        console.log('Проверяем второй стол...');
        
        await page.waitForTimeout(3000); // Даем странице загрузиться
        
        // ИЗМЕНЕНО: берем ссылку на второй стол, без цикла
        const activeLink = await checkTables(page);
        if (!activeLink) {
            console.log('Второй стол не найден, закрываю браузер');
            return;
        }
        
        console.log('Заходим во второй стол:', activeLink);
        
        await page.click(`a[href="${activeLink}"]`);
        await page.waitForTimeout(5000);
        
        // Получаем номер игры по времени
        const gameNumber = getGameNumberByTime();
        console.log('Номер игры по времени:', gameNumber);
        
        // Сохраняем номер
        lastGameNumber = gameNumber;
        fs.writeFileSync(LAST_NUMBER_FILE, gameNumber);
        console.log('Номер сохранен в файл');
        
        // Ждем появления карт
        let attempts = 0;
        let cards = { player: [], banker: [] };
        while (attempts < 12 && (cards.player.length === 0 || cards.banker.length === 0)) {
            await page.waitForTimeout(5000);
            cards = await getCards(page);
            console.log(`Попытка ${attempts + 1}: карт игрока ${cards.player.length}, карт дилера ${cards.banker.length}`);
            attempts++;
        }
        
        if (cards.player.length > 0 && cards.banker.length > 0) {
            await monitorGame(page, gameNumber);
        } else {
            console.log('Не дождались карт');
        }
        
    } catch (e) {
        console.log('Ошибка:', e.message);
    } finally {
        if (timeout) clearTimeout(timeout);
        if (browser) {
            await browser.close();
            lastMessageId = null;
            lastMessageText = '';
        }
    }
}

// Функция для расчета задержки до запуска браузера в :02 секунд
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

// Синхронизированный запуск
(async () => {
    console.log('🤖 Бот для 21 очко запущен');
    console.log('🎯 Режим: ВСЕГДА ВТОРОЙ СТОЛ');
    console.log('🎯 Время работы: 3 минуты');
    console.log('🎯 Старт игр: 3:00 МСК, интервал 1 минута');
    console.log('🎯 Запуск бота: каждую минуту в :02 секунд');
    console.log('🔍 Селектор столов: li.dashboard-game--theme-gray-100.dashboard-game.dashboard-champ__game');
    
    const initialDelay = getDelayToNextGame();
    const nextRunTime = new Date(Date.now() + initialDelay);
    console.log(`⏱ Синхронизация: первый запуск через ${(initialDelay/1000).toFixed(3)} секунд`);
    console.log(`⏱ Время первого запуска: ${nextRunTime.toLocaleTimeString()}.${nextRunTime.getMilliseconds()}`);
    
    await new Promise(resolve => setTimeout(resolve, initialDelay));
    
    console.log('✅ Синхронизировались! Запуск каждые 60 секунд');
    
    while (true) {
        const now = new Date();
        console.log(`\n🚀 Запуск браузера в ${now.toLocaleTimeString()}.${now.getMilliseconds()}`);
        
        run(); // не ждем завершения
        
        await new Promise(resolve => setTimeout(resolve, 60000));
    }
})();
