# collect_news.py
import os
import requests
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from anthropic import Anthropic
import json
from bs4 import BeautifulSoup

class RealEstateNewsCollector:
    def __init__(self):
        self.anthropic_client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        self.today = datetime.now().strftime('%Y년 %m월 %d일')
        self.yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y년 %m월 %d일')
        
    def search_web(self, query):
        """웹 검색 시뮬레이션 - 실제로는 주요 뉴스 사이트 크롤링"""
        news_sites = [
            'https://news.daum.net/estate',
            'https://www.asiae.co.kr/list/real-estate',
            'https://www.fnnews.com/section/002003000'
        ]
        
        news_data = []
        for site in news_sites:
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = requests.get(site, timeout=10, headers=headers)
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # 간단한 뉴스 제목 추출 로직
                titles = soup.find_all(['h1', 'h2', 'h3', 'h4'], limit=10)
                for title in titles:
                    text = title.get_text().strip()
                    if any(keyword in text for keyword in ['부동산', '아파트', '집값', '재건축', '분양']):
                        news_data.append({
                            'title': text,
                            'source': site,
                            'date': self.today
                        })
            except Exception as e:
                print(f"Error fetching {site}: {e}")
                
        return news_data
    
    def analyze_news_with_claude(self, news_data):
        """Claude API를 사용하여 뉴스 분석"""
        prompt = f"""
        다음은 {self.yesterday}부터 {self.today}까지의 부동산 관련 뉴스 데이터입니다:
        
        {json.dumps(news_data, ensure_ascii=False, indent=2)}
        
        이 데이터를 바탕으로 다음 기준에 맞춰 5개의 주요 부동산 뉴스를 선정하고 정리해주세요:
        
        1. 선호 매체: 매일경제, 한국경제, 서울경제, 조선일보, 중앙일보, 동아일보, 한겨레, 경향신문, 연합뉴스, 뉴시스, 조선비즈, 머니투데이, 파이낸셜뉴스, KBS, SBS, MBC
        2. 공통적으로 많이 언급된 주제별로 5개 선정 (겹치지 않게)
        3. 각각 다음 형식으로 정리:
           - 기사제목
           - 본문 요약 (3-4줄)
           - 날짜
           - 출처
        
        마크다운 형식으로 깔끔하게 정리해주세요.
        """
        
        try:
            response = self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text
        except Exception as e:
            print(f"Claude API error: {e}")
            return f"뉴스 분석 중 오류가 발생했습니다: {e}"
    
    def send_email(self, content):
        """이메일로 결과 전송"""
        try:
            msg = MIMEMultipart()
            msg['From'] = os.getenv('GMAIL_USERNAME')
            msg['To'] = os.getenv('RECIPIENT_EMAIL')
            msg['Subject'] = f"📊 일일 부동산 뉴스 브리핑 - {self.today}"
            
            body = f"""
안녕하세요!

{self.today} 부동산 뉴스 브리핑을 전달드립니다.

{content}

---
자동 생성된 뉴스 브리핑입니다.
문의사항이 있으시면 회신해주세요.
            """
            
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(os.getenv('GMAIL_USERNAME'), os.getenv('GMAIL_APP_PASSWORD'))
            server.send_message(msg)
            server.quit()
            
            print("이메일 전송 완료!")
            
        except Exception as e:
            print(f"이메일 전송 실패: {e}")
    
    def save_to_file(self, content):
        """결과를 파일로 저장"""
        with open('news_output.md', 'w', encoding='utf-8') as f:
            f.write(f"# 일일 부동산 뉴스 브리핑 - {self.today}\n\n")
            f.write(content)
    
    def run(self):
        """전체 프로세스 실행"""
        print(f"부동산 뉴스 수집 시작 - {self.today}")
        
        # 1. 웹에서 뉴스 데이터 수집
        print("뉴스 데이터 수집 중...")
        news_data = self.search_web("부동산 뉴스")
        
        # 2. Claude로 뉴스 분석 및 정리
        print("Claude로 뉴스 분석 중...")
        analyzed_content = self.analyze_news_with_claude(news_data)
        
        # 3. 파일로 저장
        self.save_to_file(analyzed_content)
        
        # 4. 이메일 전송
        print("이메일 전송 중...")
        self.send_email(analyzed_content)
        
        print("작업 완료!")

if __name__ == "__main__":
    collector = RealEstateNewsCollector()
    collector.run()
