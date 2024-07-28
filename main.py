import re
import time
import requests
import logging
from collections import OrderedDict
from datetime import datetime
import config
from bs4 import BeautifulSoup
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("function.log", "w", encoding="utf-8"), logging.StreamHandler()],
)


def parse_template(template_file):
    template_channels = OrderedDict()
    current_category = None

    with open(template_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    template_channels[current_category] = []
                elif current_category:
                    channel_name = line.split(",")[0].strip()
                    template_channels[current_category].append(channel_name)

    return template_channels


def fetch_channels(url):
    channels = OrderedDict()

    try:
        lines = ""
        if url == "酒店组播":
            lines = getHotel()
        else:
            response = requests.get(url)
            response.raise_for_status()
            response.encoding = "utf-8"
            lines = response.text.split("\n")
        current_category = None
        is_m3u = any("#EXTINF" in line for line in lines[:15])
        source_type = "m3u" if is_m3u else "txt"
        logging.info(f"url: {url} 获取成功，判断为{source_type}格式")

        if is_m3u:
            for line in lines:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    match = re.search(r'group-title="(.*?)",(.*)', line)
                    if match:
                        current_category = match.group(1).strip()
                        channel_name = match.group(2).strip()
                        if current_category not in channels:
                            channels[current_category] = []
                elif line and not line.startswith("#"):
                    channel_url = line.strip()
                    if current_category and channel_name:
                        channels[current_category].append((channel_name, channel_url))
        else:
            for line in lines:
                line = line.strip()
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    channels[current_category] = []
                elif current_category:
                    match = re.match(r"^(.*?),(.*?)$", line)
                    if match:
                        channel_name = match.group(1).strip()
                        channel_url = match.group(2).strip()
                        channels[current_category].append((channel_name, channel_url))
                    elif line:
                        channels[current_category].append((line, ""))
        if channels:
            categories = ", ".join(channels.keys())
            logging.info(
                f"url: {url} 爬取成功✅，包含频道数：{len(lines)} 包含频道分类: {categories}"
            )
    except requests.RequestException as e:
        logging.error(f"url: {url} 爬取失败❌, Error: {e}")

    return channels


def match_channels(template_channels, all_channels):
    matched_channels = OrderedDict()

    for category, channel_list in template_channels.items():
        matched_channels[category] = OrderedDict()
        for channel_name in channel_list:
            cur_channel_name = channel_name
            cur_list = [channel_name]
            if "|" in channel_name:
                cur_list = channel_name.split("|")
                cur_channel_name = cur_list[0]
            for online_category, online_channel_list in all_channels.items():
                for online_channel_name, online_channel_url in online_channel_list:
                    for item in cur_list:
                        if item == online_channel_name:
                            matched_channels[category].setdefault(cur_channel_name, []).append(
                                online_channel_url
                            )

    return matched_channels


def filter_source_urls(template_file):
    template_channels = parse_template(template_file)
    source_urls = config.source_urls

    all_channels = OrderedDict()
    for url in source_urls:
        fetched_channels = fetch_channels(url)
        for category, channel_list in fetched_channels.items():
            if category in all_channels:
                all_channels[category].extend(channel_list)
            else:
                all_channels[category] = channel_list

    matched_channels = match_channels(template_channels, all_channels)

    return matched_channels, template_channels


def is_ipv6(url):
    return re.match(r"^http:\/\/\[[0-9a-fA-F:]+\]", url) is not None


def updateChannelUrlsM3U(channels, template_channels):
    written_urls = set()

    current_date = datetime.now().strftime("%Y-%m-%d")
    for group in config.announcements:
        for announcement in group["entries"]:
            if announcement["name"] is None:
                announcement["name"] = current_date

    with open("live.m3u", "w", encoding="utf-8") as f_m3u:
        f_m3u.write(
            f"""#EXTM3U x-tvg-url={",".join(f'"{epg_url}"' for epg_url in config.epg_urls)}\n"""
        )

        with open("live.txt", "w", encoding="utf-8") as f_txt:
            for group in config.announcements:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group["entries"]:
                    f_m3u.write(
                        f"""#EXTINF:-1 tvg-id="1" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n"""
                    )
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")
            count = 0
            for category, channel_list in template_channels.items():
                f_txt.write(f"{category},#genre#\n")
                if category in channels:
                    for channel_name in channel_list:
                        channel_name = channel_name.split("|")[0]
                        if channel_name in channels[category]:
                            sorted_urls = sorted(
                                channels[category][channel_name],
                                key=lambda url: (
                                    not is_ipv6(url)
                                    if config.ip_version_priority == "ipv6"
                                    else is_ipv6(url)
                                ),
                            )
                            # sorted_urls = channels[category][channel_name]
                            filtered_urls = []
                            for url in sorted_urls:
                                if (
                                    url
                                    and url not in written_urls
                                    and not any(
                                        blacklist in url for blacklist in config.url_blacklist
                                    )
                                ):
                                    filtered_urls.append(url)
                                    written_urls.add(url)

                            total_urls = len(filtered_urls)
                            for index, url in enumerate(filtered_urls, start=1):
                                if is_ipv6(url):
                                    url_suffix = (
                                        f"$LR•IPV6"
                                        if total_urls == 1
                                        else f"$LR•IPV6『线路{index}』"
                                    )
                                else:
                                    url_suffix = (
                                        f"$LR•IPV4"
                                        if total_urls == 1
                                        else f"$LR•IPV4『线路{index}』"
                                    )
                                if "$" in url:
                                    base_url = url.split("$", 1)[0]
                                else:
                                    base_url = url

                                new_url = f"{base_url}{url_suffix}"

                                f_m3u.write(
                                    f'#EXTINF:-1 tvg-id="{index}" tvg-name="{channel_name}" tvg-logo="https://gitee.com/yuanzl77/TVBox-logo/raw/main/png/{channel_name}.png" group-title="{category}",{channel_name}\n'
                                )
                                f_m3u.write(new_url + "\n")
                                f_txt.write(f"{channel_name},{new_url}\n")
                                count += 1

            f_txt.write("\n")
            logging.info(f"爬取完成✅，共计频道数：{count}")


def getHotel():
    s = requests.Session()
    hotel = "http://www.foodieguide.com/iptvsearch/hoteliptv.php"

    rsp = s.post(
        url=hotel,
        data={
            "saerch": "广东电信",
            "Submit": "",
            "names": "Tom",
            "city": "HeZhou",
            "address": "Ca94122",
        },
        headers={
            "Host": "www.foodieguide.com",
            "Origin": "http://www.foodieguide.com",
            "Referer": "http://www.foodieguide.com/iptvsearch/hoteliptv.php",
        },
    )
    rsp.encoding = "utf-8"
    root = BeautifulSoup(rsp.text, "lxml")
    els = root.select('div[style="color:limegreen; "]')
    # ips = ["jt.zorua.cn:8787","113.109.251.210:9999"]
    ips = []
    lines = []
    for item in els:
        if item.parent.parent.a.get_text().strip() not in ips:
            ip, port = item.parent.parent.a.get_text().strip().split(":")
            if test_ip_port_connectivity(ip, int(port)):
                ips.append(item.parent.parent.a.get_text().strip())
    logging.info(",".join(ips))

    for item in ips:
        url = "http://www.foodieguide.com/iptvsearch/hotellist.html?s={0}&Submit=+&y=y".format(item)
        rsp = s.get(
            url,
            headers={
                "Host": "www.foodieguide.com",
                "Referer": "http://www.foodieguide.com/iptvsearch/hotellist.html?s={0}".format(
                    item
                ),
            },
        )
        url = "http://www.foodieguide.com/iptvsearch/alllist.php?s={0}&y=y".format(item)
        rsp = s.get(
            url,
            headers={
                "Host": "www.foodieguide.com",
                "Referer": "http://www.foodieguide.com/iptvsearch/hotellist.html?s={0}&y=false".format(
                    item
                ),
            },
        )
        logging.info(url)
        if rsp.status_code == 200:
            # logging.info(rsp.text)
            root = BeautifulSoup(rsp.text, "lxml")
            els = root.select("div.m3u8")
            for i in els:
                name = i.parent.select(".channel")[0].get_text().strip()
                ip = i.get_text().strip()
                if "高清" in name:
                    lines.append("{0},{1}".format(name.replace("高清", ""), ip))

    # lines = ["酒店组播,#genre#"] + lines

    # with open("hotel.txt", "w", encoding="utf-8") as f_txt:
    #         f_txt.write(f"{"\n".join(lines)}")

    # return lines
    sources = []
    if len(lines) > 0:
        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_channel = {
                executor.submit(download_speed_test, source): source for source in lines
            }
            speed_test_results = []
            for future in as_completed(future_to_channel):
                channel = future_to_channel[future]
                try:
                    result = future.result()
                    speed_test_results.append(result)
                except Exception as exc:
                    logging.info(f"频道：{channel[0]} 测速时发生异常：{exc}")

        with open("hotel.txt", "w", encoding="utf-8") as f_txt:
            speed_test_results = sorted(speed_test_results, key=lambda x: x[2], reverse=True)
            for name, url, speed in speed_test_results:
                f_txt.write(f"{name},{url},{speed}\n")
                sources.append(f"{name},{url}")
    else:
        logging.error(f"url: 酒店组播 爬取失败❌, 读取历史记录")
        with open("hotel.txt", "r", encoding="utf-8") as f_txt:
            for item in f_txt:
                name, url, speed = item.split(",")
                sources.append(f"{name},{url}")

    return ["酒店组播,#genre#"] + sources


def test_ip_port_connectivity(ip, port):
    """
    测试指定 IP 和端口的连通性
    """
    try:
        sock = socket.create_connection((ip, port), timeout=5)
        sock.close()
        return True
    except Exception as e:
        logging.info(f"连接 {ip}:{port} 失败: {e}")
        return False


def download_speed_test(channel):
    """
    执行下载速度测试
    """
    session = requests.Session()
    name, url = channel.split(",")
    chaoshi = 3
    for _ in range(3):
        try:
            response = session.get(url, stream=True, timeout=10)
            response.raise_for_status()
            start_time = time.time()
            size = 0
            for chunk in response.iter_content(chunk_size=1024):
                size += len(chunk)
                if time.time() - start_time >= chaoshi:
                    break
            else:
                continue
            download_time = time.time() - start_time
            download_rate = round(size / download_time / 1024 / 1024, 4)
            break
        except requests.RequestException:
            pass
    else:
        print(f"频道：{name}, URL: {url}, 0")
        return name, url, 0
    print(f"频道：{name}, URL: {url}, {download_rate}")
    return name, url, download_rate


if __name__ == "__main__":
    template_file = "demo.txt"
    channels, template_channels = filter_source_urls(template_file)
    updateChannelUrlsM3U(channels, template_channels)
