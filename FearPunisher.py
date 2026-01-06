import asyncio
import json
import aiohttp
import websockets
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

import requests
from colorama import init, Fore, Back, Style

init(autoreset=True)

logging.disable(logging.CRITICAL)

WS_URL = 'wss://yooma.su/api'
API_URL = 'https://api.fearproject.ru/admin/punishments/ban'

def load_settings():
    settings_path = os.path.join(os.path.dirname(__file__), 'settings.json')
    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("[-] Файл настроек settings.json не найден!")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[-] Ошибка чтения settings.json: {e}")
        sys.exit(1)

async def get_punishments_pages(punish_type: int = 0, search: str = '') -> Dict[str, Any]:
    try:
        async with websockets.connect(WS_URL) as websocket:
            request = {
                'type': 'get_punishments_pages',
                'punish_type': punish_type,
                'search': search
            }
            await websocket.send(json.dumps(request))

            response = await websocket.recv()
            data = json.loads(response)

            return data

    except Exception as e:
        raise


async def get_punishments(page: int = 1, punish_type: int = 0, search: str = '', cookies: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    try:
        cookie_string = '; '.join([f'{name}={value}' for name, value in cookies.items()]) if cookies else ''

        headers = {
            'Origin': 'https://yooma.su',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebSocket/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0'
        }
        if cookie_string:
            headers['Cookie'] = cookie_string

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, headers=headers) as ws:
                await ws.send_json({'type': 'get_type'})
                type_msg = await ws.receive()
                if type_msg.type == aiohttp.WSMsgType.TEXT:
                    type_response = type_msg.json()

                request = {
                    'type': 'get_punishments',
                    'page': page,
                    'punish_type': punish_type,
                    'search': search
                }

                await ws.send_json(request)

                while True:
                    msg = await ws.receive()
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        response = msg.json()

                        if 'punishments' in response and isinstance(response['punishments'], list):
                            punishments = response['punishments']
                            return punishments
                        else:
                            return []
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break

    except Exception as e:
        print(f'Ошибка получения наказаний: {e}')
        raise


def format_punishment(punishment: Dict[str, Any]) -> str:
    try:
        created_date = datetime.fromtimestamp(punishment['created']).strftime('%d.%m.%Y %H:%M:%S')
        expires_date = 'Navsegda' if not punishment.get('expires') else \
                       datetime.fromtimestamp(punishment['expires']).strftime('%d.%m.%Y %H:%M:%S')

        name = punishment.get('name', 'N/A')
        reason = punishment.get('reason', 'N/A')
        admin_name = punishment.get('admin_name', 'N/A')
        unpunish_admin_id = punishment.get('unpunish_admin_id')
        unpunish_status = 'НЕ СНЯТ' if (unpunish_admin_id is None or unpunish_admin_id == 'null') else f'СНЯТ (admin_id: {unpunish_admin_id})'

        return f"""Player: {name}
SteamID: {punishment['steamid']}
IP: {punishment['ip']}
Reason: {reason}
Admin: {admin_name}
Created: {created_date}
Expires: {expires_date}
Status: {unpunish_status}
{'-' * 40}"""
    except Exception as e:
        return f"Error formatting punishment: {e}"


async def get_punishments_older_than(cookies: Optional[Dict[str, str]] = None, days_threshold: int = 5) -> List[Dict[str, Any]]:
    all_punishments = []
    page = 1
    threshold_timestamp = time.time() - (days_threshold * 24 * 60 * 60)

    while True:
        try:
            punishments = await get_punishments(page, 0, '', cookies)
            if not punishments:
                break
            has_old_punishments = any(p.get('created', 0) < threshold_timestamp for p in punishments)
            all_punishments.extend(punishments)
            if not has_old_punishments:
                break

            page += 1
            await asyncio.sleep(0.5)

        except Exception as e:
            print(f'Ошибка при загрузке страницы {page}: {e}')
            break

    return all_punishments


async def run_parser(cookies: Optional[Dict[str, str]] = None, start_page: int = 100, num_bans_to_find: Optional[int] = None):
    if num_bans_to_find is None:
        try:
            user_input = input('Введите количество банов за читы: ')
            num_bans_to_find = int(user_input)
            if num_bans_to_find <= 0:
                print('Количество должно быть больше 0')
                return []
        except (ValueError, EOFError):
            print('Неверный ввод. Использую значение по умолчанию: 10')
            num_bans_to_find = 10

    BAN_AGE_THRESHOLD_SECONDS = 3 * 24 * 60 * 60
    current_timestamp = time.time()

    current_page = start_page
    collected_bans = []
    loading_frames = ['....', '.   ', '..  ', '... ']

    frame_index = 0
    consecutive_empty_pages = 0
    retry_count = 0
    MAX_RETRIES_PER_PAGE = 3

    def save_results(bans_list):
        output_path = os.path.join(os.path.dirname(__file__), 'output.txt')
        with open(output_path, 'w', encoding='utf-8') as f:
            for i, ban in enumerate(bans_list, 1):
                created_date = datetime.fromtimestamp(ban['created']).strftime('%d.%m.%Y %H:%M:%S')
                expires_date = 'Navsegda' if not ban.get('expires') or ban['expires'] == 0 else \
                              datetime.fromtimestamp(ban['expires']).strftime('%d.%m.%Y %H:%M:%S')

                unpunish_admin_id = ban.get('unpunish_admin_id')
                status = 'НЕ СНЯТ' if (unpunish_admin_id is None or unpunish_admin_id == 'null') else f'СНЯТ (admin_id: {unpunish_admin_id})'

                f.write(f'{i}. Player: {ban.get("name", "N/A")}\n')
                f.write(f'   SteamID: {ban["steamid"]}\n')
                f.write(f'   Created: {created_date}\n')
                f.write(f'   Expires: {expires_date}\n')
                f.write(f'   Reason: {ban.get("reason", "N/A")}\n')
                f.write(f'   Status: {status}\n')
                f.write('-' * 40 + '\n')

    async def update_animation():
        nonlocal frame_index
        try:
            while len(collected_bans) < num_bans_to_find:
                frame = loading_frames[frame_index % len(loading_frames)]
                sys.stdout.write(f'\rПарсинг{frame} ({len(collected_bans)}/{num_bans_to_find}) ({current_page})')
                sys.stdout.flush()
                frame_index += 1
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            pass

    animation_task = asyncio.create_task(update_animation())

    try:
        while len(collected_bans) < num_bans_to_find:
            try:
                punishments = await asyncio.wait_for(
                    get_punishments(current_page, 0, '', cookies),
                    timeout=45.0
                )

                if not punishments:
                    consecutive_empty_pages += 1
                    current_page += 1
                    continue

                consecutive_empty_pages = 0
                retry_count = 0

                cheat_bans_on_page = 0
                for punishment in punishments:
                    reason = punishment.get('reason', '').lower()
                    created = punishment.get('created', 0)
                    unpunish_admin_id = punishment.get('unpunish_admin_id')

                    is_cheat_ban = ('читы' in reason or 'читерство' in reason or 'чит' in reason)
                    is_old_enough = (current_timestamp - created) > BAN_AGE_THRESHOLD_SECONDS
                    is_unpunished = (unpunish_admin_id is None or unpunish_admin_id == 'null')

                    if is_cheat_ban and is_old_enough and is_unpunished:
                        collected_bans.append(punishment)
                        cheat_bans_on_page += 1
                        if len(collected_bans) >= num_bans_to_find:
                            break

                if current_page % 50 == 0:
                    print(f'\nПрогресс: страница {current_page}, найдено {len(collected_bans)}/{num_bans_to_find} банов')

                current_page += 1
                retry_count = 0
                await asyncio.sleep(1.0)

            except asyncio.TimeoutError:
                retry_count += 1
                if retry_count >= MAX_RETRIES_PER_PAGE:
                    animation_task.cancel()
                    sys.stdout.write('\r' + ' ' * 50 + '\r')
                    print(f'Таймаут при обработке страницы {current_page} после {MAX_RETRIES_PER_PAGE} попыток. Пропускаю страницу.')
                    consecutive_empty_pages += 1
                    retry_count = 0
                    current_page += 1
                    await asyncio.sleep(2.0)
                    continue

                sys.stdout.write('\r' + ' ' * 50 + '\r')
                print(f'Таймаут при обработке страницы {current_page} (45 сек). Попытка {retry_count}/{MAX_RETRIES_PER_PAGE}. Переподключение...')
                await asyncio.sleep(3.0)
                continue
            except Exception as e:
                animation_task.cancel()
                sys.stdout.write('\r' + ' ' * 50 + '\r')
                print(f'Ошибка при обработке страницы {current_page}: {e}')
                if collected_bans:
                    print(f'Сохраняю {len(collected_bans)} найденных результатов...')
                    save_results(collected_bans)
                    print('Результаты сохранены в output.txt')
                return collected_bans

    except KeyboardInterrupt:
        animation_task.cancel()
        sys.stdout.write('\r' + ' ' * 50 + '\r')
        print(f'\nПарсер остановлен пользователем!')
        if collected_bans:
            print(f'Сохраняю {len(collected_bans)} найденных результатов...')
            save_results(collected_bans)
            print('Результаты сохранены в output.txt')
        else:
            print('Результаты не найдены.')
        return collected_bans

    finally:
        animation_task.cancel()
        sys.stdout.write('\r' + ' ' * 50 + '\r')
        sys.stdout.flush()

    if collected_bans:
        print(f'Найдено {len(collected_bans)} банов за читы. Сохраняю в output.txt...')
        save_results(collected_bans)
        print('Результаты сохранены в output.txt')
        return collected_bans

def parse_output_file(file_path):
    players = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        return players

    blocks = content.strip().split('----------------------------------------')
    for block in blocks:
        if not block.strip():
            continue

        lines = block.strip().split('\n')
        if len(lines) >= 5:
            try:
                player_info = {}
                steamid_line = lines[1].strip()
                if 'SteamID: ' in steamid_line:
                    player_info['steamid'] = steamid_line.split('SteamID: ', 1)[1].strip()
                    players.append(player_info)
            except:
                continue
    return players


def check_player_bans(session, steamid):
    url = f"https://api.fearproject.ru/punishments/search?q={steamid}&page=1&limit=10&type=1"
    try:
        response = session.get(url)
        if response.status_code == 200:
            data = response.json()
            punishments = data.get('punishments', [])
            for punishment in punishments:
                if punishment.get('status') == 1:
                    return True
        return False
    except Exception as e:
        print(f"[-] Ошибка проверки {steamid}: {e}")
        return False


def update_output_file(file_path, players_to_remove):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        return

    blocks = content.strip().split('----------------------------------------')
    filtered_blocks = []

    for block in blocks:
        if not block.strip():
            continue

        lines = block.strip().split('\n')
        if len(lines) >= 5:
            try:
                steamid_line = lines[1].strip()
                if 'SteamID: ' in steamid_line:
                    steamid = steamid_line.split('SteamID: ', 1)[1].strip()
                    if steamid not in players_to_remove:
                        filtered_blocks.append(block)
            except:
                filtered_blocks.append(block)
        else:
            filtered_blocks.append(block)

    new_content = '----------------------------------------'.join(filtered_blocks)
    if filtered_blocks:
        new_content += '----------------------------------------'

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except Exception as e:
        print(f"[-] Ошибка записи файла: {e}")


def run_checker(settings):
    output_paths = [
        "output.txt",
        os.path.join(os.path.dirname(__file__), "output.txt"),
        os.path.join(os.path.dirname(__file__), "FearPunisher", "../output.txt")
    ]

    output_file = None
    for path in output_paths:
        if os.path.exists(path):
            output_file = path
            break

    if not output_file:
        print("[-] Файл output.txt не найден!")
        return

    print(f"Проверяю файл: {os.path.abspath(output_file)}")

    players = parse_output_file(output_file)
    if not players:
        print("[-] Игроки не найдены в файле!")
        return

    print(f"[i] Найдено {len(players)} игроков для проверки")

    session = requests.Session()
    session.cookies.set('access_token', settings["access_token"], domain='.fearproject.ru')

    players_to_remove = []
    checked_count = 0

    for player in players:
        steamid = player['steamid']
        print(f"[>] Проверяю {steamid}...")

        if check_player_bans(session, steamid):
            print(f"   [-] Найден активный бан, удаляю из списка")
            players_to_remove.append(steamid)
        else:
            print(f"   [+] Активных банов не найдено")

        checked_count += 1
        time.sleep(0.5)

    if players_to_remove:
        print(f"\nУдаляю {len(players_to_remove)} игроков с активными банами...")
        update_output_file(output_file, players_to_remove)
        print("[+] Файл обновлен!")
    else:
        print("\n[+] Нет игроков для удаления")

    print(f"[STATS] Проверено: {checked_count}, удалено: {len(players_to_remove)}")

class AutoBan:
    def __init__(self, settings: dict):
        self.settings = settings

        self.output_file = None
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_paths = [
            "output.txt",
            os.path.join(script_dir, "output.txt"),
            os.path.join(script_dir, "FearPunisher", "../output.txt")
        ]

        for path in output_paths:
            if os.path.exists(path):
                self.output_file = path
                break

        if self.output_file is None:
            self.output_file = os.path.join(script_dir, "output.txt")

        self.session = requests.Session()
        self.session.headers.update({
            'accept': '*/*',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'content-type': 'application/json',
            'origin': 'https://fearproject.ru',
            'referer': 'https://fearproject.ru/',
            'sec-ch-ua': '"Opera GX";v="125", "Not?A_Brand";v="8", "Chromium";v="141"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 OPR/125.0.0.0 (Edition ms_store_gx)'
        })
        self.session.cookies.set('access_token', settings["access_token"], domain='.fearproject.ru')

    def parse_output_file(self) -> List[Dict[str, Any]]:
        players = []

        if not os.path.exists(self.output_file):
            print(f"[-] Файл {self.output_file} не найден!")
            return players

        try:
            with open(self.output_file, 'r', encoding='utf-8') as f:
                content = f.read()

            blocks = content.strip().split('----------------------------------------')

            for block in blocks:
                if not block.strip():
                    continue

                lines = block.strip().split('\n')
                if len(lines) < 5:
                    continue

                try:
                    player_info = {}

                    first_line = lines[0].strip()
                    if '. Player: ' in first_line:
                        player_info['name'] = first_line.split('. Player: ', 1)[1].strip()

                    steamid_line = lines[1].strip()
                    if 'SteamID: ' in steamid_line:
                        player_info['steamid'] = steamid_line.split('SteamID: ', 1)[1].strip()

                    created_line = lines[2].strip()
                    if 'Created: ' in created_line:
                        date_str = created_line.split('Created: ', 1)[1].strip()
                        player_info['created'] = datetime.strptime(date_str, '%d.%m.%Y %H:%M:%S')

                    expires_line = lines[3].strip()
                    if 'Expires: ' in expires_line:
                        date_str = expires_line.split('Expires: ', 1)[1].strip()
                        if date_str.lower() != 'navsegda':
                            player_info['expires'] = datetime.strptime(date_str, '%d.%m.%Y %H:%M:%S')
                        else:
                            player_info['expires'] = None

                    reason_line = lines[4].strip()
                    if 'Reason: ' in reason_line:
                        player_info['reason'] = reason_line.split('Reason: ', 1)[1].strip()

                    if all(key in player_info for key in ['name', 'steamid', 'created', 'reason']):
                        players.append(player_info)

                except Exception as e:
                    continue

        except Exception as e:
            print(f"Ошибка при чтении файла {self.output_file}: {e}")

        return players

    def is_ban_active_and_recent(self, player: Dict[str, Any]) -> bool:
        now = datetime.now()

        created = player['created']
        year = created.year
        month = created.month + 2
        if month > 12:
            year += 1
            month -= 12

        if month == 2:
            day = min(created.day, 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28)
        elif month in [4, 6, 9, 11]:
            day = min(created.day, 30)
        else:
            day = min(created.day, 31)

        calculated_ban_end = created.replace(year=year, month=month, day=day)

        if now > calculated_ban_end:
            return False

        return True

    def calculate_ban_duration(self, player: Dict[str, Any]) -> int:
        now = datetime.now()

        created = player['created']
        year = created.year
        month = created.month + 2
        if month > 12:
            year += 1
            month -= 12

        if month == 2:
            day = min(created.day, 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28)
        elif month in [4, 6, 9, 11]:
            day = min(created.day, 30)
        else:
            day = min(created.day, 31)

        ban_until = created.replace(year=year, month=month, day=day)

        if player.get('expires'):
            ban_until = min(ban_until, player['expires'])

        if ban_until <= now:
            return 3600

        duration_seconds = int((ban_until - now).total_seconds())
        return max(duration_seconds, 3600)

    def ban_player(self, player: Dict[str, Any]) -> bool:
        try:
            steamid = player['steamid'].strip()
            if not steamid.isdigit() or len(steamid) != 17 or not steamid.startswith('7656119'):
                print(f"[-] Неверный формат SteamID: {steamid}")
                return False

            ban_duration = self.calculate_ban_duration(player)

            if self.settings["use_custom_reason"]:
                reason = self.settings["custom_ban_reason"]
            else:
                reason = self.settings["default_ban_reason"]

            payload = {
                "steamid": steamid,
                "reason": reason,
                "duration": ban_duration,
                "punish_type": 0
            }

            ban_until = datetime.now() + timedelta(seconds=ban_duration)
            print(f"Баню игрока {player['name']} (SteamID: {steamid}) на {ban_duration // 86400} дней (до {ban_until.strftime('%d.%m.%Y %H:%M')})")

            response = self.session.post(API_URL, json=payload)

            if response.status_code == 201:
                print(f"[+] Успешно забанен: {player['name']}")
                return True
            elif response.status_code == 409:
                print(f"[-] Игрок {player['name']} уже забанен")
                return False
            elif response.status_code == 400:
                print(f"[-] Неверные данные для бана {player['name']}: {response.text}")
                return False
            elif response.status_code == 500:
                print(f"[-] Внутренняя ошибка сервера при бане {player['name']}. Возможно игрок уже забанен или проблемы с сервером.")
                return False
            else:
                print(f"[-] Ошибка бана {player['name']}: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            print(f"[-] Ошибка при бане {player['name']}: {e}")
            return False

    def run_autoban(self):
        print("Запускаю автобан игроков из output.txt...")
        print(f"Файл: {os.path.abspath(self.output_file)}")
        print("-" * 50)

        players = self.parse_output_file()

        if not players:
            print("[-] Не найдено игроков для бана!")
            return

        print(f"[i] Найдено {len(players)} игроков")

        banned_count = 0
        skipped_count = 0

        for player in players:
            if self.is_ban_active_and_recent(player):
                print(f"[>] Игрок {player['name']} подходит для бана")
                if self.ban_player(player):
                    banned_count += 1
                time.sleep(1)
            else:
                print(f"[SKIP] Игрок {player['name']} пропущен (расчетный бан уже истек)")
                skipped_count += 1

        print("-" * 50)
        print(f"[STATS] Результаты: забанено {banned_count}, пропущено {skipped_count}")

def show_menu():
    os.system('cls' if os.name == 'nt' else 'clear')

    print(Fore.CYAN + Style.BRIGHT + """
   ███████╗ ██████╗ ██╗   ██╗███╗   ██╗██╗███████╗██╗  ██╗███████╗██████╗
║  ██╔════╝╗██╔══██╗██║   ██║████╗  ██║██║██╔════╝██║  ██║██╔════╝██╔══██╗
║  █████╗  ╝██████╔╝██║   ██║██╔██╗ ██║██║███████╗███████║█████╗  ██████╔╝
║  ██╔══╝  ╗██╔═══╝ ██║   ██║██║╚██╗██║██║╚════██║██╔══██║██╔══╝  ██╔══██╗
║  ██║     ║██║     ╚██████╔╝██║ ╚████║██║███████║██║  ██║███████╗██║  ██║
║  ╚═╝     ╚═ ╝     ╚══════╝ ╚═╝  ╚══╝ ╚╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝╚══════╝
""" + Style.RESET_ALL)

    print(Fore.YELLOW + Style.BRIGHT + "                          by anarchowitz" + Style.RESET_ALL)
    print()
    print(Fore.GREEN + Style.BRIGHT + "Выберите режим:" + Style.RESET_ALL)
    print(Fore.WHITE + "1) " + Fore.BLUE + "Парсер")
    print(Fore.WHITE + "2) " + Fore.BLUE + "Чекер")
    print(Fore.WHITE + "3) " + Fore.BLUE + "Авто-бан")
    print()
    print(Fore.RED + "Для выхода нажмите Ctrl+C" + Style.RESET_ALL)
    print()

def main():
    while True:
        show_menu()

        try:
            choice = input(Fore.CYAN + "Выберите опцию (1-3): " + Style.RESET_ALL).strip()

            if choice == "1":
                os.system('cls' if os.name == 'nt' else 'clear')
                print(Fore.GREEN + "\n" + "═"*60)
                print("PARSER")
                print("═"*60 + Style.RESET_ALL)

                try:
                    start_page_input = input(Fore.YELLOW + "Введите стартовую страницу (по умолчанию 100): " + Style.RESET_ALL).strip()
                    if not start_page_input:
                        start_page = 100
                    else:
                        start_page = int(start_page_input)
                except ValueError:
                    print(Fore.RED + "Неверный формат. Используется страница 100." + Style.RESET_ALL)
                    start_page = 100

                print(Fore.CYAN + f"Начинаю парсинг с страницы {start_page}..." + Style.RESET_ALL)
                print(Fore.YELLOW + "Для остановки нажмите Ctrl+C" + Style.RESET_ALL)

                try:
                    asyncio.run(run_parser(start_page=start_page))
                except KeyboardInterrupt:
                    print(Fore.RED + "\nПарсер прерван пользователем!" + Style.RESET_ALL)

            elif choice == "2":
                os.system('cls' if os.name == 'nt' else 'clear')
                print(Fore.GREEN + "\n" + "═"*60)
                print("CHECKER")
                print("═"*60 + Style.RESET_ALL)
                settings = load_settings()
                run_checker(settings)

            elif choice == "3":
                os.system('cls' if os.name == 'nt' else 'clear')
                print(Fore.GREEN + "\n" + "═"*60)
                print("AUTO-BAN")
                print("═"*60 + Style.RESET_ALL)
                settings = load_settings()
                autoban = AutoBan(settings)
                autoban.run_autoban()

            else:
                os.system('cls' if os.name == 'nt' else 'clear')
                print(Fore.RED + "\n[-] Ошибка: Неверный выбор. Допустимые опции: 1, 2, 3" + Style.RESET_ALL)

        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n\nВЫХОД" + Style.RESET_ALL)
            break
        except Exception as e:
            print(Fore.RED + f"\n[-] Ошибка: {e}" + Style.RESET_ALL)

        input(Fore.CYAN + "\n[*] Нажмите Enter для продолжения..." + Style.RESET_ALL)

if __name__ == "__main__":
    main()
