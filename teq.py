import datetime
import re
import time
import urllib.parse
import requests
from threading import Thread, Lock
import colorama
from fake_useragent import UserAgent
from telegram import telegram
from .utils import calculate_stickers_profit_steam, calculate_stickers_profit
colorama.init(True)
LISTINGS_ID = []


class SteamParse(Thread):
    session_id = None
    session_for_buy = None
    config = None
    number_of_thread = 0
    thread_print = Lock()

    def __init__(self, proxy, skins):
        Thread.__init__(self)
        SteamParse.number_of_thread += 1
        self.thread_id = SteamParse.number_of_thread
        self.user_agent = UserAgent().random

        self.skins = skins
        self.proxies = {
            'http': proxy,
            'https': proxy
        }

    def log(self, msg):
        self.thread_print.acquire()
        print(f'{colorama.Fore.CYAN}[{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] '
              f'{colorama.Fore.GREEN}Поток №{self.thread_id} {colorama.Fore.WHITE}{msg}')
        self.thread_print.release()

    def save_get(self, **kwargs) -> requests.Response:
        tries = 5
        while tries:
            try:
                r = requests.get(proxies=self.proxies, **kwargs)
            except requests.exceptions.ProxyError:
                tries -= 1
                continue
            except requests.exceptions.SSLError:
                tries -= 1
                continue
            if r.status_code == 200:
                return r
            self.log(f'Ошибка {r.status_code}. Пауза 10 секунд')
            time.sleep(10)

    def get_name_id(self, url):
        text = self.save_get(url=url).text
        return eval(re.search(r'\( \d+ \)', text).group())

    def get_buy_order(self, name_id):
        url = 'https://steamcommunity.com/market/itemordershistogram'
        params = {
            'country': 'RU',
            'language': 'russian',
            'currency': 1,
            'two_factor': 0,
            'item_nameid': name_id
        }
        r = self.save_get(url=url, params=params).json()
        orders = r['buy_order_graph']
        return orders[0][0]

    def get_lots(self, skin_url, query=""):
        payload = {
            "query": query,
            "start": 0,
            "count": self.config['count_parse'],
            "currency": 1,
        }

        name_item = skin_url.split('/')[-1]
        url = f'https://steamcommunity.com/market/listings/730/{name_item}/render/'

        while True:
            r = requests.get(url, proxies=self.proxies, params=payload, headers={'User-Agent': self.user_agent})

            if r.status_code != 429:
                self.log('Пауза 5 сек')
                time.sleep(5)
                return r.json()['listinginfo']

            self.log(f'Пауза 1 минута {r.status_code}')
            time.sleep(60)

    def get_skin_info(self, inspect_link):
        url = 'https://api.csgofloat.com/'
        r = self.save_get(url=url, params={'url': inspect_link})
        return r.json()['iteminfo']

    def run(self) -> None:
        if not self.skins:
            self.log('Этому потоку ничего не досталось.')
            return

        start_time = time.time()
        while True:
            self.log(f"Прошёл один цикл за {time.time() - start_time} сек.")
            start_time = time.time()

            for skin in self.skins:
                skin_float, skin = skin.split(maxsplit=1)
                try:
                    name_id = self.get_name_id(skin)
                except AttributeError:
                    self.log(f'Такого скина не существует походу. {skin}')
                    continue
                except Exception as e:
                    self.log(f'Что-то не так с {skin}:\n{str(e)}')
                    continue
                autobuy = self.get_buy_order(name_id)
                min_float, max_float = map(float, skin_float.split('-'))
                try:
                    lots = self.get_lots(skin)
                    if self.config['stickers']:
                        lots.update(self.get_lots(skin, 'sticker'))
                except Exception as e:
                    print(self.log(str(e)))
                    continue
                for pos, listing_id in enumerate(lots):
                    if listing_id in LISTINGS_ID:
                        continue

                    lot = lots[listing_id]
                    self.log(f'{skin} {listing_id}')
                    if 'market_actions' not in lot['asset']:
                        break
                    link = lot['asset']['market_actions'][0]['link']
                    link = link.replace('%listingid%', str(listing_id)).replace('%assetid%', lot['asset']['id'])

                    try:
                        info = self.get_skin_info(link)
                    except Exception as e:
                        self.log(f'{skin} что-то пошло не так. {str(e)}')
                        continue
                    info['autobuy'] = autobuy
                    part1 = skin[::-1].split('/', maxsplit=1)[1][::-1]
                    if "%" not in skin:
                        part2 = urllib.parse.quote(skin[::-1].split('/', maxsplit=1)[0][::-1])
                    else: part2 = skin[::-1].split('/', maxsplit=1)[0][::-1]
                    info['buy_url'] = part1 + '/' + part2
                    info['pos'] = pos + 1
                    converted_price = round(float(lot.get('converted_price', 0)) / 100, 2)
                    converted_fee = round(float(lot.get('converted_fee', 0)) / 100, 2)
                    info['send_price'] = round(converted_price + converted_fee, 2)

                    if self.config['float'] and (max_float >= info['floatvalue'] >= min_float):
                        telegram.send_msg_result(info, 'float')
                        LISTINGS_ID.append(listing_id)
                        self.log(f'{listing_id} float = {info["floatvalue"]}')

                    if self.config['stickers']:
                        total_price = 0
                        for i in range(len(info['stickers'])):
                            hash_name = 'Sticker | ' + info['stickers'][i]['name']
                            try:
                                sticker_price = self.parse_lowest_price(hash_name)
                                if sticker_price is None:
                                    total_price = 0
                                    break
                            except Exception as e:
                                print(str(e))
                                continue
                            if info['stickers'][i].get('wear', 1)*100 == 100:
                                total_price += sticker_price
                                info['stickers'][i]['price'] = sticker_price

                        if total_price >= self.config['minimum_total_price']:
                            info['sticker_total_price'] = total_price
                            info['profit'] = calculate_stickers_profit(info)
                            info['steam_profit'] = calculate_stickers_profit_steam(info)
                            if info['profit'] >= self.config['minimal_stickers_profit']:
                                telegram.send_msg_result(info, 'sticker')
                                LISTINGS_ID.append(listing_id)

    def parse_lowest_price(self, hash_name):
        url = 'https://steamcommunity.com/market/priceoverview/'
        count_of_errors = 0
        while True:
            if count_of_errors > 10:
                return None
            r = requests.get(url, {'appid': 730, 'currency': 1, 'market_hash_name': hash_name}, proxies=self.proxies)
            if r.status_code == 200:
                return float(r.json()['lowest_price'][1:])

            self.log(f'{r.status_code} ошибка при парсинге цены. Ожидаем...')
            count_of_errors += 1
            time.sleep(15)
