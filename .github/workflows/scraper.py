import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import sqlite3
from urllib.parse import urljoin, urlparse, quote
import logging
from typing import List, Dict, Optional, Tuple
import re
import argparse
import json
import os
from datetime import datetime, timedelta

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 设置请求头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# 内置 TMDb API 密钥
TMDB_API_KEY = "cdcb421a1f0af83703e1c4162b4331ea"

class BoxOfficeScraper:
    def __init__(self, delay: float = 1.0, tmdb_api_key: str = ""):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.tmdb_api_key = tmdb_api_key or TMDB_API_KEY
        self.movie_overrides = self._load_movie_overrides()

        if self.tmdb_api_key:
            logger.info("✓ TMDb API已启用")
        else:
            logger.info("⚠ TMDb API未配置")

    def _load_movie_overrides(self) -> Dict[str, Dict[str, str]]:
        override_file = os.path.join(os.path.dirname(__file__), 'movie_override.json')
        if not os.path.exists(override_file):
            return {}
        try:
            with open(override_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def parse_week_identifier(self, week_str: str) -> tuple:
        if not week_str or len(week_str) != 6:
            raise ValueError(f"周次格式错误: {week_str}")
        year = int(week_str[:4])
        week = int(week_str[4:])
        return (year, week)

    def get_previous_weeks(self, target_week_str: str, n: int = 3) -> List[str]:
        """
        获取目标周次之前的 n 个周次（处理跨年）
        """
        year, week = self.parse_week_identifier(target_week_str)
        weeks = []

        current_y, current_w = year, week

        # 向前推导
        for _ in range(n):
            current_w -= 1
            if current_w < 1:
                current_y -= 1
                # 简单处理：前一年按52周计算
                current_w = 52
            weeks.append(f"{current_y}{current_w:02d}")

        return sorted(weeks) # 返回按时间正序排列的列表

    def get_weekend_url_by_week(self, year: int, week: int) -> Optional[str]:
        week_identifier = f"{year}W{week:02d}"
        weekend_url = f"https://www.boxofficemojo.com/weekend/{week_identifier}/"
        try:
            response = self.session.get(weekend_url, timeout=10)
            if response.status_code == 200:
                return weekend_url
            return None
        except Exception:
            return None

    def crawl_by_week_range(self, start_week: str, end_week: str) -> List[Dict]:
        """按周次范围爬取"""
        start_year, start_week_num = self.parse_week_identifier(start_week)
        end_year, end_week_num = self.parse_week_identifier(end_week)
        all_movies = []

        for year in range(start_year, end_year + 1):
            week_start = start_week_num if year == start_year else 1
            week_end = end_week_num if year == end_year else 52

            for week in range(week_start, week_end + 1):
                weekend_url = self.get_weekend_url_by_week(year, week)
                if weekend_url:
                    logger.info(f"正在爬取 {year}年第{week:02d}周...")
                    movies = self.fetch_weekend_top10(weekend_url)
                    all_movies.extend(movies)
                    time.sleep(self.delay)
                else:
                    logger.warning(f"跳过 {year}年第{week:02d}周")
        return all_movies

    def crawl_by_week_list(self, week_list: List[str]) -> List[Dict]:
        """按给定的周次列表爬取"""
        all_movies = []
        for week_str in week_list:
            year, week = self.parse_week_identifier(week_str)
            weekend_url = self.get_weekend_url_by_week(year, week)
            if weekend_url:
                logger.info(f"正在爬取 {year}年第{week}周...")
                movies = self.fetch_weekend_top10(weekend_url)
                all_movies.extend(movies)
                time.sleep(self.delay)
            else:
                logger.warning(f"跳过 {year}年第{week}周 (URL无效)")
        return all_movies

    def search_douban_movie(self, english_title: str, year: str = "", imdb_id: str = "") -> Dict[str, str]:
        """
        通过豆瓣API搜索电影信息
        """
        try:
            # 方案0: 检查是否有预设配置
            if english_title in self.movie_overrides:
                override_data = self.movie_overrides[english_title]
                return {
                    'chinese_title': override_data.get('chinese_title', english_title),
                    'douban_url': override_data.get('douban_url', ''),
                    'tmdb_url': override_data.get('tmdb_url', ''),
                    'cover_image': override_data.get('cover_image', ''),
                    'summary': override_data.get('summary', ''),
                    'chinese_summary': override_data.get('chinese_summary', ''),
                    'genres': override_data.get('genres', []),
                    'directors': override_data.get('directors', []),
                    'actors': override_data.get('actors', []),
                    'rating': override_data.get('rating', 0),
                    'vote_count': override_data.get('vote_count', 0)
                }

            # 方案0.5: 如果有 IMDb ID，优先使用 IMDb ID 精确匹配
            if imdb_id and self.tmdb_api_key:
                # 先尝试 TMDb Find API (更准)
                result = self._search_by_imdb_id(imdb_id, english_title)
                if result['cover_image'] or result['chinese_title'] != english_title:
                    return result
                # 再尝试豆瓣 IMDb 搜索
                result = self._search_douban_by_imdb(imdb_id, english_title)
                if result['cover_image'] or result['chinese_title'] != english_title:
                    return result

            # 清理电影标题
            cleaned_title = re.sub(r'\s*\([^)]*\)', '', english_title)
            cleaned_title = re.sub(r'\s*:\s*', ' ', cleaned_title).strip()

            # 方案1: 豆瓣搜索API
            result = self._search_douban_api(cleaned_title, year, imdb_id)
            if result['chinese_title'] != english_title or result['cover_image']:
                return result

            # 方案2: TMDb API
            if self.tmdb_api_key:
                result = self._search_tmdb_api(english_title, year)
                if result['cover_image'] or result['summary']:
                    return result

            return self._get_default_result(english_title)

        except Exception as e:
            logger.error(f"搜索电影 '{english_title}' 时发生异常: {e}")
            return self._get_default_result(english_title)

    def _get_default_result(self, title: str) -> Dict[str, str]:
        return {
            'chinese_title': title, 'douban_url': '', 'tmdb_url': '',
            'cover_image': '', 'summary': '', 'chinese_summary': '',
            'genres': [], 'directors': [], 'actors': [],
            'rating': 0, 'vote_count': 0
        }

    def _search_douban_api(self, title: str, year: str = "", imdb_id: str = "") -> Dict[str, str]:
        try:
            search_url = f"https://movie.douban.com/j/subject_suggest?q={quote(title)}"
            headers = {'Referer': 'https://movie.douban.com/', 'Accept': 'application/json'}
            response = requests.get(search_url, headers=headers, timeout=8)

            if response.status_code == 200:
                results = response.json()
                if not results: return self._get_default_result(title)

                selected_item = results[0] # 简单取第一个

                chinese_title = selected_item.get('title', title)
                douban_url = selected_item.get('url', '')
                cover_image = selected_item.get('img', '')
                if cover_image: cover_image = cover_image.replace('http://', 'https://')

                # 补充 TMDb 数据
                tmdb_info = {}
                if self.tmdb_api_key:
                    tmdb_info = self._search_tmdb_api(title, year)

                return {
                    'chinese_title': chinese_title,
                    'douban_url': douban_url,
                    'tmdb_url': tmdb_info.get('tmdb_url', ''),
                    'cover_image': cover_image or tmdb_info.get('cover_image', ''),
                    'summary': tmdb_info.get('summary', ''),
                    'chinese_summary': self._get_movie_summary(douban_url) if douban_url else tmdb_info.get('chinese_summary', ''),
                    'genres': tmdb_info.get('genres', []),
                    'directors': tmdb_info.get('directors', []),
                    'actors': tmdb_info.get('actors', []),
                    'rating': tmdb_info.get('rating', 0),
                    'vote_count': tmdb_info.get('vote_count', 0)
                }
            return self._get_default_result(title)
        except:
            return self._get_default_result(title)

    def _search_douban_by_imdb(self, imdb_id: str, english_title: str = "") -> Dict[str, str]:
        try:
            search_url = f"https://search.douban.com/movie/subject_search?search_text={imdb_id}"
            response = requests.get(search_url, headers={'Referer': 'https://www.douban.com/'}, timeout=10)
            if response.status_code == 200:
                # 豆瓣搜索结果加密，这里仅作为占位，实际很难解析window.__DATA__
                # 如果需要可以保留原来的复杂解析逻辑，这里为简化稳定性推荐 TMDb
                pass
            return self._get_default_result(english_title)
        except:
            return self._get_default_result(english_title)

    def _search_by_imdb_id(self, imdb_id: str, english_title: str = "") -> Dict[str, str]:
        if not self.tmdb_api_key: return self._get_default_result(english_title)
        try:
            if not imdb_id.startswith('tt'): imdb_id = f'tt{imdb_id}'
            url = f"https://api.themoviedb.org/3/find/{imdb_id}"
            params = {'api_key': self.tmdb_api_key, 'language': 'zh-CN', 'external_source': 'imdb_id'}
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                results = data.get('movie_results', [])
                if results:
                    movie = results[0]
                    return self._format_tmdb_movie(movie, english_title)
            return self._get_default_result(english_title)
        except:
            return self._get_default_result(english_title)

    def _search_tmdb_api(self, title: str, year: str = "") -> Dict[str, str]:
        if not self.tmdb_api_key: return self._get_default_result(title)
        try:
            url = "https://api.themoviedb.org/3/search/movie"
            params = {'api_key': self.tmdb_api_key, 'query': title, 'language': 'zh-CN'}
            if year: params['year'] = year
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return self._format_tmdb_movie(results[0], title)
            return self._get_default_result(title)
        except:
            return self._get_default_result(title)

    def _format_tmdb_movie(self, movie: Dict, default_title: str) -> Dict[str, str]:
        """格式化TMDb返回的数据"""
        movie_id = movie.get('id')

        # 获取更多详情（包括导演、演员）
        details = self._get_tmdb_movie_details(movie_id) if movie_id else {}

        return {
            'chinese_title': movie.get('title', default_title),
            'douban_url': '',
            'tmdb_url': f"https://www.themoviedb.org/movie/{movie_id}" if movie_id else '',
            'cover_image': f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get('poster_path') else '',
            'summary': '', # 英文简介暂时留空，或再调用一次API
            'chinese_summary': movie.get('overview', ''),
            'genres': details.get('genres', []),
            'directors': details.get('directors', []),
            'actors': details.get('actors', []),
            'rating': movie.get('vote_average', 0),
            'vote_count': movie.get('vote_count', 0)
        }

    def _get_tmdb_movie_details(self, movie_id: int) -> dict:
        """获取TMDb电影详情（包括导演、演员）"""
        try:
            # 获取电影基本信息和演职人员
            url = f"https://api.themoviedb.org/3/movie/{movie_id}"
            params = {'api_key': self.tmdb_api_key, 'language': 'zh-CN', 'append_to_response': 'credits'}
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                genres = [g['name'] for g in data.get('genres', [])]
                
                # 提取导演
                credits = data.get('credits', {})
                crew = credits.get('crew', [])
                directors = [person['name'] for person in crew if person.get('job') == 'Director'][:2]
                
                # 提取主演（前3位）
                cast = credits.get('cast', [])
                actors = [person['name'] for person in cast[:3]]
                
                return {
                    'genres': genres,
                    'directors': directors,
                    'actors': actors
                }
            return {}
        except:
            return {}

    def _get_movie_summary(self, douban_url: str) -> str:
        try:
            resp = requests.get(douban_url, headers={'User-Agent': HEADERS['User-Agent']}, timeout=5)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'html.parser')
                s = soup.find('span', {'property': 'v:summary'})
                return s.get_text(strip=True) if s else ""
            return ""
        except:
            return ""

    def _extract_imdb_id(self, movie_url: str) -> str:
        try:
            resp = self.session.get(movie_url, timeout=10)
            if resp.status_code != 200: return ""
            match = re.search(r'tt\d+', resp.text)
            return match.group(0) if match else ""
        except:
            return ""

    def fetch_weekend_top10(self, weekend_url: str) -> List[Dict]:
        logger.info(f"\n{'='*60}")
        logger.info(f"📅 正在抓取周末数据: {weekend_url}")
        try:
            response = self.session.get(weekend_url, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')

            path_parts = urlparse(weekend_url).path.strip('/').split('/')
            weekend_identifier = path_parts[-1] if path_parts else ""
            year = weekend_identifier[:4]

            table = soup.find('table')
            if not table:
                logger.warning("⚠️  未找到票房表格")
                return []

            rows = table.find_all('tr')[1:11]

            date_header = soup.find('h1')
            weekend_dates = date_header.get_text(strip=True) if date_header else ""
            logger.info(f"📊 周末日期: {weekend_dates}")

            movies = []
            total_movies = len(rows)
            for idx, row in enumerate(rows, 1):
                cells = row.find_all('td')
                if len(cells) < 10: continue

                title = cells[2].get_text(strip=True)
                logger.info(f"🎬 [{idx}/{total_movies}] 正在处理: {title}")

                # 获取IMDb ID
                imdb_id = ""
                link = cells[2].find('a')
                if link and link.get('href'):
                    movie_url = urljoin("https://www.boxofficemojo.com", link['href'])
                    imdb_id = self._extract_imdb_id(movie_url)
                    if imdb_id:
                        logger.info(f"   ✓ IMDb ID: {imdb_id}")

                # 获取中文信息
                logger.info(f"   🔍 搜索电影信息...")
                info = self.search_douban_movie(title, year, imdb_id)
                if info['chinese_title'] != title:
                    logger.info(f"   ✓ 中文译名: {info['chinese_title']}")

                movies.append({
                    'year': int(year) if year.isdigit() else 2025,
                    'weekend_identifier': weekend_identifier,
                    'weekend_dates': weekend_dates,
                    'rank': cells[0].get_text(strip=True),
                    'title': title,
                    'chinese_title': info['chinese_title'],
                    'cover_image': info['cover_image'],
                    'weekend_gross': cells[3].get_text(strip=True),
                    'total_gross': cells[8].get_text(strip=True),
                    'weeks': cells[9].get_text(strip=True),
                    'chinese_summary': info['chinese_summary'],
                    'summary': info['summary'],
                    'rating': info['rating'],
                    'vote_count': info['vote_count'],
                    'genres': info['genres'],
                    'directors': info.get('directors', []),
                    'actors': info.get('actors', []),
                    'douban_url': info['douban_url'],
                    'tmdb_url': info['tmdb_url']
                })
            
            logger.info(f"\n✅ 本周数据抓取完成，共 {len(movies)} 部电影")
            logger.info(f"{'='*60}\n")
            return movies
        except Exception as e:
            logger.error(f"抓取失败: {e}")
            return []

    def _parse_money_str(self, money_str: str) -> int:
        """将 '$12,345,678' 转换为整数"""
        try:
            return int(re.sub(r'[^\d]', '', money_str))
        except:
            return 0

    def generate_html_report(self, data: List[Dict], filename: str = "report.html", wechat_mode: bool = False):
        """
        生成HTML报告
        Args:
            wechat_mode: 是否为微信公众号模式（去除外链，优化排版，添加趋势图）
        """
        if not data:
            return

        # 1. 数据分组与清洗
        grouped_data = {}
        for movie in data:
            wid = movie['weekend_identifier'] # e.g., 2025W05
            if wid not in grouped_data:
                grouped_data[wid] = {'movies': [], 'total_gross_val': 0, 'dates': movie.get('weekend_dates', '')}

            grouped_data[wid]['movies'].append(movie)
            # 累加当周Top10票房总和用于趋势图
            grouped_data[wid]['total_gross_val'] += self._parse_money_str(movie['weekend_gross'])

        # 排序周次
        sorted_weeks = sorted(grouped_data.keys())
        if not sorted_weeks: return

        # 定义由于是趋势对比，我们只展示最新一周的详细列表
        # 前面的周次只用于趋势图
        current_week_id = sorted_weeks[-1]
        current_week_data = grouped_data[current_week_id]

        # 2. 生成趋势图 HTML (仅当有2周以上数据时)
        trend_html = ""
        if len(sorted_weeks) >= 2:
            max_gross = max(d['total_gross_val'] for d in grouped_data.values()) or 1

            bars_html = ""
            for wid in sorted_weeks:
                gross = grouped_data[wid]['total_gross_val']
                percent = (gross / max_gross) * 100
                is_current = (wid == current_week_id)
                bar_color = "#667eea" if not is_current else "#f5c518" # 本周高亮
                gross_million = f"${gross/1000000:.1f}M"

                # 提取简单的周数显示，如 "W05"
                week_label = wid.split('W')[-1] + "周"

                bars_html += f'''
                <div style="display: flex; flex-direction: column; align-items: center; flex: 1;">
                    <div style="font-size: 12px; color: #666; margin-bottom: 4px;">{gross_million}</div>
                    <div style="width: 30px; height: 100px; background: #eee; border-radius: 4px; display: flex; align-items: flex-end; justify-content: center; overflow: hidden;">
                        <div style="width: 100%; height: {percent}%; background: {bar_color}; border-radius: 4px 4px 0 0;"></div>
                    </div>
                    <div style="font-size: 12px; color: #333; margin-top: 4px; font-weight: bold;">{week_label}</div>
                </div>
                '''

            # 生成折线图数据点（使用固定坐标系统）
            points_data = []
            for idx, wid in enumerate(sorted_weeks):
                gross = grouped_data[wid]['total_gross_val']
                # 使用固定像素坐标，而不是百分比
                x_pos = 50 + (idx * 150)  # 每个点间隔150像素
                y_pos = 150 - (gross / max_gross) * 100  # y轴反转，从上往下
                points_data.append({'x': x_pos, 'y': y_pos, 'gross': gross, 'wid': wid})
            
            # 构建SVG折线路径
            path_points = ' '.join([f"{p['x']},{p['y']}" for p in points_data])
            
            # 生成折线图HTML
            points_html = ""
            labels_html = ""
            for idx, p in enumerate(points_data):
                wid = p['wid']
                week_label = wid.split('W')[-1] + "周"
                gross_million = f"${p['gross']/1000000:.1f}M"
                is_current = (wid == current_week_id)
                point_color = "#f5c518" if is_current else "#667eea"
                
                points_html += f'<circle cx="{p["x"]}" cy="{p["y"]}" r="5" fill="{point_color}" stroke="#fff" stroke-width="2"/>'
                points_html += f'<text x="{p["x"]}" y="{p["y"] - 15}" text-anchor="middle" font-size="12" fill="#666" font-weight="bold">{gross_million}</text>'
                labels_html += f'<text x="{p["x"]}" y="170" text-anchor="middle" font-size="13" fill="#333" font-weight="bold">{week_label}</text>'
            
            # 计算SVG宽度
            svg_width = 100 + (len(sorted_weeks) - 1) * 150
            
            trend_html = f'''
            <div class="trend-section" style="background: #fff; padding: 20px; border-radius: 12px; margin-bottom: 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #eee; overflow-x: auto;">
                <h3 style="text-align: center; color: #333; margin-bottom: 20px; font-size: 18px;">📉 近四周大盘趋势 (Top10)</h3>
                <svg viewBox="0 0 {svg_width} 200" style="width: 100%; height: 180px; min-width: 400px;">
                    <polyline points="{path_points}" fill="none" stroke="#667eea" stroke-width="2.5"/>
                    {points_html}
                    {labels_html}
                </svg>
            </div>
            '''

        # 3. 构建页面主体
        # 微信模式下，背景改为白色，移除body padding，宽度自适应
        body_style = "background: #f7f7f7; padding: 20px; font-family: sans-serif;" if not wechat_mode else "background: #fff; padding: 10px; font-family: sans-serif; max-width: 100%; overflow-x: hidden;"
        container_style = "max-width: 600px; margin: 0 auto;"
        
        # 提取年份和周次
        current_year = current_week_id[:4]
        current_week_num = current_week_id.split('W')[-1]
        title_text = f"🎬 北美票房榜{current_year}年第{current_week_num}周"

        html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>北美票房周报</title>
</head>
<body style="{body_style}">
    <div style="{container_style}">
        <h1 style="text-align: center; color: #333; font-size: 22px; margin-bottom: 10px;">{title_text}</h1>
        <p style="text-align: center; color: #888; font-size: 14px; margin-bottom: 30px;">{current_week_data['dates']}</p>

        {trend_html}
'''

        # 4. 生成电影卡片 (只生成最新一周)
        rank_colors = ["#ff4757", "#ff6b81", "#ffa502"] # 前三名颜色

        for idx, movie in enumerate(current_week_data['movies']):
            rank_display = movie['rank']
            rank_bg = rank_colors[idx] if idx < 3 else "#a4b0be"

            # 微信模式：不生成 <a> 标签，只保留文字
            # 非微信模式：保留超链接

            if wechat_mode:
                link_html = "" # 微信不放外链
            else:
                douban_btn = f'<a href="{movie["douban_url"]}" style="color:#667eea; margin-right:10px;">豆瓣</a>' if movie['douban_url'] else ''
                tmdb_btn = f'<a href="{movie["tmdb_url"]}" style="color:#667eea;">TMDb</a>' if movie['tmdb_url'] else ''
                link_html = f'<div style="margin-top: 10px; font-size: 13px;">{douban_btn} {tmdb_btn}</div>'

            # 简介截断
            summary = movie['chinese_summary'] or movie['summary'] or "暂无简介"
            if len(summary) > 60: summary = summary[:60] + "..."

            # 格式化 genres
            genres_list = movie.get('genres', [])
            if isinstance(genres_list, list):
                genres_str = " / ".join(genres_list[:3])
            else:
                genres_str = str(genres_list)
            
            # 格式化导演
            directors_list = movie.get('directors', [])
            directors_str = "导演: " + ", ".join(directors_list) if directors_list else ""
            
            # 格式化演员
            actors_list = movie.get('actors', [])
            actors_str = "主演: " + ", ".join(actors_list) if actors_list else ""

            html_content += f'''
        <div style="background: #fff; border-radius: 12px; padding: 15px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #f0f0f0; display: flex; gap: 15px;">
            <div style="flex-shrink: 0; width: 90px; position: relative;">
                <div style="position: absolute; top: -5px; left: -5px; width: 24px; height: 24px; background: {rank_bg}; color: white; border-radius: 50%; text-align: center; line-height: 24px; font-weight: bold; font-size: 12px; z-index: 2;">{rank_display}</div>
                <img src="{movie['cover_image'] or 'https://via.placeholder.com/90x135?text=No+Img'}" style="width: 90px; height: 135px; object-fit: cover; border-radius: 8px; display: block; background: #eee;">
            </div>
            <div style="flex: 1; min-width: 0;">
                <h3 style="margin: 0 0 5px 0; font-size: 16px; color: #333; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;">{movie['chinese_title']}</h3>
                <div style="font-size: 12px; color: #999; margin-bottom: 8px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;">{movie['title']}</div>

                <div style="display: flex; gap: 10px; margin-bottom: 8px;">
                    <span style="font-size: 12px; background: #fff5f5; color: #ff4757; padding: 2px 6px; border-radius: 4px;">{movie['weekend_gross']}</span>
                    <span style="font-size: 12px; background: #f0f9ff; color: #2e86de; padding: 2px 6px; border-radius: 4px;">{movie['rating']}分</span>
                </div>
                <div style="font-size: 12px; color: #bbb; margin-bottom: 4px;">{genres_str}</div>
                {f'<div style="font-size: 11px; color: #888; margin-bottom: 3px;">{directors_str}</div>' if directors_str else ''}
                {f'<div style="font-size: 11px; color: #888; margin-bottom: 6px;">{actors_str}</div>' if actors_str else ''}

                <div style="font-size: 13px; color: #666; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">{summary}</div>
                {link_html}
            </div>
        </div>
'''

        html_content += '''
    </div>
</body>
</html>'''

        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"HTML报告已生成: {filename}")

def get_last_week_identifier() -> str:
    """获取当前日期的上一周的周次标识（YYYYWW格式）"""
    from datetime import datetime, timedelta
    
    # 获取上周的日期
    last_week_date = datetime.now() - timedelta(days=7)
    
    # 计算ISO周次
    iso_calendar = last_week_date.isocalendar()
    year = iso_calendar[0]
    week = iso_calendar[1]
    
    return f"{year}{week:02d}"

def main():
    parser = argparse.ArgumentParser(description='Box Office Mojo 爬虫 (微信公众号增强版)')

    # 原有参数
    parser.add_argument('--start-week', type=str, help='周次 (格式 YYYYWW，如 202505)')
    parser.add_argument('--end-week', type=str, help='结束周次')
    parser.add_argument('--start-year', type=int)
    parser.add_argument('--end-year', type=int)
    parser.add_argument('--weeks-per-year', type=int, default=10)
    parser.add_argument('--output-prefix', type=str, default="box_office")

    # 新增参数
    parser.add_argument('--with-trend', action='store_true', help='自动抓取包含本周在内的前4周数据，生成趋势图')
    parser.add_argument('--wechat', action='store_true', help='生成微信公众号专用格式（去除外链、优化排版）')

    args = parser.parse_args()

    scraper = BoxOfficeScraper()
    movies_data = []

    # 默认逻辑：如果没有任何参数，自动获取上周数据并生成趋势图
    target_week = args.start_week or args.end_week
    
    if not target_week and not args.start_year and not args.end_year:
        # 没有任何参数，使用默认逻辑
        target_week = get_last_week_identifier()
        logger.info(f"✨ 默认模式：自动获取上周数据 ({target_week})")
        args.with_trend = True  # 强制启用趋势模式
        args.wechat = True  # 默认启用微信模式

    # 逻辑增强
    if args.with_trend and target_week:
        # 如果开启趋势模式，且指定了某一周，则自动计算前3周
        # 仅取 latest week 作为基准
        week_list = scraper.get_previous_weeks(target_week, n=3) # 获取前3周
        week_list.append(target_week) # 加上本周
        logger.info(f"开启趋势模式，将爬取以下周次: {week_list}")
        movies_data = scraper.crawl_by_week_list(week_list)

    elif args.start_week and args.end_week:
        movies_data = scraper.crawl_by_week_range(args.start_week, args.end_week)

    elif args.start_year and args.end_year:
        # 简单循环年份调用
        movies_data = scraper.crawl_by_week_range(f"{args.start_year}01", f"{args.end_year}52")

    # 生成文件
    if movies_data:
        logger.info("\n" + "="*60)
        logger.info("📝 开始生成HTML报告...")
        html_filename = f"{args.output_prefix}.html"
        scraper.generate_html_report(movies_data, html_filename, wechat_mode=args.wechat)
        logger.info(f"\n🎉 任务完成！")
        logger.info(f"📄 报告文件: {os.path.abspath(html_filename)}")
        logger.info(f"📊 包含电影数: {len(movies_data)} 部")
        logger.info("="*60 + "\n")
    else:
        logger.error("\n❌ 未获取到任何数据，请检查网络或参数设置")

if __name__ == "__main__":
    main()