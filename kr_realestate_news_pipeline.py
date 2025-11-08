# -*- coding: utf-8 -*-
"""
KR Real Estate News Pipeline (macro-focused)
- v31.1 Email Report: 1차 필터링 후 5개 미만 시 2차 필터링으로 5개 채우기
"""

# ── gRPC/ABSL 경고 억제 ──────────────────────────────────────────────────────
import os
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "none")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

# 표준 출력 인코딩 고정
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import re, html, time, requests, traceback, warnings, logging, json
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timedelta
from dateutil import parser as dtparser
import pytz
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import trafilatura
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── (추가) 이메일 라이브러리 ──────────────────────────────────
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
# ───────────────────────────────────────────────────────────

warnings.filterwarnings("ignore")

# ── 로깅 설정 ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ───────────────────────────────────────────────────────────────────────────────
# Version / Config
# ───────────────────────────────────────────────────────────────────────────────
class Config:
    VERSION = "v31.1 (Email Report, Fill-5)" # 버전명 수정
    KST = pytz.timezone("Asia/Seoul")
    NAVER_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"

    # API 키 (환경 변수에서 로드)
    load_dotenv()
    NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
    NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    USE_GEMINI = os.getenv("USE_GEMINI", "1") == "1" and bool(GOOGLE_API_KEY)
    
    # (삭제) 카카오톡 API 키 부분 제거
    
    # 중복 제거 방식 선택 ("KEYWORD" 또는 "GEMINI")
    DEDUPE_METHOD = os.getenv("DEDUPE_METHOD", "KEYWORD").upper()

    # 요청 헤더
    USER_AGENT = "KR-RE-NEWS/" + VERSION
    REQ_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}

    # 허용 언론사
    WHITELIST = {
        "매일경제","한국경제","서울경제","조선일보","중앙일보","동아일보","한겨레","경향신문",
        "연합뉴스","뉴시스","조선비즈","머니투데이","파이낸셜뉴스","KBS","SBS","MBC","정책브리핑"
    }
    DOMAIN2OUTLET = {
        "mk.co.kr":"매일경제","hankyung.com":"한국경제","sedaily.com":"서울경제",
        "chosun.com":"조선일보","joongang.co.kr":"중앙일보","donga.com":"동아일보",
        "hani.co.kr":"한겨레","khan.co.kr":"경향신문","yna.co.kr":"연합뉴스",
        "newsis.com":"뉴시스","biz.chosun.com":"조선비즈","mt.co.kr":"머니투데이",
        "fnnews.com":"파이낸셜뉴스","news.kbs.co.kr":"KBS","news.sbs.co.kr":"SBS",
        "imnews.imbc.com":"MBC","korea.kr":"정책브리핑"
    }

    # ==============================================================================
    # (수정) 키워드 및 필터링 규칙
    # ==============================================================================

    # 1. 10개 카테고리별 세부 키워드 (필터링/가중치용)
    MARKET_KWS = ["아파트값", "집값", "매매가", "전세가", "상승", "하락", "보합", "급등", "급락", "반등", "거래량", "거래절벽", "매물 적체", "매수 심리", "관망세", "갭투자", "외지인 투자", "낙찰가율", "경매"]
    POLICY_KWS = ["부동산 대책", "주거 안정", "1.10 대책", "임대차 3법", "전월세 상한제", "계약갱신청구권", "주거 사다리", "청년 주거", "신혼부부 지원", "공시가격 현실화", "국토교통부", "기획재정부", "금융위원회", "한국은행"]
    REGULATION_KWS = ["규제 완화", "규제 강화", "투기과열지구", "조정대상지역", "규제지역 해제", "분양가 상한제", "분상제", "실거주 의무", "토지거래허가구역", "자금조달계획서"]
    FINANCE_KWS = ["기준금리", "금리 인상", "금리 인하", "코픽스", "COFIX", "가계부채", "PF", "프로젝트 파이낸싱", "부실", "연체율", "주택금융공사", "HF", "HUG", "주택연금"]
    LOAN_KWS = ["LTV", "DTI", "DSR", "스트레스 DSR", "주택담보대출", "주담대", "전세자금대출", "디딤돌대출", "보금자리론", "특례보금자리론", "신생아 특례대출", "대출 한도", "가산금리", "중도금 대출"]
    TAX_KWS = ["양도세", "취득세", "종부세", "재산세", "증여세", "상속세", "비과세", "감면", "중과", "유예", "양도세 중과 유예", "1가구 1주택", "다주택자", "일시적 2주택", "분양권 세금", "세법 개정"]
    SUPPLY_KWS = ["공급 대책", "입주 물량", "공급 가뭄", "공공분양", "민간분양", "사전 청약", "공공임대", "3기 신도시", "신도시", "택지지구", "LH", "인허가"]
    DEVELOPMENT_KWS = ["재개발", "재건축", "리모델링", "신속통합기획", "신통기획", "모아타운", "역세권 개발", "재건축초과이익환수제", "재초환", "안전진단", "GTX", "신설역", "SOC", "정비사업"]
    SUBSCRIPTION_KWS = ["청약", "청약 경쟁률", "줍줍", "무순위 청약", "미계약", "청약 가점", "당첨선", "미분양", "악성 미분양", "완판", "분양가", "모델하우스", "견본주택"]
    BROKERAGE_KWS = ["공인중개사법", "중개보수", "수수료", "실거래가", "부동산 플랫폼", "허위 매물", "전자계약", "전세 사기", "이중계약", "확인설명서", "중개업소"]

    # 2. 핵심 검색 및 필터링 리스트
    
    # 2-0. (*** 수정 ***) 제목 1차 필터링용 핵심 명사 (포괄적 단어 추가)
    # Naver 검색 직후 1차 필터링에 사용됨. (has_core_in_title)
    CORE_IN_TITLE = ["부동산", "주택", "아파트", "청약", "시장", "주거", # <-- 5개 못 채우는 문제 해결용 포괄 키워드
                     "집값","아파트값","매매가격","전세가격","전셋값","가격지수","KB시세","한국부동산원",
                     "거래량","거래절벽","매물","수급","공급","입주물량","분양물량","미분양",
                     "대책","공급대책","규제지역","토지거래허가","정비사업","재건축","재개발",
                     "금리","기준금리","LTV","DSR","대출규제","전세대출","보유세","종부세","취득세","양도세"]

    # 2-1. (대체) 전체 부동산 키워드 리스트 (모든 카테고리 통합)
    # 본문 내용 체크(body_is_real_estate)에 사용됨. 기존 REAL_ESTATE_KWS 대체.
    ALL_KWS = list(set(
        MARKET_KWS + POLICY_KWS + REGULATION_KWS + FINANCE_KWS + LOAN_KWS +
        TAX_KWS + SUPPLY_KWS + DEVELOPMENT_KWS + SUBSCRIPTION_KWS + BROKERAGE_KWS +
        CORE_IN_TITLE # 1차 필터 키워드도 당연히 포함
    ))

    # 2-2. (수정) 핵심 검색어 리스트 (기사 수집용)
    QUERY_TERMS = [
        "아파트값", "전세가", "부동산 거래량", "미분양", "입주물량",                 # 시장
        "부동산 대책", "공급 대책", "국토부 발표", "세법 개정",                       # 정책/세금
        "규제지역 해제", "토지거래허가구역", "분양가 상한제", "실거주 의무",           # 규제
        "GTX", "재건축", "재개발", "신통기획", "모아타운",                           # 개발
        "LTV", "DSR", "스트레스 DSR", "주택담보대출", "신생아 특례대출", "전세사기",  # 금융/대출
        "청약 경쟁률", "줍줍", "사전 청약",                                           # 분양
        "한국부동산원", "KB시세"                                                     # 지표
    ]
    
    # 2-3. (수정) 블랙리스트 (기사 제외)
    BLACKLIST = [
        # 기존 리스트
        "사설", "칼럼", "opinion", "기고", "만평", "상담", "연예", "게임", "스포츠",
        "화재", "폭발", "사고", "체납", "횡령", "체포", "ETF", "펀드", "주식",
        "채권", "선물", "옵션", "코인", "비트코인", "웹3", "가상자산",
        # 추가 리스트
        "부고", "인사", "동정", "운세", "포토", "날씨", "증시", "환율",           # 비관련 섹션
        "이벤트", "경품", "추첨", "무료", "할인", "광고", "홍보", "협찬",           # 광고/스팸
        "OOO 기자" # 가끔 기자 이름이 제목에 포함될 때
    ]

    # 2-4. (수정) 기사 제목 페널티 키워드 (중요도 DOWN)
    TITLE_PENALTY = [
        "설문", "전망", "예상", "예측", "인터뷰", "분석", "의견", "전문가", "관계자", # 추측/의견
        "화제", "눈길", "이유는", " 살펴보니",                                 # 홍보/가십
        "오피스텔", "상가", "지식산업센터", "빌라", "생활형숙박시설",              # (아파트 외)
        "해외", "미국", "중국"                                                  # 해외 부동산
    ]

    # 2-5. (수정) 기사 제목 보너스 키워드 (중요도 UP)
    TITLE_BONUS = [
        "발표", "시행", "확대", "완화", "강화", "도입", "폐지", "유예", "개정",    # 정책/규제 동사
        "공급", "대책", "규제", "세제", "대출", "특례", "LTV", "DSR", "재초환", # 핵심 정책
        "GTX", "재건축", "재개발", "신통기획", "모아타운", "안전진단", "인허가",   # 핵심 개발
        "미분양", "실거래", "거래량", "급등", "급락", "LH", "HUG", "국토부"       # 핵심 현상/기관
    ]
    # ==============================================================================

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(["GET", "POST"]))
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(Config.REQ_HEADERS)
    return s
SESSION = make_session()

# ── (삭제) KakaoTalk Functions 섹션 전체 제거 ───────────────────────────────────

# ── (유지) Email Function ───────────────────────────────────────────────────────
def send_email_report(html_content: str, subject: str):
    """지정된 이메일로 HTML 리포트를 발송합니다."""
    
    sender_email = os.getenv("EMAIL_USER")
    receiver_email = os.getenv("EMAIL_RECEIVER")
    app_password = os.getenv("EMAIL_PASSWORD")

    if not all([sender_email, receiver_email, app_password]):
        logging.warning("이메일 환경변수(USER, PASSWORD, RECEIVER)가 없어 발송을 건너뜁니다.")
        return

    # --- SMTP 서버 설정 (Gmail / Naver) ---
    if "@gmail.com" in sender_email:
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
    elif "@naver.com" in sender_email:
        smtp_server = "smtp.naver.com"
        smtp_port = 587 # 또는 465 (SSL)
    else:
        logging.error("지원되지 않는 이메일 도메인입니다. (Gmail/Naver만 자동 지원)")
        return
    
    try:
        # --- 메시지 본문 구성 ---
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        # --- 서버 연결 및 발송 ---
        logging.info(f"{smtp_server}에 연결하여 이메일 발송을 시도합니다...")
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # TLS 암호화
            server.login(sender_email, app_password)
            server.sendmail(sender_email, receiver_email, msg.as_string())
        
        logging.info("이메일 전송 성공!")
    
    except Exception as e:
        logging.error(f"이메일 전송 중 오류 발생: {e}")

# ── Gemini (Google Generative AI) & Deduplication ──────────────────────────────
gem_model = None
if Config.USE_GEMINI:
    try:
        import absl.logging
        absl.logging.set_verbosity(absl.logging.ERROR)
        import google.generativeai as genai
        genai.configure(api_key=Config.GOOGLE_API_KEY)
        gem_model = genai.GenerativeModel("gemini-pro-latest")
    except Exception as e:
        logging.warning(f"Gemini 초기화 실패: {e}")
        Config.USE_GEMINI = False
        gem_model = None

def ai_summarize_gemini(text: str) -> str:
    if not gem_model or not text: return ""
    
    # 지시사항과 실제 텍스트(text)를 f-string으로 결합
    full_prompt = f"""다음 한국어 뉴스 본문을 3~5개의 완벽한 문장으로 요약해줘. 각 문장은 '다'나 '요'로 끝나야 해. 핵심 데이터, 정책, 시장 동향을 포함하고 사실 중심으로 작성해줘. 서론이나 부연 설명 없이 요약 문장으로 바로 시작해줘.

[뉴스 본문]
{text}
"""
    
    try:
        # 수정: 'prompt' 대신 'full_prompt'를 전달
        resp = gem_model.generate_content(full_prompt)  
        out = (resp.text or "").strip()
        
        # 후처리 로직은 기존과 동일
        sents = re.findall(r'[^.!?]+(?:[다요]\.|[.!?])', out)
        sents = [s.strip() for s in sents if s.strip()]
        return " ".join(sents)
    
    except Exception as e:
        logging.error(f"Gemini 요약 중 오류 발생: {e}")
        return ""

def are_summaries_similar_by_keyword(s1: str, s2: str, thr: float = 0.4) -> bool:
    stop = {"서울","지역","정부","시장","경제","기자","정책","부동산","아파트","관련","위해","대한","따르면","밝혔다","전망","예상","올해","내년"}
    def tokenize(s):
        return {w for w in re.sub(r"['\"“”‘’\[\]\(\)\.]", " ", s.lower()).split() if len(w) > 1 and w not in stop}
    toks1, toks2 = tokenize(s1), tokenize(s2)
    if not toks1 or not toks2: return False
    return (len(toks1 & toks2) / len(toks1 | toks2)) >= thr

def filter_diverse_articles_by_keyword(articles, target=5, max_per_outlet=2):
    results = []
    outlet_count = {}
    for article in articles:
        if len(results) >= target: break
        if outlet_count.get(article["outlet"], 0) >= max_per_outlet: continue
        is_too_similar = any(are_summaries_similar_by_keyword(article['summary'], ex['summary']) for ex in results)
        if not is_too_similar:
            results.append(article)
            outlet_count[article["outlet"]] = outlet_count.get(article["outlet"], 0) + 1
    return results

def filter_diverse_articles_by_gemini(articles, target=5):
    if not gem_model:
        logging.warning("Gemini 모델이 없어 키워드 기반 중복 제거를 실행합니다.")
        return filter_diverse_articles_by_keyword(articles, target)

    logging.info("Gemini를 이용한 주제 그룹핑 및 대표 기사 선정을 시작합니다...")
    
    # AI에게 전달할 후보군을 15개로 제한하여 비용 및 시간 효율화
    candidate_articles = articles[:15]
    
    article_candidates_str = ""
    for i, article in enumerate(candidate_articles):
        article_candidates_str += f"ID: {i}\n"
        article_candidates_str += f"제목: {article['title']}\n"
        article_candidates_str += f"요약: {article['summary']}\n---\n"

    prompt = f"""당신은 한국 부동산 뉴스 전문 편집장입니다. 아래는 오늘 수집된 여러 뉴스 기사의 요약문 목록입니다.
    
    [임무]
    1. 모든 기사를 읽고 내용이 의미적으로 같은 핵심 사건(event)이나 주제를 다루는 것끼리 그룹으로 묶어주세요.
    2. 각 그룹에서 가장 내용을 포괄적이고 잘 설명하는 대표 기사 'ID'를 하나씩만 선택해주세요.
    3. 최종적으로 선택된 대표 기사들의 'ID'를 쉼표(,)로 구분하여 {target}개만 알려주세요. 다른 설명은 절대 추가하지 마세요.

    [기사 목록]
    {article_candidates_str}

    [출력 형식]
    ID1,ID2,ID3,ID4,ID5
    """
    
    try:
        resp = gem_model.generate_content(prompt)
        selected_ids_str = (resp.text or "").strip()
        selected_ids = [int(id_str.strip()) for id_str in selected_ids_str.split(',') if id_str.strip().isdigit()]
        
        if not selected_ids:
            raise ValueError("Gemini가 유효한 ID를 반환하지 않았습니다.")

        logging.info(f"Gemini가 선정한 대표 기사 ID: {selected_ids}")
        
        results = [candidate_articles[i] for i in selected_ids if i < len(candidate_articles)]
        return results[:target]

    except Exception as e:
        logging.error(f"Gemini 그룹핑 실패, 키워드 방식으로 대체합니다: {e}")
        return filter_diverse_articles_by_keyword(articles, target)

# ── Helper, Fetching, Parsing, Reranking Functions ─────────────────
def host(url:str) -> str:
    try: return urlparse(url).netloc.lower()
    except: return ""
def norm_link(u:str) -> str:
    try: return urlunparse((urlparse(u).scheme, urlparse(u).netloc, urlparse(u).path, "", "", ""))
    except: return u or ""
def normalize_news_url(u:str)->str:
    return re.sub(r"\?.*$", "", norm_link(u))
def looks_like_article_url(u:str)->bool:
    return any(re.search(p, u) for p in [r"sedaily\.com", r"hankyung\.com", r"yna\.co\.kr", r"sbs\.co.kr", r"chosun\.com", r"mk\.co.kr", r"mt\.co.kr", r"fnnews\.com", r"newsis\.com", r"korea\.kr", r"joongang\.co.kr", r"hani\.co\.kr", "khan\.co\.kr", r"kbs\.co\.kr", r"imbc\.com"])
def outlet_from_url(url:str) -> str:
    h = host(url)
    for dom, name in Config.DOMAIN2OUTLET.items():
        if dom in h: return name
    return ""
def norm_title(t:str) -> str:
    # 1. 앞/뒤 공백 및 여러 공백을 하나로 합침
    t = re.sub(r"\s+", " ", t).strip()
    
    # 2. [속보], [단독] 같은 접두사 제거 (기존 로직)
    t = re.sub(r"^\[[^\]]+\]\s*", "", t)
    
    # 3. (추가) "- 언론사명" 형태의 접미사 제거
    # Config.WHITELIST에 있는 언론사명 리스트를 기반으로 정규식 생성
    suffix_pattern = r"\s*-\s*(매일경제|한국경제|서울경제|조선일보|중앙일보|동아일보|한겨레|경향신문|연합뉴스|뉴시스|조선비즈|머니투데이|파이낸셜뉴스|KBS|SBS|MBC|정책브리핑)$"
    t = re.sub(suffix_pattern, "", t).strip()
    
    return t
def clean(s:str) -> str:
    return re.sub(r"\s+"," ", html.unescape(s or "")).strip()
def has_core_in_title(title:str) -> bool: return any(k in title for k in Config.CORE_IN_TICLE)
def has_blacklist(txt:str) -> bool: 
    return any(k in txt for k in Config.BLACKLIST)

# (*** 수정 ***) Config.REAL_ESTATE_KWS -> Config.ALL_KWS로 변경
def body_is_real_estate(text:str, min_hits:int=2)->bool:
    return sum(1 for k in Config.ALL_KWS if k in text) >= min_hits

def naver_search(q:str, display=30, pages=3):
    headers={"X-Naver-Client-Id":Config.NAVER_CLIENT_ID, "X-Naver-Client-Secret":Config.NAVER_CLIENT_SECRET}
    items=[]
    for i in range(pages):
        start = 1 + i*display
        try:
            r = SESSION.get(Config.NAVER_ENDPOINT, headers=headers, params={"query": q, "display": display, "start": start, "sort": "date"}, timeout=10)
            r.raise_for_status()
            items.extend(r.json().get("items", []))
        except requests.exceptions.RequestException as e:
            logging.warning(f"Naver search failed for '{q}': {e}")
            break
    return items
def fetch_html(url:str) -> str:
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code == 200: return r.text
    except: pass
    return ""
def parse_page_datetime_kst(html_text:str):
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for sel in ["meta[property='article:published_time']", "meta[name='date']", "time"]:
            if n := soup.select_one(sel):
                if v:= (n.get("content") or n.get_text()):
                    dt = dtparser.parse(v.strip())
                    return dt.astimezone(Config.KST) if dt.tzinfo else Config.KST.localize(dt)
    except: pass
    return None
def extract_full_title_from_html(html_text: str) -> str:
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        if og_title := soup.select_one("meta[property='og:title']"):
            return og_title.get("content", "")
        if title_tag := soup.select_one("title"):
            return title_tag.get_text("", strip=True)
    except: pass
    return ""
def resolve_naver_press_and_canonical(url:str):
    try:
        html_text = fetch_html(url)
        if not html_text: return "", url, None, None
        soup = BeautifulSoup(html_text, "html.parser")
        press = (soup.select_one(".media_end_head_top_logo img[alt]") or {}).get("alt", "").strip()
        can_url = (soup.select_one("link[rel='canonical']") or {}).get("href", url).strip()
        page_dt = parse_page_datetime_kst(html_text)
        return press, normalize_news_url(can_url), page_dt, html_text
    except: return "", url, None, None

def extract_text_from_html(url:str, html_text:str) -> str:
    try:
        extracted = trafilatura.extract(html_text, url=url)
        if extracted and len(extracted.split()) > 60: 
            return extracted.strip()
    except Exception as e:
        logging.warning(f"Trafilatura failed for {url}, falling back to BeautifulSoup: {e}")

    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for sel in ["#dic_area", ".newsct_article", "article", "#articleBody", ".article_body", ".art_txt", ".article-txt", "#news_view", "#content", ".view_con", ".text_area"]:
            if node := soup.select_one(sel):
                text = node.get_text(" ", strip=True)
                if len(text.split()) > 60: 
                    return text
    except Exception as e:
        logging.warning(f"BeautifulSoup fallback failed for {url}: {e}")
    return ""

def rerank_policy_bias(items):
    def score(it):
        t = it["title"]
        sc = sum(1 for k in Config.TITLE_BONUS if k in t) - sum(0.8 for k in Config.TITLE_PENALTY if k in t)
        return sc + it["pub_kst_dt"].timestamp() * 1e-12
    return sorted(items, key=score, reverse=True)

# ── Main Pipeline Logic ─────────────────────────────────────────────────────────
def process_article(item: dict, since: datetime, until: datetime) -> dict | None:
    try:
        link = normalize_news_url(item.get("originallink") or item.get("link", ""))
        outlet = outlet_from_url(link)
        html_cache = None
        effective_dt = item.get('pub_kst')

        if "news.naver.com" in host(link):
            press, can_link, page_dt, html_cache = resolve_naver_press_and_canonical(link)
            if press in Config.WHITELIST:
                outlet, link = press, can_link
                if page_dt: effective_dt = page_dt
        
        if outlet not in Config.WHITELIST: return None
        if not (since <= effective_dt <= until): return None

        html_text = html_cache or fetch_html(link)
        if not html_text: return None

        body = extract_text_from_html(link, html_text)
        if not body or not body_is_real_estate(body): return None

        summary = ai_summarize_gemini(body) if Config.USE_GEMINI else item["desc"]
        full_title = extract_full_title_from_html(html_text)

        return {
            "title": norm_title(full_title or item["title"]),
            "summary": clean(summary),
            "date": effective_dt.strftime("%Y-%m-%d"),
            "outlet": outlet,
            "link": link,
            "pub_kst_dt": effective_dt,
        }
    except Exception as e:
        logging.warning(f"기사 처리 실패 ({item.get('link')}): {e}")
        return None

def main():
    since = datetime.now(Config.KST).replace(hour=0,minute=0,second=0)
    until = datetime.now(Config.KST)

    candidate_pool = []
    seen_links = set()
    for q in Config.QUERY_TERMS:
        for item in naver_search(q):
            link = normalize_news_url(item.get("originallink") or item.get("link", ""))
            if not link or link in seen_links: continue
            
            title = norm_title(clean(item.get("title","")))
            if has_blacklist(title) or not has_core_in_title(title): continue

            try:
                item['pub_kst'] = dtparser.parse(item.get("pubDate","")).astimezone(Config.KST)
                candidate_pool.append(item)
                seen_links.add(link)
            except: continue

    if not candidate_pool:
        logging.info("수집된 기사가 없습니다.")
        return

    candidate_pool.sort(key=lambda x: x['pub_kst'], reverse=True)

    processed_articles = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_item = {executor.submit(process_article, item, since, until): item for item in candidate_pool[:50]}
        for future in as_completed(future_to_item):
            if processed := future.result():
                processed_articles.append(processed)

    processed_articles = rerank_policy_bias(processed_articles)
    
    # -----------------------------------------------------------------
    # (수정) 1차/2차 필터링 로직
    # -----------------------------------------------------------------
    target_count = 5
    max_per_outlet = 2 # filter_diverse_articles_by_keyword의 기본값과 동일하게 설정

    # --- 1차 필터링 (기존 로직) ---
    if Config.DEDUPE_METHOD == "GEMINI":
        results = filter_diverse_articles_by_gemini(processed_articles, target=target_count)
    else:
        results = filter_diverse_articles_by_keyword(processed_articles, target=target_count, max_per_outlet=max_per_outlet)
    
    # --- 2차 채우기 로직 (수정/추가) ---
    if len(results) < target_count:
        logging.info(f"1차 필터링 결과 {len(results)}건. {target_count}건을 채우기 위해 2차 필터링을 시작합니다.")
        
        # 1차 결과에서 이미 선택된 기사 링크와 요약문
        seen_links = {r['link'] for r in results}
        seen_summaries = {r['summary'] for r in results}
        
        # 1차 결과의 언론사 카운트 집계
        outlet_count = {}
        for r in results:
            outlet_count[r["outlet"]] = outlet_count.get(r["outlet"], 0) + 1

        # 1차에서 선택되지 않은 나머지 기사들로 2차 필터링
        for article in processed_articles:
            # 5개 다 채웠으면 즉시 종료
            if len(results) >= target_count:
                break
            
            # 1차 결과에 이미 포함된 기사면 건너뛰기
            if article['link'] in seen_links:
                continue
                
            # 2-1. 언론사별 최대 갯수 체크
            if outlet_count.get(article["outlet"], 0) >= max_per_outlet:
                continue
                
            # 2-2. 1차 결과와 내용 중복 체크 (키워드 기반)
            is_too_similar = any(are_summaries_similar_by_keyword(article['summary'], ex_summary) for ex_summary in seen_summaries)
            
            if not is_too_similar:
                results.append(article)
                seen_links.add(article['link']) # 2차에서 추가된 것도 기록
                seen_summaries.add(article['summary']) # 2차에서 추가된 것도 기록
                outlet_count[article["outlet"]] = outlet_count.get(article["outlet"], 0) + 1
                logging.info(f"2차 필터링으로 기사 추가: [{article['outlet']}] {article['title']}")
    # -----------------------------------------------------------------

    if not results:
        logging.info("최종 선별된 기사가 없습니다.")
        return

    # --- 결과물 생성 (Text 및 HTML) ---
    subject_line = f"📰 [{since.strftime('%Y-%m-%d')}] 부동산 뉴스 요약 ({len(results)}건)"
    
    # 1. 터미널 출력용 (Plain Text) - (기존과 동일)
    txt_out = [f"{subject_line} · {Config.VERSION}", "```text"]
    for i, r in enumerate(results, 1):
        txt_out.extend([f"{i}) 기사제목: {r['title']}", f"    본문 요약: {r['summary']}", f"    ({r['date']}, {r['outlet']})\n"])
    txt_out.extend(["```", "\n근거 링크"])
    for i, r in enumerate(results, 1):
        txt_out.append(f"- ({i}) {r['link']}")
    
    final_text_output = "\n".join(txt_out)
    print(final_text_output) # 터미널에 출력

    # 2. 이메일 발송용 (HTML) - (기존과 동일)
    
    # 스타일시트: 제목(h2) 색상 변경, 링크 섹션 스타일 추가
    styles = """
    <style>
        body { font-family: sans-serif; margin: 20px; }
        h1 { font-size: 1.3em; }
        h2 { font-size: 1.1em; margin-bottom: 5px; color: #000; } /* 링크 제거 후 검은색으로 */
        p { font-size: 0.95em; color: #333; margin-top: 5px; line-height: 1.5; }
        span { font-size: 0.85em; color: #777; }
        
        /* 기사 본문 아이템 */
        div.article-item { 
            border-bottom: 1px solid #eee; 
            padding-bottom: 15px; 
            margin-bottom: 15px; 
        }
        
        /* 하단 링크 섹션 */
        div.links-section {
            margin-top: 30px;
            padding-top: 15px;
        }
        div.links-section p {
            font-size: 0.9em;
            margin: 8px 0;
        }
        div.links-section a {
            color: #007bff; /* 링크 색상 */
            text-decoration: none;
        }
        div.links-section a:hover {
            text-decoration: underline;
        }
    </style>
    """
    
    html_out = [f"<html><head>{styles}</head><body>"]
    html_out.append(f"<h1>{subject_line}</h1>")
    
    # 기사 목록: <a> 태그 제거
    for r in results:
        html_out.append("<div class='article-item'>")
        html_out.append(f"<h2>[{r['outlet']}] {r['title']}</h2>") # <-- <a> 태그 제거
        html_out.append(f"<p>{r['summary']}</p>")
        html_out.append(f"<span>({r['date']})</span>")
        html_out.append("</div>")
    
    # 하단 기사 링크 섹션
    html_out.append("<div class='links-section'>")
    html_out.append("<p><b>기사 링크 :</b></p>")
    for i, r in enumerate(results, 1):
        # 링크를 번호와 함께 <a> 태그로 추가
        html_out.append(f"<p>{i}) <a href='{r['link']}' target='_blank'>{r['link']}</a></p>")
    html_out.append("</div>")
    
    html_out.append("</body></html>")
    final_html_output = "\n".join(html_out)

    # --- (유지) 이메일 발송 ---
    send_email_report(final_html_output, subject_line)
    
    # (삭제됨) 카카오톡 메시지 전송 코드 없음

if __name__ == "__main__":

    main()
