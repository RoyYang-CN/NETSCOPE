import os
import json
from wechatpy import WeChatClient
from bs4 import BeautifulSoup
import requests
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 从环境变量获取密钥 (在 GitHub Actions 里配置)
APP_ID = os.environ.get("WECHAT_APP_ID")
APP_SECRET = os.environ.get("WECHAT_APP_SECRET")

def upload_cover_image(client, html_content):
    """
    从HTML中提取第一张图片（冠军海报），下载并上传到微信获取 media_id
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        # 寻找第一个电影海报
        img_tag = soup.find('img')
        
        if not img_tag or not img_tag.get('src'):
            logger.warning("HTML中没找到图片，将使用纯文本封面")
            return None

        img_url = img_tag['src']
        logger.info(f"下载封面图: {img_url}")
        
        # 下载图片
        res = requests.get(img_url)
        res.raise_for_status()
        
        # 保存为临时文件
        temp_filename = "cover.jpg"
        with open(temp_filename, "wb") as f:
            f.write(res.content)
            
        # 上传到微信永久素材库 (草稿封面需要)
        with open(temp_filename, "rb") as f:
            # 微信接口要求文件对象
            res_upload = client.material.add("image", f)
            media_id = res_upload['media_id']
            logger.info(f"封面上传成功，media_id: {media_id}")
            return media_id

    except Exception as e:
        logger.error(f"上传封面失败: {e}")
        return None

def main():
    if not APP_ID or not APP_SECRET:
        logger.error("❌ 未检测到 WECHAT_APP_ID 或 WECHAT_APP_SECRET 环境变量")
        return

    # 1. 初始化客户端
    client = WeChatClient(APP_ID, APP_SECRET)
    
    # 2. 读取生成的 HTML 内容
    # 注意：文件名要和 box_office_scraper.py 默认生成的一致 (box_office.html)
    html_file = "box_office.html" 
    if not os.path.exists(html_file):
        logger.error(f"❌ 未找到报告文件: {html_file}")
        return

    with open(html_file, "r", encoding="utf-8") as f:
        content = f.read()

    # 3. 准备封面图
    # 尝试提取 HTML 里的第一张图作为封面，如果失败可能需要一个默认 media_id
    thumb_media_id = upload_cover_image(client, content)
    
    # 如果实在没有图片，这里必须填一个你公众号后台已有的图片 media_id，否则会报错
    # 建议手动在后台传一张通用图，把 ID 填在下面作为兜底
    # if not thumb_media_id:
    #     thumb_media_id = "YOUR_FALLBACK_MEDIA_ID"

    if not thumb_media_id:
        logger.error("❌ 无法生成封面图 ID，停止上传")
        return

    # 4. 创建草稿
    # 提取标题，例如从 HTML title 标签或者文件名
    # 这里简单处理，假设 HTML 里有 h1
    soup = BeautifulSoup(content, 'html.parser')
    title_tag = soup.find('h1')
    title = title_tag.get_text(strip=True) if title_tag else "本周北美票房快报"
    
    # 摘要 (Digest) - 可选
    digest = f"最新北美票房数据出炉，本周冠军是..."

    article_data = {
        "title": title,
        "author": "BoxOfficeBot",
        "digest": digest,
        "content": content, # 直接塞入 HTML
        "content_source_url": "https://www.boxofficemojo.com",
        "thumb_media_id": thumb_media_id,
        "show_cover_pic": 1,
        "need_open_comment": 1,
        "only_fans_can_comment": 0
    }

    try:
        # 调用新增草稿接口
        # wechatpy 的 draft 接口在 1.8.18+ 版本支持，如果报错请检查版本
        # 或者使用 draft.add (新版) / material.add_articles (旧版，现已转为草稿)
        # 微信接口更新后，add_articles 返回的就是草稿
        res = client.draft.add(articles=[article_data])
        logger.info("✅ 公众号草稿创建成功！")
        logger.info(f"Media ID: {res['media_id']}")
        logger.info("请登录微信公众号后台 -> 草稿箱 查看并发布。")
        
    except Exception as e:
        logger.error(f"❌ 草稿创建失败: {e}")
        # 尝试旧版接口兼容
        try:
            logger.info("尝试使用旧版接口...")
            res = client.material.add_articles(articles=[article_data])
            logger.info("✅ (旧接口) 草稿创建成功！")
        except Exception as e2:
            logger.error(f"❌ 旧接口也失败了: {e2}")

if __name__ == "__main__":
    main()