import re
import requests
import logging
from collections import OrderedDict
from datetime import datetime
import config
from bs4 import BeautifulSoup
import time
import random

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

            f_txt.write("\n")


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
    ips = []
    lines = []
    lines.append("酒店组播,#genre#")
    for item in els:
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
                )
            },
        )
        url = "http://www.foodieguide.com/iptvsearch/alllist.php?s={0}&y=y".format(item)
        rsp = s.get(
            url,
            headers={
                "Host": "www.foodieguide.com",
                "Referer": "http://www.foodieguide.com/iptvsearch/hotellist.html?s={0}&y=false".format(
                    item
                )
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
    return lines


def getHotel2():
    hotel = "http://tonkiang.us/hoteliptv.php"

    rsp = requests.post(
        url=hotel,
        data={
            "saerch": "广东电信",
            "Submit": "",
            # "names": "Tom",
            # "city": "HeZhou",
            # "address": "Ca94122",
        },
        headers={
            "Host": "tonkiang.us",
            "Origin": "http://tonkiang.us",
            "Referer": "http://tonkiang.us/hoteliptv.php",
        },
    )
    rsp.encoding = "utf-8"
    root = BeautifulSoup(rsp.text, "lxml")
    els = root.select('div[style="color:limegreen; "]')
    ips = []
    lines = []
    lines.append("酒店组播,#genre#")
    for item in els:
        ips.append(item.parent.parent.a.get_text().strip())
    logging.info(",".join(ips))
    for item in ips:
        s = requests.Session()
        s.cookies.update(
            {
                "ckip1": "110.7.129.246%7C112.114.137.111%7C111.61.236.247%7C113.90.239.108%7C118.81.54.64%7C47.108.221.227%7C58.57.40.22%7C183.236.194.115",
                "ckip2": "144.52.146.226%7C1.83.124.179%7C123.113.237.36%7C117.32.84.33%7C60.189.35.225%7C60.189.35.225%7Ckms.erfiy.com%7C95.181.85.13",
                "REFERER": "Gameover",
            },
        )
        url = "http://tonkiang.us/alllist.php?s={0}".format(item)
        rsp = s.get(
            url,
            headers={
                "Host": "tonkiang.us",
                "Referer": "http://tonkiang.us/hotellist.html?s={0}".format(item),
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        logging.info(url)
        if rsp.status_code == 200:
            logging.info(rsp.text)
            root = BeautifulSoup(rsp.text, "lxml")
            els = root.select("div.m3u8")
            for i in els:
                name = i.parent.select(".channel")[0].get_text().strip()
                ip = i.get_text().strip()
                if "高清" in name:
                    lines.append("{0},{1}".format(name.replace("高清", ""), ip))
    return lines


if __name__ == "__main__":
    template_file = "demo.txt"
    channels, template_channels = filter_source_urls(template_file)
    updateChannelUrlsM3U(channels, template_channels)
