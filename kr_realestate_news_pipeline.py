# -*- coding: utf-8 -*-
"""
KR Real Estate News Pipeline (macro-focused)
- v31.10: '기사 보기' 텍스트를 실제 URL로 변경 및 링크 유지
"""

import os
import sys
import re
import html
import time
import requests
import logging
import warnings
from datetime import datetime
from dateutil import parser as dtparser
import pytz
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import trafilatura
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# 억제 설정
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "none")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
warnings.filterwarnings("ignore")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception: pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ───────────────────────────────────────────────────────────────────────────────
# Config
# ───────────────────────────────────────────────────────────────────────────────
class Config:
    VERSION = "v31.10 (Link Display Fix)" 
    KST = pytz.timezone("Asia/Seoul")
    NAVER_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"

    load_dotenv()
    raw_key = os.getenv("GOOGLE_API_KEY", "")
    GOOGLE_API_KEY = raw_key.strip().replace('"', '').replace("'", "")
    
    NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
    NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
    USE_GEMINI = os.getenv("USE_GEMINI", "1") == "1" and bool(GOOGLE_API_KEY)
    DEDUPE_METHOD = os.getenv("DEDUPE_METHOD", "GEMINI").upper()
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    REQ_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}

    WHITELIST = {"매일경제","한국경제","서울경제","조선일보","중앙일보","동아일보","한겨레","경향신문","연합뉴스","뉴시스","조선비즈","머니투데이","파이낸셜뉴스","KBS","SBS","MBC","정책브리핑"}
    DOMAIN2OUTLET = {"mk.co.kr":"매일경제","hankyung.com":"한국경제","sedaily.com":"서울경제","chosun.com":"조선일보","joongang.co.kr":"중앙일보","donga.com":"동아일보","hani.co.kr":"한겨레","khan.co.kr":"경향신문","yna.co.kr":"연합뉴스","newsis.com":"뉴시스","biz.chosun.com":"조선비즈","mt.co.kr":"머니투데이","fnnews.com":"파이낸셜뉴스","news.kbs.co.kr":"KBS","news.sbs.co.kr":"SBS","imnews.imbc.com":"MBC","korea.kr":"정책브리핑"}

    CORE_IN_TITLE = ["부동산", "주택", "아파트", "청약", "시장", "주거", "집값", "공급", "대책", "금리", "대출", "재건축", "재개발"]
    ALL_KWS = list(set(CORE_IN_TITLE + ["미분양", "LTV", "DSR", "양도세", "종부세"]))
    QUERY_TERMS = ["아파트값", "부동산 대책", "공급 대책", "재건축", "미분양", "LTV DSR", "주택담보대출"]
    BLACKLIST = ["사설", "칼럼", "부고", "인사", "운세", "포토", "이벤트", "경품"]
    TITLE_BONUS = ["발표", "시행", "확대", "완화", "공급", "대책", "규제", "금리"]
    TITLE_PENALTY = ["설문", "전망", "예상", "인터뷰", "의견", "전문가", "해외"]

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(Config.REQ_HEADERS)
    return s
SESSION = make_session()

def clean(s: str) -> str:
    if not s: return ""
    s = re.sub(r'<[^>]*>', '', s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def send_email_report(html_content: str, subject: str):
    sender = os.getenv("EMAIL_USER"); receiver = os.getenv("EMAIL_RECEIVER"); pwd = os.getenv("EMAIL_PASSWORD")
    if not all([sender, receiver, pwd]): return
    msg = MIMEMultipart(); msg['Subject'] = subject; msg['From'] = sender; msg['To'] = receiver
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))
    try:
        smtp_server = "smtp.gmail.com" if "@gmail.com" in sender else "smtp.naver.com"
        with smtplib.SMTP(smtp_server, 587) as server:
            server.starttls(); server.login(sender, pwd); server.sendmail(sender, receiver, msg.as_string())
        logging.info("이메일 전송 성공!")
    except Exception as e: logging.error(f"이메일 전송 실패: {e}")

# ── Gemini (Google Generative AI) ──────────────────────────────
gem_model = None
if Config.USE_GEMINI:
    try:
        import google.generativeai as genai
        genai.configure(api_key=Config.GOOGLE_API_KEY)
        gem_model = genai.GenerativeModel("models/gemini-2.5-flash")
    except:
        Config.USE_GEMINI = False

def ai_summarize_gemini(text: str) -> str:
    if not gem_model or not text: return ""
    safety = [{"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
    prompt = f"부동산 전문 기자로서 다음 기사를 3-5문장의 줄글로 요약하세요. '~다'로 끝나야 합니다.\n\n[본문]\n{text}"
    try:
        resp = gem_model.generate_content(prompt, safety_settings=safety)
        out = (resp.text or "").strip()
        sents = re.findall(r'[^.!?]+(?:[다요]\.|[.!?])', out)
        return " ".join([s.strip() for s in sents if s.strip()]) or out
    except Exception as e:
        print(f"[AI Error] {e}"); return ""

# ── Processing Functions ──────────────────────────────────────────
def process_article(item: dict, since: datetime, until: datetime) -> dict | None:
    try:
        from urllib.parse import urlparse
        link = re.sub(r"\?.*$", "", item.get("originallink") or item.get("link", ""))
        outlet = ""
        h = urlparse(link).netloc.lower()
        for dom, name in Config.DOMAIN2OUTLET.items():
            if dom in h: outlet = name; break

        if "news.naver.com" in link:
            r = SESSION.get(link, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            press = (soup.select_one(".media_end_head_top_logo img[alt]") or {}).get("alt", "").strip()
            if press in Config.WHITELIST: outlet = press
            html_content = r.text
        else: html_content = SESSION.get(link, timeout=10).text

        if outlet not in Config.WHITELIST: return None
        body = trafilatura.extract(html_content)
        if not body or sum(1 for k in Config.ALL_KWS if k in body) < 2: return None

        summary = ai_summarize_gemini(body) if Config.USE_GEMINI else ""
        if not summary:
            summary = clean(item.get("description", ""))

        return {
            "title": clean(item['title']), "summary": summary, 
            "date": datetime.now(Config.KST).strftime("%Y-%m-%d"),
            "outlet": outlet, "link": link, "pub_kst_dt": item.get('pub_kst', datetime.now(Config.KST))
        }
    except: return None

def are_summaries_similar_by_keyword(s1, s2):
    stop = {"서울","지역","정부","시장","경제","정책","부동산","아파트"}
    def tok(s): return {w for w in re.sub(r"[^\w\s]", " ", s).split() if len(w)>1 and w not in stop}
    t1, t2 = tok(s1), tok(s2)
    return (len(t1 & t2) / len(t1 | t2)) >= 0.4 if t1 and t2 else False

def filter_diverse_articles(articles, target=5):
    res, outlets = [], {}
    for a in articles:
        if len(res) >= target: break
        if outlets.get(a["outlet"], 0) >= 2: continue
        if any(are_summaries_similar_by_keyword(a['summary'], ex['summary']) for ex in res): continue
        res.append(a); outlets[a["outlet"]] = outlets.get(a["outlet"], 0) + 1
    return res

def main():
    candidate_pool, seen_links = [], set()
    headers = {"X-Naver-Client-Id": Config.NAVER_CLIENT_ID, "X-Naver-Client-Secret": Config.NAVER_CLIENT_SECRET}
    
    for q in Config.QUERY_TERMS:
        try:
            r = SESSION.get(Config.NAVER_ENDPOINT, headers=headers, params={"query": q, "display": 50, "sort": "date"})
            if r.status_code != 200: continue
            for it in r.json().get("items", []):
                link = re.sub(r"\?.*$", "", it.get("originallink") or it.get("link", ""))
                if link in seen_links: continue
                title = clean(it['title'])
                if any(k in title for k in Config.BLACKLIST) or not any(k in title for k in Config.CORE_IN_TITLE): continue
                it['pub_kst'] = dtparser.parse(it.get("pubDate","")).astimezone(Config.KST)
                candidate_pool.append(it); seen_links.add(link)
        except: continue

    if not candidate_pool: return logging.info("기사 없음")
    candidate_pool.sort(key=lambda x: x['pub_kst'], reverse=True)

    processed = []
    with ThreadPoolExecutor(max_workers=10) as exe:
        futures = [exe.submit(process_article, it, None, None) for it in candidate_pool[:50]]
        for f in as_completed(futures):
            if res := f.result(): processed.append(res)

    processed.sort(key=lambda x: sum(1 for k in Config.TITLE_BONUS if k in x['title']) - sum(0.8 for k in Config.TITLE_PENALTY if k in x['title']), reverse=True)
    results = filter_diverse_articles(processed, target=5)
    
    if len(results) < 5:
        seen = {r['link'] for r in results}
        for a in processed:
            if len(results) >= 5: break
            if a['link'] not in seen: results.append(a); seen.add(a['link'])

    if not results: return
    subject = f"📰 [{datetime.now(Config.KST).strftime('%Y-%m-%d')}] 부동산 핵심 브리핑"
    html_body = f"<html><body style='font-family:sans-serif;'><h2>{subject}</h2>"
    for i, r in enumerate(results, 1):
        html_body += f"<div style='border-bottom:1px solid #eee; padding:15px;'>"
        html_body += f"<h3 style='color:#2c3e50;'>{i}. [{r['outlet']}] {r['title']}</h3>"
        html_body += f"<p style='line-height:1.6; color:#34495e;'>{r['summary']}</p>"
        html_body += f"<span>({r['date']})</span><br>"
        # [핵심 수정] '기사 보기' 텍스트를 실제 URL로 변경
        html_body += f"<a href='{r['link']}' style='color:#3498db; text-decoration:none; font-size:0.9em;'>{r['link']}</a></div>"
    html_body += "</body></html>"
    
    send_email_report(html_body, subject)
    print(f"\n{subject} 전송 완료 (뉴스 {len(results)}건)")

if __name__ == "__main__":
    main()
